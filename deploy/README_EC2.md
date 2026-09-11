# EC2 配備手順（積算コード選定システム ＋ 図面検図システム）

このリポジトリには**2つの独立アプリ**が入っている。部署が違うので入口を分けている。

| アプリ | 用途 / 部署 | 起点 | ポート | ログイン | 公開名(例) |
|---|---|---|---|---|---|
| 積算コード選定システム | 見積・営業 | `app:app` | 8000 | `APP_PASSWORD` | estimate.example.co.jp |
| 図面検図システム | 図面QC・工場現場 | `kenzu_app:app` | 8001 | `KENZU_PASSWORD` | kenzu.example.co.jp |

- **別プロセス（別 gunicorn / 別 systemd サービス）**なので片方を再起動しても他方は無停止。
- **別ログイン**（環境変数が別）。現場ユーザに営業用画面は見えない。
- **別APIキー**: 検図は `KENZU_ANTHROPIC_API_KEY`（無ければ `KENZU_GEMINI_API_KEY`）を使う。
  検図プロセス内でだけ有効化されるので、積算のキーとは混ざらない（課金・レート枠も分離）。
- **別データ**: 検図の実行結果・フィードバックは `KENZU_DATA_DIR`（既定 `<repo>/kenzu_data`、
  本番は `/var/lib/kenzu-system` 推奨）に保存。積算の `db.json` とは完全に別。
- **同じリポジトリ・同じ venv**。検図ロジック `wireharness/fromto_qc/` は共有。
- 同一 EC2 の別ポートで同居させても、別 EC2 に分けてもよい（下記どちらも可）。

### 検図のフィードバック（バージョンアップの土台）
検図画面で各指摘に **是正/誤検知** を、ツールが出せなかった不具合は **見逃し** を記録できる。
集計（`/api/stats`）はルール別に是正/誤検知/見逃し件数と適合率を返し、
「誤検知が多い→チューニング」「見逃し→新ルール」の判断に使う。データは上記の別領域に蓄積。

---

## A. すでに積算システムが EC2 で動いている場合（＝検図を“追加”する）

### A-1. 検図サービスを新規に立てる（初回のみ）

```bash
cd <アプリのディレクトリ>            # 例 /home/ec2-user/estimate-system
git pull --ff-only origin main       # kenzu_app.py 等を取り込む
./venv/bin/pip install -r requirements.txt   # matplotlib 追加分

# 現場用の環境変数（積算とは別ファイル・別パスワード・別キー・別データ）
sudo tee /etc/kenzu-system.env >/dev/null <<'EOF'
KENZU_PASSWORD=（現場用ログインパスワード）
KENZU_SECRET=（ランダムな長い文字列）
KENZU_ANTHROPIC_API_KEY=（R6のAI補助用。積算のキーとは別。無ければ省略可→R6スキップ）
KENZU_DATA_DIR=/var/lib/kenzu-system
EOF
sudo chmod 600 /etc/kenzu-system.env
# フィードバック等の保存先（積算db.jsonとは別。永続領域）を作成
sudo mkdir -p /var/lib/kenzu-system && sudo chown {APP_USER}:{APP_USER} /var/lib/kenzu-system

# systemd 常駐（{APP_DIR}/{APP_USER} を書き換えてからコピー）
sudo cp deploy/kenzu-system.service /etc/systemd/system/kenzu-system.service
sudo systemctl daemon-reload
sudo systemctl enable --now kenzu-system
curl -fsS http://127.0.0.1:8001/api/health      # {"app":"kenzu",...ok:true}

# nginx を別ホスト名で（{KENZU_DOMAIN} を書き換え）
sudo cp deploy/nginx-kenzu.conf /etc/nginx/conf.d/kenzu.conf
sudo nginx -t && sudo systemctl reload nginx
# HTTPS: sudo certbot --nginx -d <KENZU_DOMAIN>
```

→ 現場は `https://<KENZU_DOMAIN>/` で検図画面。営業の積算はこれまで通り別アドレス。

### A-2. 以後のコード更新（両アプリまとめて）

```bash
cd <アプリのディレクトリ>
./deploy/update.sh main                    # 積算＋検図の両方を再起動
# 検図だけ更新したいとき:
./deploy/update.sh main kenzu-system
```

