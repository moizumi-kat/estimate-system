#!/usr/bin/env bash
# 端子番号割付（2人目）システムを EC2 に設置するワンショットスクリプト（相乗り可）。
# 役割: venv依存導入 → 永続領域作成 → systemd登録(8100) → nginx公開 → ヘルスチェック。
# 既存の見積(8000)・検図(8001)・ハーネス(8002)と別プロセス/別ポート/別データなので影響しない。
# 使い方(リポジトリ直下から、sudo権限のあるユーザで):
#   # 1) 先にログイン情報を作成(評価はID+パスワード):
#   sudo cp deploy/terminal-system.env.example /etc/terminal-system.env
#   sudo vi /etc/terminal-system.env       # TERMINAL_PASSWORD / TERMINAL_SECRET を設定
#   sudo chmod 600 /etc/terminal-system.env
#   # 2) 設置(ドメインは任意。nginxを使わないなら省略可):
#   TERMINAL_DOMAIN=harness.furukawa-lab.com bash deploy/setup-terminal.sh
set -euo pipefail

# --- 場所と実行ユーザの自動判定 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_USER="${SUDO_USER:-$(id -un)}"
VENV="$APP_DIR/venv"
PORT=8100
TERMINAL_DOMAIN="${TERMINAL_DOMAIN:-}"

echo "== 端子番号割付（2人目）システム 設置 =="
echo "  APP_DIR = $APP_DIR"
echo "  APP_USER= $APP_USER"
echo "  PORT    = $PORT"
echo "  DOMAIN  = ${TERMINAL_DOMAIN:-(nginx設定はスキップ)}"

# --- 0) 前提確認 ---
command -v python3 >/dev/null || { echo "python3 がありません"; exit 1; }
[ -f /etc/terminal-system.env ] || {
  echo "!! /etc/terminal-system.env がありません。先に作成してください:"
  echo "   sudo cp deploy/terminal-system.env.example /etc/terminal-system.env && sudo vi /etc/terminal-system.env"
  exit 1
}

# --- 1) venv と依存(既存のvenvがあれば共用) ---
if [ ! -x "$VENV/bin/python" ]; then
  echo "-- venv 作成"
  python3 -m venv "$VENV"
fi
echo "-- 依存導入(wireharness/requirements.txt)"
"$VENV/bin/pip" install -q -U pip
"$VENV/bin/pip" install -q -r "$APP_DIR/wireharness/requirements.txt"

# --- 2) 永続領域 ---
echo "-- 永続領域 /var/lib/terminal-system/work"
sudo mkdir -p /var/lib/terminal-system/work
sudo chown -R "$APP_USER:$APP_USER" /var/lib/terminal-system
# 学習コーパスはハーネスと共有可(別ファイルに分離)。無ければ作成。
sudo mkdir -p /var/lib/harness-system/state
sudo chown -R "$APP_USER:$APP_USER" /var/lib/harness-system 2>/dev/null || true

# --- 3) systemd サービス(8100) ---
echo "-- systemd 登録 terminal-system"
sudo sed -e "s#{APP_DIR}#$APP_DIR#g" -e "s#{APP_USER}#$APP_USER#g" \
  "$SCRIPT_DIR/terminal-system.service" | sudo tee /etc/systemd/system/terminal-system.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now terminal-system
sleep 2
sudo systemctl --no-pager --full status terminal-system | head -n 5 || true

# --- 4) nginx(任意・ドメイン指定時のみ) ---
if [ -n "$TERMINAL_DOMAIN" ]; then
  if command -v nginx >/dev/null; then
    echo "-- nginx 公開 $TERMINAL_DOMAIN"
    sudo sed -e "s#{TERMINAL_DOMAIN}#$TERMINAL_DOMAIN#g" \
      "$SCRIPT_DIR/nginx-terminal.conf" | sudo tee /etc/nginx/conf.d/terminal.conf >/dev/null
    sudo nginx -t && sudo systemctl reload nginx
    echo "   HTTPS化(証明書): sudo certbot --nginx -d $TERMINAL_DOMAIN"
  else
    echo "!! nginx が見つかりません。インストール後に deploy/nginx-terminal.conf を手動設置してください。"
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
[ -n "$TERMINAL_DOMAIN" ] && echo "  公開(要DNS/HTTPS/SG): https://$TERMINAL_DOMAIN/"
echo "  ログ: sudo journalctl -u terminal-system -f"
echo "  更新: cd $APP_DIR && git pull && \"$VENV/bin/pip\" install -r wireharness/requirements.txt && sudo systemctl restart terminal-system"
