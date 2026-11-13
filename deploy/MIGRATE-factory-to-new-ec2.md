# 工場系システム（検図・端子・ハーネス）を新しいEC2へ移す手順書

対象：**工場系3システムを新サーバへ移し、現サーバは見積（営業）専用にする**
（方針B。見積＝現サーバ据え置き・無変更。工場系の移行は主に「2人目」担当）

| システム | 用途/部署 | 起点 | ポート | クローン元 | 環境ファイル | データ領域 |
|---|---|---|---|---|---|---|
| 検図（kenzu） | 工場・図面QC | `kenzu_app:app` | 8001 | estimate-system | `/etc/kenzu-system.env` | `/var/lib/kenzu-system` |
| 端子番号割付（terminal） | 工場 | `wireharness.terminal_app:app` | 8100 | estimate-system | `/etc/terminal-system.env` | `/var/lib/terminal-system`（＋`/var/lib/harness-system/state`） |
| ハーネス（harness） | 工場 | `wireharness.harness_app:app` | 8002 | **harness_system（別クローン）** | 2人目設定 | `/var/lib/harness-system` |

> ⚠ 見積（estimate・8000）は現サーバに残すので、**この手順では触りません**。
> ⚠ 環境変数の中身（パスワード・APIキー）は**2人目さんが把握している値**を使ってください（本書には値を書きません）。

---

## 0. 事前準備（現サーバで確認・バックアップ）

現サーバ（`mitsumori-server`）で、移すものを確認します。

```bash
# 工場系サービスの実名を確認
systemctl list-units --type=service | grep -Ei 'kenzu|terminal|harness'

# それぞれの環境ファイル・データ領域の存在を確認
ls -l /etc/kenzu-system.env /etc/terminal-system.env /etc/harness-system.env 2>/dev/null
ls -ld /var/lib/kenzu-system /var/lib/terminal-system /var/lib/harness-system 2>/dev/null

# ハーネスの別クローン場所を確認（起点ディレクトリ）
systemctl cat harness-system 2>/dev/null | grep -E 'WorkingDirectory|ExecStart'
```

> 💡 **データ（検図のフィードバック・端子/ハーネスの学習コーパス等）は必ず新サーバへコピー**します（手順4）。失うと蓄積が消えます。先にEBSスナップショットを取っておくと安心です。

---

## 1. 新しいEC2インスタンスを起動

- **リージョン**：現サーバと同じ（ap-northeast-1 / 東京）。
- **AMI**：Amazon Linux 2023（現サーバと同系。Ubuntuでも可・パッケージ名読み替え）。
- **タイプ**：**t3.small（2GB）〜 t3.medium（4GB）**。検図はmatplotlib＋ezdxfでワーカが太る・3システム同居なので **t3.medium 推奨**。
- **ストレージ**：16GiB以上（既定8GiBだと手狭）。
- **セキュリティグループ**：22(SSH・自分のIPのみ)、80/443（できれば社内IP/VPNのみ）。
- **キーペア**：SSH用（既存の運用鍵、または新規）。
- 起動後、**Elastic IP を新規に取得して割り当て**（固定IP。工場のDNSはこれを向ける）。

---

## 2. 新サーバの初期セットアップ（SSHで実施）

```bash
# 依存
sudo yum install -y git python3 nginx   # Ubuntu: sudo apt install -y git python3-venv nginx

# 見積リポジトリ（検図・端子が入っている）をclone
cd ~
git clone https://github.com/moizumi-kat/estimate-system.git
cd estimate-system
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt            # 検図(kenzu)用
./venv/bin/pip install -r wireharness/requirements.txt # 端子(terminal)用

# ハーネス（別リポジトリ）も新サーバにclone（URLは2人目さんが把握）
cd ~
git clone <ハーネスのリポジトリURL> harness_system
cd harness_system
python3 -m venv venv
./venv/bin/pip install -U pip -r requirements.txt     # ハーネスの依存（構成に合わせて）
```

---

## 3. 環境変数ファイルを作成（2人目さんの値で）

値はチャット等に出さず、各サーバ上で直接入力してください。

```bash
# 検図
sudo tee /etc/kenzu-system.env >/dev/null <<'EOF'
KENZU_PASSWORD=（検図ログイン）
KENZU_SECRET=（ランダムな長い文字列）
KENZU_ANTHROPIC_API_KEY=（検図R6用・任意／積算とは別キー）
KENZU_DATA_DIR=/var/lib/kenzu-system
EOF
sudo chmod 600 /etc/kenzu-system.env

# 端子
sudo cp ~/estimate-system/deploy/terminal-system.env.example /etc/terminal-system.env
sudo vi /etc/terminal-system.env   # TERMINAL_PASSWORD / TERMINAL_SECRET / 各PATH を設定
sudo chmod 600 /etc/terminal-system.env

# ハーネス（2人目さんの構成に合わせて）
sudo vi /etc/harness-system.env    # HARNESS_* を設定
sudo chmod 600 /etc/harness-system.env
```

---

## 4. 既存データを現サーバから新サーバへコピー（重要）

検図のフィードバック・端子/ハーネスの学習コーパスを引き継ぎます。**現サーバで該当サービスを止める前に**コピーしてOK（読み取りのみ）。

