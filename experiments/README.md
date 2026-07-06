# experiments/ — PoC・検証コード（2026-07 セッション）

本番(app.py)には未統合の**実験・実証コード**。次セッションで再開できるよう保存。
開発方針・知見の要約は正本の `CLAUDE.md` を参照。

## 実行の前提
- `db.json` / `attr_table.json` はリポジトリ直下のものを使用（多くのスクリプトは `import app` する）。
- 図面PDF（`seigyo.pdf`＝動力制御盤, `amagasaki.pdf`＝受変電 等）は**リポジトリに含めない**（顧客図面）。Dropbox `Estimate_system0630` 等から取得して同ディレクトリに置く。
- 環境変数 `ANTHROPIC_API_KEY`。Gemini を使うものは同ディレクトリに `.gemini_key`（AI Studioの`AQ.`形式キー）を置く（**gitignore・使用後失効**）。
- 依存：`flask anthropic PyMuPDF openpyxl pillow google-genai`。

## 制御盤エンジン（分岐回路コード方式）
動力制御盤は「回路パターン集＋適用表」。**1負荷＝1分岐回路コード（DB 22-29系）**で積算する。
- `poc_control.py` … 適用表・パターン集を Vision で構造抽出できるか検証。
- `poc_control_engine.py` … パターン部品を個別展開（**旧案・過剰設計**。DBに接触器等のコードが無く不適）。
- `poc_control_v2.py` … **正解**。主回路パターン→回路種別(L-S/スターデルタ/INV)、kW→容量ステップ切上、●/○→MCCB/ELB で分岐回路コードを一発引き。尼崎で60負荷中59を◎化（ファン32台→`22000`）。
- `poc_control_bom.py` … BOM＋コード付与＋Excel（部品個別版・参考）。
- `pattern_master.json` … 主回路A〜L/操作1〜11 の部品マスタ雛形（**御社標準に合わせ人手で確定する**）。

## 二重Vision＋裁定（確実な抽出）
Claude(1回目)∩Gemini 2.5 Pro(2回目)→一致◎/不一致→Claude裁定→判別不能△(人)。
- `cross_check.py` … 突合エンジン（行対応＋フィールド比較、小数/別名正規化）。`gemini_read` 未実装のスタブ。
- `cross_check_gemini.py` … Claude×Gemini を適用表で実突合（100%一致を実証）。
- `cross_check_adjudicate.py` … 制御盤 全適用表で 二重Vision＋Claude裁定＋△。
- `cross_check_juhen.py` … **受変電で本番相当**。名称正規化（コード集合＋数値・別名`TR=T`）、負荷明細除外、裁定キャッシュ。実測 ◎60/○27/△9（真に曖昧な機器のみ）。

## コスト/精度 検証（v1.8関連）
- `run_batch.py` `run_all_v18.py` … 図面一括処理。
- `ab_test.py` `ab_summary.py` … 問題B/◉の A/B（回帰ゼロ確認）。
- `ab_tile.py` `poc_tile.py` … タイル分割の A/B。**タイル＋Haikuは逆効果**（既定OFF）。タイル＋上位モデルは未検証（CLAUDE.md 参照）。
- `deploy_v18.py` … v1.8（問題B/◉/品質ゲート/抽出コスト改修）反映パッチ。**アンカーは正本の現行app.pyに合わせて要確認**（作成時はDropbox v1.6ベース）。

## 要点（詳細は CLAUDE.md）
- ◎の誤答ゼロ最優先／迷ったら△。AIはコード生成せずDB実在コードのみ。
- 2回目は Gemini が安い（入力~1/4・出力~2/5）＆別モデルで独立検証。
- コード選定は決定的。`ai_pick` 曖昧時のみ Gemini に**選定ルールごと**第2意見を求める。
