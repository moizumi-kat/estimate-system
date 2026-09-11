#!/usr/bin/env bash
# EC2 更新スクリプト: 最新コードを取り込み、依存を入れ、対象サービスを再起動する。
# 使い方(EC2内):
#   cd {APP_DIR}
#   ./deploy/update.sh [ブランチ名(既定 main)] [サービス名...(既定: estimate-system kenzu-system)]
# 例:
#   ./deploy/update.sh main                       # 両方(積算＋検図)を更新
#   ./deploy/update.sh main kenzu-system          # 検図だけ再起動
# 前提: このディレクトリが git clone 済み、venv が {APP_DIR}/venv、systemd 常駐。
set -euo pipefail

BRANCH="${1:-main}"
shift || true
SERVICES=("$@")
if [ ${#SERVICES[@]} -eq 0 ]; then
  SERVICES=(estimate-system kenzu-system)   # 既定で両アプリ
fi
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

echo "==> [1/4] git 取り込み ($BRANCH)"
git fetch --prune origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "==> [2/4] 依存インストール (venv)"
if [ ! -d venv ]; then python3 -m venv venv; fi
./venv/bin/pip install --upgrade pip >/dev/null
./venv/bin/pip install -r requirements.txt

echo "==> [3/4] サービス再起動: ${SERVICES[*]}"
for s in "${SERVICES[@]}"; do
  if systemctl list-unit-files | grep -q "^${s}.service"; then
    sudo systemctl restart "$s"
  else
    echo "  (skip) ${s}.service 未登録"
  fi
done
sleep 2

echo "==> [4/4] ヘルスチェック"
ok=1
for pair in "estimate-system:8000:/" "kenzu-system:8001:/kenzu用"; do
  s="${pair%%:*}"; rest="${pair#*:}"; port="${rest%%:*}"
  systemctl list-unit-files | grep -q "^${s}.service" || continue
  if curl -fsS "http://127.0.0.1:${port}/api/health" >/dev/null; then
    echo "  OK  ${s} (127.0.0.1:${port})"
  else
    echo "  NG  ${s} (127.0.0.1:${port}) → sudo journalctl -u ${s} -n 50 --no-pager" >&2
    ok=0
  fi
done
[ "$ok" = 1 ] || exit 1
echo "完了: 積算=/(8000) / 検図=別ホスト(8001)。"