`update.sh` が git pull →`pip install`→ 登録済みサービスを再起動 → 各 `/api/health` 確認まで行う。

---

## B. まだ常駐化していない／新規 EC2 に両方構築する場合

Amazon Linux 2023 / Ubuntu 共通（パッケージ名だけ読み替え）。

```bash
sudo yum install -y git python3 nginx      # Ubuntu: sudo apt install -y git python3-venv nginx
git clone https://github.com/moizumi-kat/estimate-system.git
cd estimate-system
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 環境変数（2アプリ分。平文はリポジトリに置かない）
sudo tee /etc/estimate-system.env >/dev/null <<'EOF'
APP_PASSWORD=（積算ログイン）
APP_SECRET=（ランダム長文字列）
ANTHROPIC_API_KEY=（積算のVision用）
EOF
sudo tee /etc/kenzu-system.env >/dev/null <<'EOF'
KENZU_PASSWORD=（検図ログイン）
KENZU_SECRET=（ランダム長文字列）
KENZU_ANTHROPIC_API_KEY=（検図R6用。積算とは別キー。任意）
KENZU_DATA_DIR=/var/lib/kenzu-system
EOF
sudo chmod 600 /etc/estimate-system.env /etc/kenzu-system.env
sudo mkdir -p /var/lib/kenzu-system && sudo chown {APP_USER}:{APP_USER} /var/lib/kenzu-system

# systemd（各 .service の {APP_DIR}/{APP_USER} を書き換え）
sudo cp deploy/estimate-system.service /etc/systemd/system/
sudo cp deploy/kenzu-system.service    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now estimate-system kenzu-system

# nginx（2ホスト名。各 {DOMAIN}/{KENZU_DOMAIN} を書き換え）
sudo cp deploy/nginx-estimate.conf /etc/nginx/conf.d/estimate.conf
sudo cp deploy/nginx-kenzu.conf    /etc/nginx/conf.d/kenzu.conf
sudo nginx -t && sudo systemctl enable --now nginx
```

> **別 EC2 に分ける構成**にする場合は、検図用インスタンスで同じ clone を置き、
> `kenzu-system.service`＋`nginx-kenzu.conf` だけを設定する（積算側は不要）。
> コードは同じでも、起動するのは `kenzu_app:app` だけ。

gunicorn を直接起こす最小例（systemd を使わないとき）:
```bash
# 検図
KENZU_PASSWORD=... MPLBACKEND=Agg MPLCONFIGDIR=$PWD/.mplcache \
./venv/bin/gunicorn kenzu_app:app --workers 3 --timeout 300 --bind 127.0.0.1:8001
```

---

## 検図まわりの注意（EC2 特有）

- **依存**: 検図の主軸(R1-R5,R7＋H1-H5)は `ezdxf` のみで動く。R6(SPD警報のAI補助)で
  `matplotlib` を使う→ `requirements.txt` に追加済み・`Agg` 強制済み。systemd 配下では
  キャッシュ書込先が要るのでユニットで `MPLCONFIGDIR` を指定。
- **APIキー**: R6 のときだけ `ANTHROPIC_API_KEY`（無ければ `GEMINI_API_KEY`）。未設定なら
  R6 を飛ばして他ルールは動く。積算とキーを共用してもよいし、検図用を別に持ってもよい。
- **アップロード上限**: DXF/ZIP のため検図 app 側 45MB。nginx `client_max_body_size 45m`。
- **タイムアウト**: gunicorn `--timeout 300`、nginx `proxy_read_timeout 300s`。
- **メモリ**: matplotlib＋ezdxf でワーカが太る。t3.small 以上を推奨。
- **認証**: `KENZU_PASSWORD` 未設定だと検図は認証オフ（社内LAN/検証のみ）。社外公開時は必ず設定。

## 動作確認

```bash
curl -fsS http://127.0.0.1:8001/api/health                 # {"app":"kenzu",...}
# DXF/ZIP を multipart で送る（系統は自動判定）
curl -s -F "file=@シーケンス.dxf" -F "file=@内部配置図.dxf" \
     http://127.0.0.1:8001/api/kenzu
```
