#!/usr/bin/env bash
# 工場系(検図kenzu・端子terminal)を「テストサーバ」で一括起動する手順自動化スクリプト。
# 目的: スマホSSHでの1行ずつ貼り付けをやめ、1コマンドで検証できるようにする。
# 使い方(リポジトリ直下で、sudo権限のあるユーザで):
#   cd ~/estimate-system && bash deploy/setup-factory-test.sh
#
# ・テスト用の仮パスワード/仮鍵で環境ファイルを作成(既存ファイルは上書きしない=冪等)。
# ・ワーカー1(t3.micro=1GBでも軽く動く)。本番(社内サーバ)では2人目さんの実値・ワーカー3に。
# ・ハーネス(harness/8002)は2人目さんの別リポジトリのため本スクリプトの対象外。
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-$(id -un)}"
echo "== 工場系テスト起動 =="
echo "  APP_DIR=$APP_DIR  USER=$APP_USER"

# --- 検図(kenzu/8001) ---
if [ ! -f /etc/kenzu-system.env ]; then
  echo "-- /etc/kenzu-system.env を作成(テスト値)"
  printf 'KENZU_PASSWORD=test1234\nKENZU_SECRET=test-secret-change-later-0123456789\nKENZU_DATA_DIR=/var/lib/kenzu-system\n' | sudo tee /etc/kenzu-system.env >/dev/null
  sudo chmod 600 /etc/kenzu-system.env
else
  echo "-- /etc/kenzu-system.env は既存(そのまま使用)"
fi
sudo mkdir -p /var/lib/kenzu-system && sudo chown "$APP_USER:$APP_USER" /var/lib/kenzu-system
sudo sed -e "s#{APP_DIR}#$APP_DIR#g" -e "s#{APP_USER}#$APP_USER#g" -e 's/--workers 3/--workers 1/' \
  "$APP_DIR/deploy/kenzu-system.service" | sudo tee /etc/systemd/system/kenzu-system.service >/dev/null

# --- 端子(terminal/8100) ---
if [ ! -f /etc/terminal-system.env ]; then
  echo "-- /etc/terminal-system.env を作成(テスト値)"
  printf 'TERMINAL_USER=tanshi\nTERMINAL_PASSWORD=test1234\nTERMINAL_SECRET=test-secret-change-later-0123456789\nTERMINAL_WORK=/var/lib/terminal-system/work\nHARNESS_STATE=/var/lib/harness-system/state\n' | sudo tee /etc/terminal-system.env >/dev/null
  sudo chmod 600 /etc/terminal-system.env
else
  echo "-- /etc/terminal-system.env は既存(そのまま使用)"
fi
sudo mkdir -p /var/lib/terminal-system/work /var/lib/harness-system/state
sudo chown -R "$APP_USER:$APP_USER" /var/lib/terminal-system /var/lib/harness-system
sudo sed -e "s#{APP_DIR}#$APP_DIR#g" -e "s#{APP_USER}#$APP_USER#g" -e 's/--workers 3/--workers 1/' \
  "$APP_DIR/deploy/terminal-system.service" | sudo tee /etc/systemd/system/terminal-system.service >/dev/null

# --- 反映・起動 ---
sudo systemctl daemon-reload
sudo systemctl enable --now kenzu-system terminal-system

# --- 動作確認 ---
echo; echo "== ヘルスチェック =="
sleep 3
ok=0
for pair in "検図:8001" "端子:8100"; do
  name="${pair%%:*}"; port="${pair##*:}"
  st="$(systemctl is-active "$([ "$port" = 8001 ] && echo kenzu-system || echo terminal-system)" || true)"
  printf '%s (%s): service=%s  ' "$name" "$port" "$st"
  if curl -fsS "http://127.0.0.1:$port/api/health"; then echo "  -> OK"; ok=$((ok+1)); else echo "  -> 応答なし"; fi
done
echo
if [ "$ok" -eq 2 ]; then
  echo "✅ 検図・端子ともに起動テスト成功。"
else
  echo "⚠ 一部が未応答。ログ: sudo journalctl -u kenzu-system -u terminal-system --no-pager | tail -40"
fi
echo
echo "ハーネス(harness/8002)は2人目さんの別リポジトリ(harness_system)。同じ流れで別途セットアップしてください。"
echo "本番(社内サーバ)では: 環境ファイルを2人目さんの実値に差し替え、--workers を3へ(sedの '--workers 1' を外す/本スクリプトを使わず service をそのまま設置)。"
