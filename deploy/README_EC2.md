# EC2 配備手順（積算コード選定システム ＋ ハーネス/検図システム）

このリポジトリには**2つの独立アプリ**が入っている。部署・用途が違うので入口を分けている。

| アプリ | 用途 / 部署 | 起点 | ポート | ログイン | 公開名(例) |
|---|---|---|---|---|---|
| 積算コード選定システム | 見積・営業 | `app:app` | 8000 | `APP_PASSWORD` | estimate.furukawa-lab.com |
| ハーネス/検図システム | 製造・設計(ハーネス生成＋図面検図) | `wireharness.harness_app:app` | 8002 | `HARNESS_PASSWORD` | harness.furukawa-lab.com |

> **検図システムの統合**: かつての現場用・単独検図アプリ（`kenzu_app:app` / 8001）は**廃止**した。
> 図面検図は**ハーネスアプリに統合**済み（単独検図 `/check` ＋ ハーネス①の設計不備）。
> 既に 8001 で kenzu が常駐している場合は停止・無効化する:
> ```bash
> sudo systemctl disable --now kenzu-system
> sudo rm -f /etc/systemd/system/kenzu-system.service /etc/nginx/conf.d/kenzu.conf
> sudo systemctl daemon-reload && sudo nginx -t && sudo systemctl reload nginx
> ```
> 検図の蓄積学習(見逃しルール)がある場合は、`kenzu_data/defect_rules.json` を
> ハーネスの `HARNESS_STATE`(/var/lib/harness-system/state) へコピーすれば引き継げる。

- **別プロセス（別 gunicorn / 別 systemd サービス）**。片方を再起動しても他方は無停止。
- **別ログイン・別データ**。積算=`db.json`、ハーネス=`HARNESS_STATE`/`HARNESS_WORK`(/var/lib/harness-system)。
- **同じリポジトリ・同じ venv でよい**。検図/ハーネスのロジック `wireharness/` は共有。
- 同一 EC2 に同居でも、別 EC2 でもよい。

---

## A. 積算システム（app:app / 8000）
```bash
cd <アプリのディレクトリ>            # 例 /home/ec2-user/estimate-system
git pull --ff-only
./venv/bin/pip install -r requirements.txt
sudo tee /etc/estimate-system.env >/dev/null <<'EOF'
APP_PASSWORD=（積算ログイン）
APP_SECRET=（ランダム長文字列）
ANTHROPIC_API_KEY=（積算のVision用）
EOF
sudo chmod 600 /etc/estimate-system.env
sudo cp deploy/estimate-system.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now estimate-system
sudo cp deploy/nginx-estimate.conf /etc/nginx/conf.d/estimate.conf
sudo nginx -t && sudo systemctl reload nginx
curl -fsS http://127.0.0.1:8000/     # 動作確認
```

## B. ハーネス/検図システム（wireharness.harness_app:app / 8002）
手順の詳細は **`deploy/AWS_ハーネス_デプロイ手順.md`** を参照（永続化・社内限定/ID+PW・DNS/HTTPS・
検図統合・社内サーバ移行まで）。要点のみ:
```bash
cd <アプリのディレクトリ>
git pull --ff-only
./venv/bin/pip install -r wireharness/requirements.txt
sudo mkdir -p /var/lib/harness-system/work /var/lib/harness-system/state
sudo chown -R {APP_USER}:{APP_USER} /var/lib/harness-system
sudo cp deploy/harness-system.env.example /etc/harness-system.env   # HARNESS_WORK/STATE/USER/PASSWORD/SECRET
sudo chmod 600 /etc/harness-system.env
sudo cp deploy/harness-system.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now harness-system
sudo cp deploy/nginx-harness.conf /etc/nginx/conf.d/harness.conf    # {HARNESS_DOMAIN} 等を置換
sudo nginx -t && sudo systemctl reload nginx
curl -fsS http://127.0.0.1:8002/api/health    # {"status":"ok",...}
```

## C. 更新（再デプロイ）
```bash
cd <アプリのディレクトリ> && git pull --ff-only
./venv/bin/pip install -r requirements.txt -r wireharness/requirements.txt   # 依存変化時
sudo systemctl restart estimate-system harness-system
```
- ハーネスの学習結果・アップロードは `/var/lib/harness-system/` にあるため更新で保持される。

## メモ（EC2 特有）
- **アップロード上限**: ハーネスは大容量DXFに対応（app 80MB / nginx `client_max_body_size 60m`）。積算は45MB。
- **タイムアウト**: gunicorn `--timeout 300`、nginx `proxy_read_timeout 300s`（DXF解析・測長が長い）。
- **メモリ**: ezdxf 等でワーカが太る。t3.small 以上を推奨。
- **アクセス制御**: 評価は `HARNESS_PASSWORD`（ID+PW）。本運用で社内限定を強めるなら nginx allow/deny ＋
  セキュリティグループで社内IP/VPNのみ許可（併用可）。
- **AI補助(R6/SPD警報)**: 任意。`ANTHROPIC_API_KEY`/`GEMINI_API_KEY` 未設定なら R6 を飛ばして他は動く。
