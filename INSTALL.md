# インストール手順（積算コード選定システム）

新しいサーバ／PCへ本システムを新規導入する手順です。既存 EC2 への「更新反映」は `DEPLOY.md` を参照してください。

- 構成: Python 製の Web アプリ（Flask）。ブラウザで図面(PDF/PNG/JPG/DXF/ZIP)をアップ→抽出→コード選定→Excel出力。
- 抽出は **二重Vision（Claude ＋ Gemini 2.5 Pro）**。選定は DB 照合＋ルール（約1秒・決定的）。
- 正本は GitHub（`moizumi-kat/estimate-system`）。本番 EC2 は下流（GitHub から受け取るのみ）。

---

## 0. 前提（用意するもの）

| 項目 | 内容 |
|---|---|
| OS | Linux / macOS（本番は Amazon Linux 等の EC2） |
| Python | **3.11 以上**（`python3 --version` で確認） |
| Anthropic APIキー | Claude 用（`sk-ant-...`） |
| Google AI Studio APIキー | Gemini 用（新形式 `AQ.` で始まる）。二重Visionに使用 |
| ネット | api.anthropic.com / generativelanguage.googleapis.com へ疎通 |

> APIキーは**リポジトリに絶対に入れない**（`.gitignore` 済み）。環境変数かキーファイルで渡す。

---

## 1. コードの取得

```bash
git clone https://github.com/moizumi-kat/estimate-system.git
cd estimate-system
```

（本番運用中の反映は `DEPLOY.md`。ここでは新規導入を説明。）

## 2. Python 仮想環境と依存パッケージ

```bash
python3 -m venv venv
source venv/bin/activate            # Windows は venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` の主な中身: flask / anthropic / google-genai / pymupdf / openpyxl（＋任意で gunicorn, ezdxf）。

## 3. APIキーの設定

**Claude（必須）** と **Gemini（二重Visionに必要）** の2つ。どちらか方式を選ぶ。

### 方式A: 環境変数（推奨・本番向け）
```bash
export ANTHROPIC_API_KEY="sk-ant-xxxxxxxx"
export GEMINI_API_KEY="AQ.xxxxxxxx"
```

### 方式B: キーファイル（リポジトリ直下・gitignore済）
```bash
printf '%s' "AQ.xxxxxxxx" > .gemini_key        # Gemini。改行を入れない
# Claude は環境変数 ANTHROPIC_API_KEY で渡す（.akey を使う運用なら export ANTHROPIC_API_KEY=$(cat .akey)）
```

> **セキュリティ（重要）**: 本番運用では Gemini キーは**失効させず常設**（社員が随時使うため）。キーは環境変数かリポジトリ外ファイルで管理し、画面・ログ・チャットに出さない。漏れたら必ず失効・再発行。

## 4. 動作設定（環境変数）

| 変数 | 既定 | 説明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | （必須） | Claude APIキー |
| `GEMINI_API_KEY` または `.gemini_key` | 任意 | Gemini APIキー。無ければ**自動でClaude単独**に切替（システムは止まらない） |
| `APP_PASSWORD` | 空=認証オフ | 社員ログイン用パスワード。**本番では必ず設定** |
| `PORT` | 8000 | 待受ポート |
| `DUAL_VISION` | 1（有効） | `0` にすると二重Vision無効＝Claude単独（コスト/速度優先時） |
| `APP_SECRET` | 自動 | セッション署名鍵（設定推奨・後述） |

## 5. 起動（開発・動作確認）

```bash
source venv/bin/activate
export ANTHROPIC_API_KEY="sk-ant-..."
export GEMINI_API_KEY="AQ...."          # 二重Visionを使う場合
export APP_PASSWORD="社内で決めたパスワード"
python app.py
```

- 起動ログに `積算コード選定システム起動: http://localhost:8000 (DB 3641件 / APIキー OK)` が出る。
- ブラウザで `http://localhost:8000` → ログイン → 図面をアップして抽出→選定を確認。

### 疎通確認
```bash
curl -s localhost:8000/api/health      # {"ok":true,"db":3641,...}
```

## 6. 本番運用（EC2・systemd 常駐）

本番は gunicorn ＋ systemd で常駐。サービス名は既存に合わせ `estimate`。

### 6-1. gunicorn で起動確認
```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:8000 --timeout 600 app:app
```
> 二重Vision は Gemini が1枚あたり数分かかることがあるため **`--timeout 600`（10分）以上**を推奨。

### 6-2. systemd サービス（例: `/etc/systemd/system/estimate.service`）
```ini
[Unit]
Description=Estimate Code Selection System
After=network.target

[Service]
User=ec2-user
WorkingDirectory=/home/ec2-user/estimate-system
Environment=ANTHROPIC_API_KEY=sk-ant-xxxx
Environment=GEMINI_API_KEY=AQ.xxxx
Environment=APP_PASSWORD=xxxx
Environment=APP_SECRET=（openssl rand -hex 32 で生成した値）
Environment=DUAL_VISION=1
ExecStart=/home/ec2-user/estimate-system/venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 --timeout 600 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now estimate
sudo systemctl status estimate --no-pager | head
```

### 6-3. リバースプロキシ（任意・HTTPS/社内公開）
nginx 等で 443→127.0.0.1:8000 に転送。アップロードサイズ制限（`client_max_body_size 50m;`）と
タイムアウト（`proxy_read_timeout 600s;`）を大きめに。社内限定なら IP 制限も併用。

## 7. 使い方（社員向けの基本フロー）

1. ログイン → 図面（PDF等）をアップ → **[抽出]**。
   - 抽出結果に「🔍 Claude+Gemini（二重Vision）」と突合サマリ、各機器に **✓✓（二重検証）／＋G（Geminiのみ＝取りこぼし候補・要確認）** が付く。
2. 配電盤セット盤は**仕様確認ゲート**（計器種別/VCB/操作方式）に回答。
3. **[選定]** → ◎/○/△ でコード提示。盤ごとに付属品ゲート（換気扇/照明等）を調整。
4. **Excel 出力** → 「確認」列で 未確認/OK/要修正 を記入し、違う場合は正しいコードとコメントを追記。
   - この**フィードバック Excel が事例データ**。集めて精度改善に使う。

## 8. トラブルシューティング

| 症状 | 対処 |
|---|---|
| `ANTHROPIC_API_KEY 未設定` | 環境変数を設定して再起動 |
| 抽出が「Claude」だけで二重Visionにならない | Gemini キー未設定/疎通不可、または `DUAL_VISION=0`。キーと `generativelanguage.googleapis.com` 疎通を確認 |
| 抽出が遅い/タイムアウト | Gemini 2.5 Pro は1枚数分。gunicorn/nginx の timeout を600s以上に。急ぐ時は `DUAL_VISION=0` |
| `no space left on device` | 一時ファイル/バックアップを削除。再起動で `_EXTRACT_CACHE` はクリアされる |
| ログイン画面が出ない | `APP_PASSWORD` 未設定（認証オフ）。本番では設定する |

## 9. 更新（2回目以降）

導入後の更新は **`DEPLOY.md`（GitHub 正本 → EC2）** を参照。要点は
`git pull` → 構文チェック → `sudo systemctl restart estimate` → `/api/health` 確認 → ロールバック用に事前バックアップ。
