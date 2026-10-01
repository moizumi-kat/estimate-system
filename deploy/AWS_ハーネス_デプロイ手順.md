# ハーネスデータ自動生成システム — AWS(EC2) デプロイ手順

見積システム・検図システムと**同じEC2・同じ形態**（systemd + gunicorn + nginx）で、
ハーネスシステムを **別ポート(8002)・別サブドメイン・別データ**として相乗りさせる。
ドメイン例: `harness.furukawa-lab.com`。アクセスは**社内限定**。

| システム | gunicorn | ポート | 例ホスト名 |
|---|---|---|---|
| 見積 | app:app | 8000 | estimate.furukawa-lab.com |
| ハーネス（**検図統合**） | wireharness.harness_app:app | **8002** | **harness.furukawa-lab.com** |

> ※ 旧・単独検図アプリ(kenzu_app:app, 8001)は**廃止**。検図機能はハーネスアプリの
>   `/check`（単独検図）＋ ①設計不備 に統合済み。8001 は停止・無効化してよい
>   （`sudo systemctl disable --now kenzu-system` / nginx の kenzu.conf 削除）。

## 0. 方針
- 既存EC2に相乗り（新規に立てる場合も手順は同じ）。
- 永続データは EBS 上の `/var/lib/harness-system/`（`work`=アップロード/出力/ログ/バックアップ、
  `state`=学習結果）。再デプロイ(git pull)してもここは保持。
- **アクセス制御**:
  - 評価フェーズは **ID+パスワードの画面ログイン**で可（`/etc/harness-system.env` に
    `HARNESS_USER`/`HARNESS_PASSWORD`/`HARNESS_SECRET` を設定）。IP制限/VPNが不要で手軽。
  - より堅牢にするなら **社内限定**: ①nginx の allow/deny（社内固定IP）＋②セキュリティグループで
    80/443 を社内IP/VPNのみ許可。可能なら VPC プライベート配置が最も堅牢。両者は併用可。
- このシステムは外部APIキーを持たない（図面のみでローカル処理）。秘匿情報の露出面が小さい。

## 1. 取得・依存（※見積とは別ディレクトリに clone する）
> 本ブランチは見積(main)と**履歴が別系統**。見積の作業ディレクトリで checkout 切替すると
> 見積のコードが置き換わり壊れる。必ず**別フォルダに独立 clone**し、見積には触れないこと。
```bash
# 見積EC2に相乗り。ただし別ディレクトリ(例 /opt/harness-system)へ clone する。
sudo mkdir -p /opt/harness-system && sudo chown $(id -un): /opt/harness-system
git clone https://github.com/moizumi-kat/estimate-system.git /opt/harness-system
cd /opt/harness-system
git checkout claude/wire-harness-software-2322ld     # 本番でも当面この評価ブランチを使用
python3 -m venv venv && ./venv/bin/pip install -U pip
./venv/bin/pip install -r wireharness/requirements.txt
```

## 2. 環境変数（ログイン情報）を作成
```bash
sudo cp deploy/harness-system.env.example /etc/harness-system.env
sudo vi /etc/harness-system.env   # HARNESS_USER/PASSWORD/SECRET・HARNESS_WORK/STATE を設定
sudo chmod 600 /etc/harness-system.env
```

## 3. 設置（推奨＝ワンショット）
`/opt/harness-system` で下記を実行すれば、永続領域作成→systemd(8002)→nginx→疎通確認まで自動。
```bash
HARNESS_DOMAIN=harness.furukawa-lab.com bash deploy/setup-harness.sh
```
手動で行う場合は 3a/4 を参照。

### 3a. systemd 常駐（手動の場合）
```bash
sudo cp deploy/harness-system.service /etc/systemd/system/harness-system.service
# APP_DIR は clone 先、APP_USER は実行ユーザ(例 ec2-user)
sudo sed -i 's#{APP_DIR}#/opt/harness-system#g; s/{APP_USER}/ec2-user/g' /etc/systemd/system/harness-system.service
sudo mkdir -p /var/lib/harness-system/work /var/lib/harness-system/state
sudo chown -R ec2-user:ec2-user /var/lib/harness-system
sudo systemctl daemon-reload && sudo systemctl enable --now harness-system
sudo systemctl status harness-system          # active(running) を確認
curl -s http://127.0.0.1:8002/api/health       # {"status":"ok",...}
```