```bash
# 新サーバ上で、現サーバ(18.178.195.90)からrsync（SSH鍵が要る）
sudo mkdir -p /var/lib/kenzu-system /var/lib/terminal-system /var/lib/harness-system
sudo rsync -avz -e "ssh -i ~/鍵.pem" \
  ec2-user@18.178.195.90:/var/lib/kenzu-system/     /var/lib/kenzu-system/
sudo rsync -avz -e "ssh -i ~/鍵.pem" \
  ec2-user@18.178.195.90:/var/lib/terminal-system/  /var/lib/terminal-system/
sudo rsync -avz -e "ssh -i ~/鍵.pem" \
  ec2-user@18.178.195.90:/var/lib/harness-system/   /var/lib/harness-system/
# 所有者を実行ユーザに
sudo chown -R ec2-user:ec2-user /var/lib/kenzu-system /var/lib/terminal-system /var/lib/harness-system
```

> SSH直結が難しければ、現サーバで `tar czf` → S3 or SFTP経由でもOK。

---

## 5. systemd サービス登録（新サーバ）

```bash
cd ~/estimate-system

# 検図（{APP_DIR}=/home/ec2-user/estimate-system, {APP_USER}=ec2-user に置換）
sudo sed -e 's#{APP_DIR}#/home/ec2-user/estimate-system#g' -e 's#{APP_USER}#ec2-user#g' \
  deploy/kenzu-system.service | sudo tee /etc/systemd/system/kenzu-system.service >/dev/null

# 端子（ワンショットスクリプトが systemd 登録まで実施）
bash deploy/setup-terminal.sh          # DOMAIN無しなら nginx はスキップ（手順6で設定）

# ハーネス（2人目さんの harness-system.service を同様に設置）
#   sudo cp ~/harness_system/deploy/harness-system.service /etc/systemd/system/ 等

sudo systemctl daemon-reload
sudo systemctl enable --now kenzu-system terminal-system harness-system

# 稼働確認（ローカル）
curl -fsS http://127.0.0.1:8001/api/health   # 検図
curl -fsS http://127.0.0.1:8100/api/health   # 端子
curl -fsS http://127.0.0.1:8002/api/health   # ハーネス
```

---

## 6. nginx（サブドメイン公開）

```bash
cd ~/estimate-system
# 検図
sudo sed 's#{DOMAIN}#kenzu.xxxx#g' deploy/nginx-kenzu.conf | sudo tee /etc/nginx/conf.d/kenzu.conf >/dev/null
# 端子/ハーネス（既存の公開名に合わせる。例 harness.furukawa-lab.com）
sudo sed 's#{TERMINAL_DOMAIN}#harness.xxxx#g' deploy/nginx-terminal.conf | sudo tee /etc/nginx/conf.d/terminal.conf >/dev/null
# （ハーネス8002用のnginxも同様に）
sudo nginx -t && sudo systemctl enable --now nginx
# HTTPS
sudo certbot --nginx -d kenzu.xxxx -d harness.xxxx
```

---

## 7. DNS（Cloudflare）を新サーバへ向ける

- 工場系サブドメイン（例 `kenzu.*`／`harness.*`／端子用）の **Aレコードを「新サーバのElastic IP」** に変更。
- **見積 `estimate.*` は現サーバ（18.178.195.90）のまま**＝変更しない。
- 反映後、ブラウザで各工場システムにアクセスしてログイン確認。

---

## 8. 切替確認 → 現サーバの工場系を停止（見積専用化）

**新サーバで工場系3つが正常稼働し、現場が新URLで使えることを確認してから**、現サーバの工場系を止めます（見積にメモリ・CPUを集中）。

```bash
# 現サーバ(mitsumori-server)で。実名は手順0で確認したもの
sudo systemctl disable --now kenzu-system terminal-system harness-system

# nginxから工場系の振り分けを外す（見積estimate.confは残す）
ls /etc/nginx/conf.d/
sudo mv /etc/nginx/conf.d/kenzu.conf    /etc/nginx/conf.d/kenzu.conf.disabled
sudo mv /etc/nginx/conf.d/terminal.conf /etc/nginx/conf.d/terminal.conf.disabled
# （ハーネス用confがあれば同様に）
sudo nginx -t && sudo systemctl reload nginx

# 確認：見積だけが残る
systemctl list-units --type=service | grep -Ei 'estimate|kenzu|terminal|harness'
free -h
```

> ⚠ 工場ユーザーが使っている時間帯を避け、**2人目さんと時間を合わせて**実施してください。

---

## 9. 仕上げ（任意）

- **見積の同時抽出を増やす**：現サーバが見積専用になったので、`/etc/systemd/system/estimate.service` の `[Service]` に `Environment=EXTRACT_CONCURRENCY=3` を足して再起動すると、営業の同時抽出に余裕が出ます（メモリに応じて）。
- **新サーバの自動更新**：工場系のコード更新用に、新サーバでも `deploy/update.sh` 等で `git pull`→再起動できるようにしておく（見積の自動デプロイtimerは新サーバには不要）。
- **停止保護・監視**：新サーバにも停止保護（disableApiStop）と死活監視を設定。

---

## 役割分担まとめ
- **2人目さん**：手順1〜7（新サーバ構築・工場系3システム・データ移行・DNS）。環境変数/APIキー/ハーネスのリポジトリは2人目さんの管轄。
- **茂泉様／見積担当（私が案内）**：手順8（現サーバの工場系停止＝見積専用化）、手順9の見積側調整。
- **見積（営業）システムは全工程で無変更**（現サーバ・Elastic IP・公開名そのまま）。
