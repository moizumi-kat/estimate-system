#!/bin/bash
# 自動デプロイ(systemdタイマー)を1回で設定するスクリプト。
# 使い方: bash ~/estimate_code_system_v1.4/setup_autodeploy.sh
# 以降、GitHub main に push されると5分以内に自動で pull + restart される(SSH作業不要)。
set -e
HOME_DIR=/home/ec2-user
APP_DIR="$HOME_DIR/estimate_code_system_v1.4"

# 1) 差分がある時だけ pull + 再起動するスクリプト
cat > "$HOME_DIR/autodeploy.sh" <<'EOS'
#!/bin/bash
cd /home/ec2-user/estimate_code_system_v1.4 || exit 0
git fetch origin main -q || exit 0
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  git reset --hard origin/main -q
  sudo systemctl restart estimate
fi
EOS
chmod +x "$HOME_DIR/autodeploy.sh"

# 2) systemd サービス(oneshot・ec2-userで実行)
sudo tee /etc/systemd/system/estimate-autodeploy.service >/dev/null <<'EOS'
[Unit]
Description=estimate auto-deploy from GitHub main
After=network-online.target
[Service]
Type=oneshot
User=ec2-user
ExecStart=/home/ec2-user/autodeploy.sh
EOS

# 3) systemd タイマー(5分ごと)
sudo tee /etc/systemd/system/estimate-autodeploy.timer >/dev/null <<'EOS'
[Unit]
Description=run estimate auto-deploy every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOS

sudo systemctl daemon-reload
sudo systemctl enable --now estimate-autodeploy.timer
echo "OK: 自動デプロイ(estimate-autodeploy.timer)を設定しました。今後は push だけで自動反映されます。"
sudo systemctl list-timers estimate-autodeploy.timer --no-pager || true
