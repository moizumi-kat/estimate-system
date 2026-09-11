# EC2 配備手順（見積システム ＋ 図面検図）

検図（`/kenzu`・`/api/kenzu`）は見積システムと**同じ `app.py`** に統合済み。
そのため EC2 で見積が動いていれば、**同じプロセス（gunicorn）でそのまま検図も動く**。
新しいサーバは不要で、やることは「コード更新 → 依存追加 → 再起動」だけ。

---

## A. すでに見積システムが EC2 で動いている場合（＝今回の更新）

一番簡単なのは同梱の更新スクリプト。EC2 に SSH して:

```bash
cd <アプリのディレクトリ>          # 例 /home/ec2-user/estimate-system
./deploy/update.sh main            # 本番ブランチ名（既定 main）
```

`update.sh` が (1) git pull → (2) `pip install -r requirements.txt`
（今回追加の **matplotlib** もここで入る）→ (3) gunicorn 再起動 →
(4) `/api/health` 確認、まで自動でやる。完了後:

- 見積: `https://<ドメイン>/`
- 検図: `https://<ドメイン>/kenzu`（トップ右上「図面検図 →」からも）

> 手動でやる場合:
> ```bash
> cd <アプリのディレクトリ>
> git pull --ff-only origin main
> ./venv/bin/pip install -r requirements.txt   # matplotlib 追加分
> sudo systemctl restart estimate-system        # サービス名は環境に合わせる
> curl -fsS http://127.0.0.1:8000/api/health
> ```
> systemd を使わず nohup/tmux で gunicorn を起こしている場合は、その gunicorn を
> 落として同じコマンドで起動し直す（下の gunicorn 例を参照）。

### 反映すべきブランチについて
今回の検図の変更はブランチ `claude/wire-harness-software-2322ld` にある。
本番が `main` から配備されているなら、**このブランチを main にマージ**してから
`update.sh main` を実行する（PR の作成が必要なら指示ください）。

---

## B. まだ常駐化していない／作り直す場合（EC2 初期設定）

Amazon Linux 2023 / Ubuntu どちらでも手順は同じ（パッケージ名だけ読み替え）。

```bash
# 1) 取得と venv
sudo yum install -y git python3 nginx      # Ubuntu: sudo apt install -y git python3-venv nginx
git clone https://github.com/moizumi-kat/estimate-system.git
cd estimate-system
python3 -m venv venv
./venv/bin/pip install -r requirements.txt   # flask/gunicorn/ezdxf/matplotlib など

# 2) 環境変数ファイル（平文をリポジトリに置かない）
sudo tee /etc/estimate-system.env >/dev/null <<'EOF'
APP_PASSWORD=（画面ログイン用パスワード）
APP_SECRET=（ランダムな長い文字列）
ANTHROPIC_API_KEY=（Vision/AI補助を使う場合のみ。無ければ省略可）
EOF
sudo chmod 600 /etc/estimate-system.env

# 3) systemd 常駐化（deploy/estimate-system.service の {APP_DIR}/{APP_USER} を書換え）
sudo cp deploy/estimate-system.service /etc/systemd/system/estimate-system.service
sudo systemctl daemon-reload
sudo systemctl enable --now estimate-system
curl -fsS http://127.0.0.1:8000/api/health

# 4) nginx リバースプロキシ（deploy/nginx-estimate.conf の {DOMAIN} を書換え）
sudo cp deploy/nginx-estimate.conf /etc/nginx/conf.d/estimate.conf
sudo nginx -t && sudo systemctl enable --now nginx
# HTTPS: sudo certbot --nginx -d <ドメイン>   もしくは ALB+ACM で終端
```

gunicorn を直接起こす場合の最小例（systemd を使わないとき）:
```bash
MPLBACKEND=Agg MPLCONFIGDIR=$PWD/.mplcache \
./venv/bin/gunicorn app:app --workers 3 --timeout 300 --bind 127.0.0.1:8000
```

---

## 検図まわりの注意（EC2 特有）

- **依存**: 検図の主軸(R1-R5,R7＋H1-H5)は `ezdxf` のみで動く。R6(SPD警報のAI補助)で
  `matplotlib` を使い領域画像を描く→ `requirements.txt` に追加済み。matplotlib は
  headless 用に `Agg` を強制済み。systemd 配下ではキャッシュ書込先が要るので
  ユニットで `MPLCONFIGDIR` を指定している。
- **APIキー**: R6 を使うときだけ `ANTHROPIC_API_KEY`（無ければ `GEMINI_API_KEY`）が要る。
  未設定なら R6 を飛ばして他ルールは動く。見積の Vision 抽出と同じキーを共用してよい。
- **アップロード上限**: DXF/ZIP のため app 側 40MB。nginx は `client_max_body_size 45m`。
- **タイムアウト**: 検図(特にR6)や見積のVisionは秒〜十数秒かかる。gunicorn `--timeout 300`、
  nginx `proxy_read_timeout 300s` を設定済み。
- **メモリ**: matplotlib＋ezdxf のワーカが太る。t3.small 以上を推奨。ワーカ数は vCPU に合わせる。
- **認証**: `APP_PASSWORD` を設定すれば `/kenzu` も含め全画面がログイン必須になる
  （API は 401）。社外に出す場合は必ず設定する。

## 動作確認

```bash
# 見積 → コード選定画面
curl -fsS https://<ドメイン>/api/health
# 検図画面
open https://<ドメイン>/kenzu
# 検図API（DXF/ZIP を multipart で送る。系統は自動判定）
curl -s -F "file=@シーケンス.dxf" -F "file=@内部配置図.dxf" https://<ドメイン>/api/kenzu
```
