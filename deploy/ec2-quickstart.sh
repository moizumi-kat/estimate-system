#!/usr/bin/env bash
# EC2 評価用クイックスタート（nginxなし・gunicornを直接 0.0.0.0:8002 で公開）。
# 端末の貼り付け二重化を避けるため、複雑な処理は全てこのスクリプトに封じ込める。
# 使い方(短く手入力):  bash deploy/ec2-quickstart.sh
# 事前に /etc/harness-system.env (HARNESS_USER/PASSWORD/SECRET/WORK/STATE) を作成済みであること。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_USER="${SUDO_USER:-$(id -un)}"
VENV="$APP_DIR/venv"
PORT=8002
SVC=/etc/systemd/system/harness-system.service

echo "== EC2 クイックスタート =="
echo "  APP_DIR=$APP_DIR / USER=$APP_USER / PORT=$PORT"

# 0) 前提
[ -f /etc/harness-system.env ] || { echo "!! /etc/harness-system.env がありません。先に作成してください。"; exit 1; }
[ -x "$VENV/bin/gunicorn" ] || { echo "!! venv/gunicorn がありません。先に venv 作成と pip install を実施してください。"; exit 1; }

# 1) スワップ(2GB)が無ければ作成（t3.micro のOOM対策）
if ! sudo swapon --show | grep -q .; then
  if [ ! -f /swapfile ]; then
    echo "-- スワップ2GBを作成"
    sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
  fi
  sudo swapon /swapfile || true
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# 2) 永続領域
sudo mkdir -p /var/lib/harness-system/work /var/lib/harness-system/state
sudo chown -R "$APP_USER:$APP_USER" /var/lib/harness-system

# 3) systemd サービスを“テンプレートから”生成（貼り付けに依存しない）
#    bind を 0.0.0.0 に、workers を 2 に、プレースホルダを実値へ。
sudo python3 - "$APP_DIR" "$APP_USER" "$SVC" <<'PY'
import sys
app_dir, app_user, out = sys.argv[1], sys.argv[2], sys.argv[3]
src = open(app_dir + '/deploy/harness-system.service').read()
src = (src.replace('{APP_DIR}', app_dir)
          .replace('{APP_USER}', app_user)
          .replace('127.0.0.1:8002', '0.0.0.0:8002')
          .replace('--workers 3', '--workers 2'))
open(out, 'w').write(src)
print('service written:', out)
PY

# 4) 起動
sudo systemctl daemon-reload
sudo systemctl enable --now harness-system
sleep 3
echo "-- 状態 --"
sudo systemctl is-active harness-system || true
echo "-- ヘルスチェック --"
for i in 1 2 3 4 5; do
  if curl -fsS "http://127.0.0.1:$PORT/api/health"; then echo; break; fi
  sleep 2
done
echo
echo "== 完了 =="
IP=$(curl -fsS http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo '<EC2のパブリックIP>')
echo "  次: セキュリティグループで TCP $PORT を開放 → ブラウザで http://$IP:$PORT/"
echo "  ログ: sudo journalctl -u harness-system -e"