## 4. nginx 公開（社内限定）
```bash
sudo cp deploy/nginx-harness.conf /etc/nginx/conf.d/harness.conf
# {HARNESS_DOMAIN}=harness.furukawa-lab.com、{OFFICE_CIDR}=自社の固定IP帯 に置換
sudo vi /etc/nginx/conf.d/harness.conf
sudo nginx -t && sudo systemctl reload nginx
```

## 5. DNS と HTTPS
- Route 53（または社内DNS）で `harness.furukawa-lab.com` を EC2 のIP（またはALB）へ向ける。
- HTTPS: `sudo certbot --nginx -d harness.furukawa-lab.com`（Let's Encrypt）、
  または ALB + ACM 証明書。社内限定なら ALB + 内部向け/社内IP制限でも可。

## 6. セキュリティグループ（社内限定の要）
- インバウンド 80/443 は**社内の固定グローバルIP/CIDR または VPN のみ**許可。
- 8002 は 127.0.0.1 バインドのため外部露出なし（nginx 経由のみ）。
- SSH(22) は踏み台/社内IPのみ。

## 7. 動作確認
1. `https://harness.furukawa-lab.com/` が社内から開く（社外からは拒否されること）。
2. 製番＋図面DXFをアップロード→一括生成→確認UI①②③→Excel出力。
3. `/design` で修正前後図面を登録→学習。`/admin` でバックアップ/ログ確認。

## 8. 更新（再デプロイ）
```bash
cd /opt/harness-system
git pull origin claude/wire-harness-software-2322ld        # 評価ブランチを更新
./venv/bin/pip install -r wireharness/requirements.txt     # 依存変化時のみ
sudo systemctl restart harness-system
```
- `HARNESS_STATE`/`HARNESS_WORK` は `/var/lib/harness-system/` にあるため、
  コード更新で**学習結果・アップロード・バックアップは保持**される。

## 9. 社内サーバ(オンプレ)への移行 — クラウド評価後
本システムは **AWS固有サービスに依存しない**（素のLinux+systemd+gunicorn+nginx、
データはローカルディレクトリ）。クラウドで評価して問題なければ、**同じ定義ファイルのまま**
社内サーバへ移せる（ロックインなし）。手順は本書の 1〜8 と同一で、違いは次の3点だけ:

1. **DNS**: Route53 の代わりに**社内DNS**で `harness.furukawa-lab.com`（または社内ホスト名）を
   社内サーバのIPへ向ける。
2. **証明書(HTTPS)**: ACM/Let's Encrypt の代わりに**社内CA発行証明書**か自己署名を nginx に設定
   （社内限定のため外部CAは必須ではない）。社内限定アクセスは nginx allow/deny ＋
   社内ファイアウォール/ネットワーク分離で担保。
3. **データ移行**: 評価中に貯まった学習結果・実績を引き継ぐ場合、永続領域をそのままコピー:
   ```bash
   # AWS側で固める
   sudo tar czf harness-state.tgz -C /var/lib harness-system
   # 社内サーバへ転送し展開
   sudo tar xzf harness-state.tgz -C /var/lib
   sudo chown -R {APP_USER}:{APP_USER} /var/lib/harness-system
   sudo systemctl restart harness-system
   ```
   これで `learned.json`／`design_cases.json`／`confirmed/`／アップロード/出力/バックアップが
   社内サーバへそのまま引き継がれる。
- アプリ本体は `git clone`（または tar 配布）＋ `pip install -r wireharness/requirements.txt` で同一。
  外部APIキー等の秘匿情報は持たないため、持ち出し時の露出リスクも小さい。
- クラウドと社内の**並行稼働**も可能（別ホスト名）。切替時は社内側へデータ移行後、DNSを社内へ向ける。

## 10. バックアップ/復元
- 学習結果は学習のたびに `HARNESS_WORK/_backup/<日時>/` へ自動退避（直近30世代）。`/admin`で手動退避も可。
- EBS スナップショットで `/var/lib/harness-system/` ごと定期バックアップを推奨。
- 復元は `_backup/<日時>/` の learned.json / design_cases.json / confirmed を
  `HARNESS_STATE`(/var/lib/harness-system/state) へ戻して restart。
