# 端子番号割付（2人目）システム — EC2 デプロイ手順

稼働中の EC2（estimate 等が動作中）へ **相乗り** で追加する。別プロセス・別ポート(8100)・別データなので既存アプリに影響しない。

## 画面フロー
設計図（DXF/DWG）を選択 → ロケーター生成・保存 → 端子番号割付（図面を見て確認・修正） → 保存（サーバ保存＋学習に蓄積） → 端子番号入り図面をPDF印刷。

## DWG対応（任意・現場がDWGを扱う場合）
アップロードで **DWG** を選ぶと、サーバ内で **DWG→DXF 自動変換**（外部送信なし）してから解析する。
変換器 `dwg2dxf`(LibreDWG) が必要。未導入だとDWGはエラー表示（DXFはそのまま使える）。
導入（EC2・1回だけ）:
```bash
cd {APP_DIR} && bash deploy/install-libredwg.sh && sudo systemctl restart terminal-system
```
（パッケージが無い環境ではソースから自動ビルド。導入後 `dwg2dxf --version` で確認。）

## 構成
- アプリ: `wireharness/terminal_app.py`（`wireharness.terminal_app:app`）
- ポート: `127.0.0.1:8100`（gunicorn）、nginx で公開
- データ: `TERMINAL_WORK`（アップロード/生成物/保存/ログ）、`HARNESS_STATE`（学習コーパス。ハーネスと共有可）

## 手順（EC2 上で）
```bash
# 1. 最新コードを取得（既存の配置ディレクトリ {APP_DIR} で）
cd {APP_DIR} && git pull

# 2. 依存は既存 venv を共用（flask/gunicorn/ezdxf/matplotlib/PyMuPDF は導入済みのはず）
#    不足時: {APP_DIR}/venv/bin/pip install -r wireharness/requirements.txt

# 3. 環境ファイル（ログインPW・保存先）
sudo cp deploy/terminal-system.env.example /etc/terminal-system.env
sudo chmod 600 /etc/terminal-system.env
sudo nano /etc/terminal-system.env      # TERMINAL_PASSWORD / TERMINAL_SECRET を設定
sudo mkdir -p /var/lib/terminal-system/work && sudo chown {APP_USER}: /var/lib/terminal-system/work

# 4. systemd サービス（{APP_DIR}/{APP_USER} を書き換えてから）
sudo cp deploy/terminal-system.service /etc/systemd/system/terminal-system.service
sudo nano /etc/systemd/system/terminal-system.service    # {APP_DIR}/{APP_USER}
sudo systemctl daemon-reload && sudo systemctl enable --now terminal-system
sudo systemctl status terminal-system      # active (running) を確認
curl -s http://127.0.0.1:8100/api/health   # {"ok":true}

# 5. nginx（ホスト名を設定）
sudo cp deploy/nginx-terminal.conf /etc/nginx/conf.d/terminal.conf
sudo nano /etc/nginx/conf.d/terminal.conf  # {TERMINAL_DOMAIN}=harness.furukawa-lab.com
sudo nginx -t && sudo systemctl reload nginx

# 6. DNS: harness.furukawa-lab.com の A レコードを EC2 のパブリックIPへ向ける
#    AWS セキュリティグループで 80/443 を許可（社内IP限定推奨）

# 7. HTTPS（Let's Encrypt 無料証明書）
sudo certbot --nginx -d harness.furukawa-lab.com
```

完了後、`https://harness.furukawa-lab.com` を開く → ログイン → 「新しい製番」から設計DXFを登録。

## 更新（新しいコードを反映）
```bash
cd {APP_DIR} && git pull && sudo systemctl restart terminal-system
```

## 運用データの確認（管理者）
- 保存物: `TERMINAL_WORK/<製番>/`（最終リスト・学習用CSV・PDF・meta.json）
- 学習の蓄積と採用: `{APP_DIR}` で
  ```bash
  HARNESS_STATE=/var/lib/harness-system/state venv/bin/python -m wireharness.fromto_qc.terminal_feedback status
  HARNESS_STATE=/var/lib/harness-system/state venv/bin/python -m wireharness.fromto_qc.terminal_feedback learn   # 複数製番一致の修正を採用
  ```
