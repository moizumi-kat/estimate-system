#!/usr/bin/env bash
# ハーネス/検図システムを EC2 に設置するワンショットスクリプト（相乗り可）。
# 役割: venv依存導入 → 永続領域作成 → systemd登録(8002) → nginx公開 → ヘルスチェック。
# 使い方(リポジトリ直下から、sudo権限のあるユーザで):
#   # 1) 先にログイン情報を作成(評価はID+パスワード):
#   sudo cp deploy/harness-system.env.example /etc/harness-system.env
#   sudo vi /etc/harness-system.env        # HARNESS_PASSWORD 等を設定。chmod 600。
#   sudo chmod 600 /etc/harness-system.env
#   # 2) 設置(ドメインは任意。nginxを使わないなら省略可):
#   HARNESS_DOMAIN=harness.furukawa-lab.com bash deploy/setup-harness.sh
set -euo pipefail

# --- 場所と実行ユーザの自動判定 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_USER="${SUDO_USER:-$(id -un)}"
VENV="$APP_DIR/venv"
PORT=8002
HARNESS_DOMAIN="${HARNESS_DOMAIN:-}"

echo "== ハーネス/検図システム 設置 =="
echo "  APP_DIR = $APP_DIR"
echo "  APP_USER= $APP_USER"
echo "  PORT    = $PORT"
echo "  DOMAIN  = ${HARNESS_DOMAIN:-(nginx設定はスキップ)}"

# --- 0) 前提確認 ---
command -v python3 >/dev/null || { echo "python3 がありません"; exit 1; }
[ -f /etc/harness-system.env ] || {
  echo "!! /etc/harness-system.env がありません。先に作成してください:"
  echo "   sudo cp deploy/harness-system.env.example /etc/harness-system.env && sudo vi /etc/harness-system.env"
  exit 1
}

# --- 1) venv と依存 ---
if [ ! -x "$VENV/bin/python" ]; then
  echo "-- venv 作成"
  python3 -m venv "$VENV"
fi
echo "-- 依存導入(wireharness/requirements.txt)"
"$VENV/bin/pip" install -q -U pip
"$VENV/bin/pip" install -q -r "$APP_DIR/wireharness/requirements.txt"

# --- 2) 永続領域 ---
echo "-- 永続領域 /var/lib/harness-system"
sudo mkdir -p /var/lib/harness-system/work /var/lib/harness-system/state
sudo chown -R "$APP_USER:$APP_USER" /var/lib/harness-system

# --- 3) systemd サービス(8002) ---
echo "-- systemd 登録 harness-system"
sudo sed -e "s#{APP_DIR}#$APP_DIR#g" -e "s#{APP_USER}#$APP_USER#g" \
  "$SCRIPT_DIR/harness-system.service" | sudo tee /etc/systemd/system/harness-system.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now harness-system
sleep 2
sudo systemctl --no-pager --full status harness-system | head -n 5 || true

# --- 4) nginx(任意・ドメイン指定時のみ) ---
if [ -n "$HARNESS_DOMAIN" ]; then
  if command -v nginx >/dev/null; then
    echo "-- nginx 公開 $HARNESS_DOMAIN"
    sudo sed -e "s#{HARNESS_DOMAIN}#$HARNESS_DOMAIN#g" \
      "$SCRIPT_DIR/nginx-harness.conf" | sudo tee /etc/nginx/conf.d/harness.conf >/dev/null
    sudo nginx -t && sudo systemctl reload nginx
  else
    echo "!! nginx が見つかりません。インストール後に deploy/nginx-harness.conf を手動設置してください。"
  fi
fi

# --- 5) ヘルスチェック ---
echo "-- ヘルスチェック(127.0.0.1:$PORT)"
for i in 1 2 3 4 5; do
  if curl -fsS "http://127.0.0.1:$PORT/api/health"; then echo; break; fi
  sleep 2
done

echo
echo "== 完了 =="
echo "  ローカル: curl http://127.0.0.1:$PORT/api/health"
[ -n "$HARNESS_DOMAIN" ] && echo "  公開(要DNS/HTTPS/SG): https://$HARNESS_DOMAIN/"
echo "  ログ: sudo journalctl -u harness-system -f   /   \$HARNESS_WORK/logs/app.log"
echo "  更新: cd $APP_DIR && git pull && \"$VENV/bin/pip\" install -r wireharness/requirements.txt && sudo systemctl restart harness-system"
