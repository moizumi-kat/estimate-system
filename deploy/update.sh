#!/usr/bin/env bash
# EC2 更新スクリプト: 最新コードを取り込み、依存を入れ、gunicorn を再起動する。
# 使い方(EC2内): cd {APP_DIR} && ./deploy/update.sh [ブランチ名(既定 main)]
# 前提: このディレクトリが git clone 済み、venv が {APP_DIR}/venv にある、
#        systemd サービス名が estimate-system。
set -euo pipefail

BRANCH="${1:-main}"
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

echo "==> [3/4] gunicorn 再起動"
sudo systemctl restart estimate-system
sleep 2

echo "==> [4/4] ヘルスチェック"
if curl -fsS http://127.0.0.1:8000/api/health; then
  echo; echo "OK: 稼働中。/kenzu で検図画面、/ で見積画面。"
else
  echo; echo "NG: 起動失敗。ログ: sudo journalctl -u estimate-system -n 50 --no-pager" >&2
  exit 1
fi
