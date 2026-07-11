# 電気図面 QC + From-To 抽出（DXF幾何 × Vision ハイブリッド）

制御盤・分電盤の電気図面（BricsCAD/AutoCAD DXF: シーケンス／スケルトン）から、
**(1) 製造前の図面QCチェック** と **(2) From-To（配線）データの自動抽出** を行うツール。

## なぜハイブリッドか

実データ（5-29026 等）で検証した結論：

- DXFには接続リスト（ネットリスト）は**入っておらず**、接続は「線の位置」＝幾何にしか無い。
- 幾何解析だけでは **機器の入力側/出力側を区別できず**、機器レベル再現率は **約31%で頭打ち**。
- 一方、DXFから描き起こした**きれいなタイル画像をVision（Claude）に読ませると、人と同じように線を追って接続を判定**でき、この壁を越えられる（29026制御ラダーで実証）。

そこで **幾何（決定論的な骨格）× Vision（人的な結線読み）** を突合する：
一致 → 確定 ／ 不一致 → QC指摘（要確認）。これがそのまま「電気ミス検知」にもなる。

## 構成

| ファイル | 役割 |
|---|---|
| `geometry.py` | DXF→構造モデル（端子ピン・電線・ネット・号線・機器外形枠）。TB属性＋TEMPLATE点＋ブロック内電線＋外形枠を統合 |
| `qc.py` | 製造前QCチェック（浮き電線・スナップ誤差・号線欠落・孤立ネット） |
| `render.py` | 構造モデル→Vision入力用のタイル画像（端子ピン・号線を強調） |
| `vision.py` | Vision（Claude / Gemini）で結線トレース→From-To（`trace_tile` / `trace_tile_gemini`）。スマートタイリングで全面処理 |
| `ensemble.py` | 幾何 × Claude × Gemini を号線単位で突合し「全一致=自動確定 / 多数決=準確定 / 割れ=要確認」を判定 |
| `compare.py` | 人手ハーネスデータ.txt の解析と一致率スコア |
| `run.py` | CLIオーケストレータ |

## 使い方

```bash
# 幾何のみ（APIキー不要）: QCチェック＋機器レベルFrom-To突合
python -m wireharness.fromto_qc.run \
  --dxf seq.dxf skel.dxf --human harness.txt \
  --alias alias.json

# Vision入力用タイル画像を出力
python -m wireharness.fromto_qc.run --dxf seq.dxf --tiles out/ --cols 2 --rows 3

# Claude Visionトレース（要 ANTHROPIC_API_KEY）→ 幾何と突合
ANTHROPIC_API_KEY=sk-... python -m wireharness.fromto_qc.run \
  --dxf seq.dxf skel.dxf --human harness.txt --vision

# 三重アンサンブル（幾何 × Claude × Gemini）: 全一致=自動確定 / 割れ=要確認
ANTHROPIC_API_KEY=sk-... GEMINI_API_KEY=... python -m wireharness.fromto_qc.run \
  --dxf seq.dxf skel.dxf --human harness.txt --vision --gemini
```

## アンサンブル（幾何 × Claude × Gemini）

独立した読みを号線単位で突合し、`confirmed`(全一致・自動確定) / `majority`(多数決・準確定) /
`split`(割れ・人が確認) を判定。独立した検出を重ねるほど「サイレント誤り」が希少になり、
人の確認は "割れた少数" だけに縮む（＝限りなく無人に近い半自動）。

`alias.json` は機器別名辞書（人手データの命名 ↔ 図面の機器記号）。例：
```json
{"入102": "3102", "切102": "3102"}
```
この辞書は、過去の「図面＋人手From-To」の対から自動学習して充実させられる。

## 現状と到達見込み

- **幾何のみ**：機器レベル再現率 ~31%（＝下限。QCチェックには既に有用）。
- **ハイブリッド（Vision併用）**：機器の入出力側を解決でき、人手同等に近づく。
  ※ Visionは非決定論的なので、幾何と一致した接続のみ確定し、不一致は人が確認（QCゲート）。

## 依存

```
ezdxf>=1.1
matplotlib>=3.5
anthropic>=0.40        # Claude Visionトレース時のみ
google-genai>=0.3      # Gemini Visionトレース時のみ
```

## 設計上の前提（精度を上げる条件）

- 部品ライブラリの**端子ピン標準化**（各端子に接続点＋端子番号）が進むほど幾何側の精度が上がる。
- 作図規約（電線端点を端子にスナップ／号線付与）は、実データ上シーケンスで既に96%満たされている。
- スケルトンは端子点を持たないため機器レベル（外形枠）で補完。端子レベルが必要ならVisionで補う。
