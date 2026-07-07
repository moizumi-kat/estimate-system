# app.py 統合手順（配電盤セットコード）

`setcode.py`（分類＋確認ゲート＋決定的セレクタ）を app.py に**後方互換**で組み込む手順。
方針(CLAUDE.md 第6章)：候補148件は**未承認**なので、まずセットコード表を別ファイル(`cand_haiden_sets.json`)で持つ。承認後に db.json へ統合。既存の1機器1コード選定は温存し、配電盤の本体だけセット選定に置き換える。

## 変更点は2箇所のみ

### パッチ1: EXTRACT_PROMPT に「盤単位のセット属性」を追加
現行の出力スキーマ(app.py 現 line 261)：
```
{"panels":[{"panel":"盤名","items":[{...}]}]}
```
に、盤ごとの `set_attrs` を追加する：
```
{"panels":[{"panel":"盤名",
  "set_attrs":{"settype":"低圧|高圧|段積|","meter":"普通角|広角|マルチ|",
               "phase":"1φ3W|3φ3W|スコット|","cap":"容量KVA数値|",
               "vcb":"8KA|12.5KA|","op":"手動|電動|電動引出|","unclear_specs":[]},
  "items":[{...}]}]}
```
プロンプトに追記する指示（要点）：
- 各盤について、配電盤(受電/饋電/低圧電灯/低圧動力/スコット/段積)なら `set_attrs` を埋める。制御盤(動力制御=分岐回路)・分電盤・端子盤は `settype:""`(空=対象外)。
- **計器種別 meter**：**推測しない**。器具表等で明確に分かる場合のみ「広角/普通角/マルチ」を入れ、単線図だけで確信が持てなければ空にする。
  - ※ meter は `ALWAYS_CONFIRM`(setcode.py)で**常に確認ゲートに載る**。抽出値があればそれを初期選択、無ければ既定「普通角」を初期選択に、コンボボックスで人が最終確認する。よって抽出は無理に埋めなくてよい(安全側=◎誤答ゼロ)。実測(木村単線図)でも計器種別は確定困難で、常時確認が妥当と確認済み。
- **相 phase**：TRが1φ→1φ3W、3φ→3φ3W、スコットTR→スコット。**容量 cap**：TRのKVA。
- **VCB操作 op**：VCB記号が手動=そのまま/電動=電動/**上下に黒四角＋回転矢印記号なら電動引出**。
- 読み切れない仕様は `unclear_specs` に列挙（確認ゲートが人に問う）。

※ line 30 に既に「計器(VM/AM/VS/AS): 形式(普通角/広角/付加)」の指示あり。これを盤単位の meter に集約する。

### パッチ2: select_from_extracted に盤単位フック
`select_from_extracted`(app.py 現 line 1263)の各 panel ループ先頭に、セット判定を挿入：
```python
import setcode  # 冒頭で
...
for p in data.get('panels',[]):
    sa = p.get('set_attrs') or {}
    if sa.get('settype'):
        r = setcode.resolve(p.get('panel',''), sa)
        if r['settype'] and (r['code'] or r['confirm']):
            # 確認ゲート: 未確定仕様があればUIへ(コンボボックス)。ここでは既定適用済みの選定も返す。
            set_row = {'code': r['code'], 'name': setcode_name(r), 'conf': r['conf'],
                       'note': r['note'], 'raw': p.get('panel',''), 'qty':'1',
                       'is_setcode': True, 'confirm': r['confirm']}
            # 配電盤本体=セット1行。分岐MCB/オプション/函体は従来の個別選定で追加。
            rows = [set_row] + select_nonset_items(p)   # 分岐MCB等の非セット個別品のみ
            out.append(dict(panel=p.get('panel',''), rows=rows)); continue
    # settype空(制御盤/分電盤/端子盤 等)は従来どおり全item個別選定
    ... 既存処理 ...
```
- **配電盤本体**＝セットコード1行を出力（積算ソフトが展開）。
- **非セット個別品**（配電盤の分岐MCB、受電盤のUVR/UPS/APFC/デマンド等オプション）＝従来の `select_one` で拾って追加。
- **函体(96系)**＝盤寸法から別途（別ロジック、本統合の対象外）。
- `confirm` が非空なら、選定結果を出しつつ「要確認(コンボボックス)」をUIに表示。

## 反映計画（CLAUDE.md 4章のパッチ方式）
1. **茂泉様が候補148件(`cand_haiden_sets.csv`)を照合・承認**。
2. 承認後、候補を db.json へ統合（`settype/meter/phase/role/vcb/op` フィールド付き）。`setcode.py` の `_load()` を db.json 参照へ1行差替え。
3. **サンドボックスで実図面＋API(ANTHROPIC_API_KEY)で抽出→選定を実測**（本統合は確認ゲート等ロジックのみ単体検証済。抽出の盤属性出力はAPI環境で要実測）。副作用チェック（既存の◎/○/△が変わらない＝回帰ゼロ）。
4. `deploy_setcode.py`（アンカー方式・冪等・自動バックアップ・構文チェック）で本番へ。→ 動作確認 → GitHub反映。

## 現状の検証状況
- セレクタ・確認ゲート・分類：単体検証済（`setcode.py` の __main__、本物見積書の属性で ◎）。
- 低圧セット：本物見積書4案件で TR 20/20 命中（`validation_results.md`）。
- 未実測：API環境での「抽出が set_attrs を正しく出すか」。要承認：候補148件。
