# EC2 反映手順（GitHub 正本 → 本番）

対象: 積算コード選定システム / 本番 EC2 `~/estimate_code_system_v1.4/`（systemd サービス名 `estimate`）
方針: **GitHub が正本、EC2 は下流**（GitHub から受け取るのみ）。今セッションの改修は多数の関数追加・改修のため、
アンカー方式パッチ（deploy_vXX.py）ではなく **ファイル差し替え** で反映する。

## 反映するファイル（3点）
| ファイル | 内容 |
|---|---|
| `app.py` | 選定ロジック本体（受変電計器グループA解消・高圧LBS/TR・リアクトル・制御盤たすき掛け統合・スコットTR/PF/警報盤・確認ゲート等） |
| `index.html` | UI（セット確認ゲートのコンボボックスを追加） |
| `db.json` | コードDB 3,641件（配電盤セット・AX付・段積等を含む） |

> **二重Vision（Claude＋Gemini）を使う場合**：EC2 に Gemini キー（環境変数 `GEMINI_API_KEY` かリポジトリ外 `.gemini_key`）を設定。
> 無ければ自動で Claude 単独にフォールバック（システムは止まらない）。`DUAL_VISION=0` で無効化可。Gemini は1枚数分かかるため
> gunicorn/nginx のタイムアウトを 600s 以上に。新規導入の詳細は `INSTALL.md`。

## ⚠️ 反映前チェック（重要・退行防止）
CLAUDE.md記載の「v1.7d（Excel名の英数字化）がEC2にあり正本未反映の可能性」に対応済:
- 正本の Excel ダウンロード名を英数字 `estimate_code_{日時}.xlsx` に変更済（iOS等の日本語DL不具合対策）。
- **それでも念のため**、上書き前に EC2 の現行 app.py と正本を差分確認し、EC2 だけにある変更が無いか見る:
  ```
  cd ~/estimate_code_system_v1.4
  diff <(git show HEAD:app.py 2>/dev/null || cat app.py) app.py | head   # ローカル手編集の有無
  ```
  EC2固有の変更が見つかったら、上書き前に必ず正本へ取り込む（相談）。

## 手順
1. **バックアップ**（必須・ロールバック用）
   ```
   cd ~/estimate_code_system_v1.4
   TS=$(date +%Y%m%d_%H%M%S)
   cp app.py app.py.bak_$TS ; cp index.html index.html.bak_$TS ; cp db.json db.json.bak_$TS
   ```
2. **正本を取得して差し替え**（EC2がこのリポジトリのクローンなら git、そうでなければ SFTP）
   - git 運用の場合:
     ```
     git fetch origin && git checkout origin/main -- app.py index.html db.json   # ブランチ名は実運用に合わせる
     ```
   - SFTP 運用の場合: GitHub 正本の `app.py` `index.html` `db.json` を Termius SFTP で上書き。
3. **構文チェック**
   ```
   python3 -c "import ast; ast.parse(open('app.py').read()); print('app OK')"
   python3 -c "import json; d=json.load(open('db.json')); print('db', len(d), 'codes /', sum(1 for x in d if x.get('settype')), 'sets')"
   ```
4. **再起動**
   ```
   sudo systemctl restart estimate
   sudo systemctl status estimate --no-pager | head
   ```
5. **動作確認**
   - `curl -s localhost:<PORT>/api/health` → `{"db":3641,...,"ok":true}`
   - ブラウザで図面を1件アップ→抽出→選定→**セット確認ゲート**（配電盤の計器種別/VCB/操作方式のコンボボックス）が
     出ることを確認→回答して再選定→受電盤の計器が抑制されコードにまとまる→Excel ダウンロード。

## ロールバック
```
cd ~/estimate_code_system_v1.4
cp app.py.bak_<TS> app.py ; cp index.html.bak_<TS> index.html ; cp db.json.bak_<TS> db.json
sudo systemctl restart estimate
```

## 反映後に社員試用で見てほしい点（フィードバック観点）
- ◎の誤りが無いか（最優先）。誤った◎があれば必ず報告。
- 確認ゲート（計器種別・VCB・操作方式・容量）の選択肢で足りない/分かりにくいものは無いか。
- △になった機器の妥当性（安全側△＝仕様不足/支給品/凡例なし は想定内。誤って△なら報告）。
- 動力制御盤の適用表（たすき掛け）が正しく分岐回路コードになっているか。
