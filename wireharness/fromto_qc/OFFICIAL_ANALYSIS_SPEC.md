# DXFシーケンス図 解析ルール仕様（AI/プログラム用）

本ファイルは、当社のシーケンス図（展開接続図, BricsCAD V19, Shift-JIS/cp932）を
AIまたはプログラムで解析するための**機械可読な判定仕様**である。
解析対象のDXFと本ファイルをAIに与え、下記の手順・規則に厳密に従って
「構造化 → 間違い探し（検図）」を行わせる。実証済みシミュレーターの判定ロジックと一致。

- 主目的: 検図（配線ミス・機器過不足・電源未到達・線番不整合の検出）
- 副次利用: 機器一覧 / 接点集計 / 動作シミュレーション
- 座標系: DXF作図単位（mm想定）。以下の距離しきい値は作図単位。

---

## 0. 入力の前提

- DXFの `ENTITIES` セクションを解析する。文字コードは cp932。
- 機器は `INSERT`（ブロック参照）＋直後の `ATTRIB`（属性）で表現。
  - ブロック名: `INSERT` の group code 2（`EffectiveName` 相当）。
  - 属性: 各 `ATTRIB` の tag=group 2, 値=group 1。挿入点=group 10/20, 回転=group 50(度)。
- 配線は `LINE`（10/20=始点, 11/21=終点）と `LWPOLYLINE`（頂点列 10/20）。
- 画層は group code 8。

## 1. 画層マップ

| 画層 | 役割 | 扱い |
|---|---|---|
| `L_CONTROL` | 制御線（内部配線） | 配線ネット（主対象） |
| `L_MAIN` | 電源母線 | 配線ネット＋**電源判定の基準**（ここに繋がる線番＝制御電源） |
| `L_OUTSIDE` | 外部制御線（端子台の先） | 配線ネット（端子台で内部と接続） |
| `_SIM_STATE`,`_SIM_SELECT`,`_SIM_WARN` | ツール描画専用 | **無視** |
| その他（`0`,`TEMPLATE`,`DIAGRAM`,`OTHER`…） | 図枠・注記 | 非配線（対象外） |

- 配線画層 `WIRE_LAYERS = {L_CONTROL, L_MAIN, L_OUTSIDE}`。

## 2. シンボル種別判定（順序厳守・最初に一致した規則を採用）

`n = UpperCase(blockName)`, `parts = UpperCase(PARTS)`, `typ = UpperCase(TYPE)`。
`*` はワイルドカード（0文字以上）。上から順に評価し、最初にマッチした種別で確定。

| # | 条件 | 種別コード | 意味 |
|---|---|---|---|
| 0 | （記号定義.csv があれば最優先で適用） | CSV指定 | 追加/上書き定義 |
| 1 | `n == "_CROSSPOINT1"` | `CROSS` | 接続点マーカ |
| 2 | `n LIKE "_LU*"` | `TERM` | 端子台（中継） |
| 3 | `typ == "61F-AN"` OR `parts == "ALTR"` | `ALT` | 交互リレー |
| 4 | `n LIKE "LS_A*ROB*"` OR `n LIKE "LS_A*ROT*"` | `COSA` | COS個別接点 a |
| 5 | `n LIKE "LS_B*ROB*"` OR `n LIKE "LS_B*ROT*"` | `COSB` | COS個別接点 b |
| 6 | `n LIKE "LS_*ROB*"` OR `n LIKE "LS_*ROT*"` OR `n LIKE "LS_CH*"` | `SEL` | 切替/カムスイッチ |
| 7 | `Left(n,5) == "_LS_C"` | `CHG` | 切替接点（c接点：共通+NO+NC） |
| 8 | `n == "LS_A-LIT"` OR `n == "LS_A-LIT_A"` | `A` | a接点（外部機器接点） |
| 9 | `n == "LS_B-LIT"` | `B` | b接点（外部機器接点） |
| 10 | `Left(n,5) IN {"_LS_A","LS_AA"}` OR `n LIKE "LS_A*"` | `A` | a接点（押釦a=LS_AAPST含む） |
| 11 | `Left(n,5) IN {"_LS_B","LS_BA"}` OR `n LIKE "LS_B*"` | `B` | b接点（押釦b=LS_BAPST含む） |
| 12 | `n LIKE "LA_KR*"` | `KEEP` | キープリレー |
| 13 | `n LIKE "LA_TLRON*"` | `TON` | タイマ（オンディレイ） |
| 14 | `n LIKE "LA_TLROFF*"` | `TOFF` | タイマ（オフディレイ） |
| 15 | `n LIKE "LA_*"` | `COIL` | コイル/リレー |
| 16 | `n LIKE "LC_BZ*"` OR `parts == "BZ"` | `LAMP` | ブザー（負荷） |
| 17 | `n LIKE "LO_*"` | `LAMP` | 表示灯（負荷） |
| ― | 上記いずれも不一致 | `UNKNOWN` | 未分類（DEVICE有りなら**要検出**） |

種別コード→分類:
`A`=a接点, `B`=b接点, `COIL/TON/TOFF/KEEP/ALT`=コイル系, `LAMP`=負荷,
`SEL`=切替, `COSA/COSB`=COS個別接点, `CHG`=切替接点(c), `CROSS`=接続点, `TERM`=端子台。

## 3. 属性スキーマ

| tag | 内容 | 例 |
|---|---|---|
| `DEVICE` | 機器記号 D1 | 52, 51X, RL, 43 |
| `DEVICE1` | 回路番号 D2 | 101, 304 |
| `DEVICE2` | 区分 D3 | ON, OFF |
| `PARTS` | 部品種別 | MC,AXR,SL,COS,PBS,BZ,CS,ALTR |
| `TYPE` | 型式 | RU2S-A200, 61F-AN |
| `CMNTJ1` | 位置名/コメント/タイマ設定時間 | 切,入,自動,交互,No.1,2sec |
| `CMNTJ2` | 補足コメント | |
| `TB` | 端子相対座標 `x1,y1,x2,y2,…` | 0,0,0,-100 |
| `PMT` | 図形範囲 `左,上,右,下` | -45,0,25,-100 |
| `線番` | 線番号（H_SENBAN/V_SENBAN） | RC1,101R,CR,303R,P1 |
| `VOLTAGE1/2` | 電圧（V_VOLTAGE/H_VOLTAGE） | DC100V |

- **機器キー** = `DEVICE|DEVICE1|DEVICE2`（同一キー＝同一機器）。
- DEVICE(D1)が空の記号は、`CMNTJ1`（無ければ種別名, さらに無ければ座標）で一意化する
  （空のままだと別機器が同一キーに衝突する）。

## 4. 接続の構築アルゴリズム

しきい値: `POINT_TOL = 5`（同一節点判定）, `TERMINAL_TOL = 15`（端子台）,
`SENBAN_SEG_MAXDIST = 250`（線番→配線）。

1. **配線ネット化**: `WIRE_LAYERS` の LINE/LWPOLYLINE の各セグメント端点を
   量子化（`round(x/POINT_TOL)`）してUnion-Findで結合。
2. **接続点（CROSS）**: `_crossPoint1` の挿入点から `POINT_TOL` 内を通る配線セグメントの
   端点を、その挿入点と結合（T字接続）。
3. **端子台（TERM）**: `_LU*` の挿入点（中心）から `TERMINAL_TOL` 内の配線セグメント端点を
   すべて結合（内部 L_CONTROL ⇔ 外部 L_OUTSIDE を橋渡し。向き・TB/PMT非依存）。
4. **端子展開**: 各記号の `TB="lx1,ly1,lx2,ly2,…"` を
   `world = 挿入点 + 回転(θ)·尺度·(lx,ly)` でワールド座標化。
   端子は (a) 同一量子化セルの配線端点、または (b) 配線セグメント上（点-線分距離≦POINT_TOL）
   のとき、その配線ネットへ結合（接点が線に重なって突き抜けても救済）。
5. **線番割当**: `H_SENBAN/V_SENBAN` の属性`線番`を、挿入点から垂線距離が最小かつ
   `SENBAN_SEG_MAXDIST` 以内の配線セグメントのネットへ割当。同一線番のネットは結合。

## 5. 制御電源の判定

1. `L_MAIN` の全セグメント端点が属するネット集合 = **電源ネット**。
2. 電源ネットの線番を hot / neutral に振り分け（`PowerSide`）:
   - hot(R側): 線番末尾が `R` または `P`、あるいは接頭が `RC/RO/R/P`
   - neutral(S側): 線番末尾が `S` または `N`、あるいは接頭が `SC/SO/S/N`
   - 例: `CR,303R,304R,RC1,P1,ROPR1`=hot / `CS,303S,304S,SC1,N1,SOPR1`=neutral
3. `L_MAIN` に電源が無い図面は、線番パターンにフォールバック
   （`POWER_HOT="RC*,*R,P*"`, `POWER_NEUTRAL="SC*,*S,N*"`。`*`=任意文字, `#`=数字1桁以上）。

## 6. 接点開閉ロジック（シミュレーション/到達性判定に使用）

- a接点: 機器が作動で閉 / b接点: 作動で開。
- COS個別接点(COSA/COSB): 選択位置名(`gInput`のindex→`gCosPos`)＝自身の`CMNTJ1`のとき閉。
  同一機器の同一位置名の接点は**同時に閉**（例 43-304 の「交互」2接点）。
- 切替接点(CHG, `_LS_C*`): 共通(0,0)/NC(+50,-100)/NO(0,-100) の3極。通常=共通-NC, 動作=共通-NO。
- コイル: 一端 hot・他端 neutral で励磁。KEEP=SET/RESETでラッチ, TON/TOFF=時間遅延, ALT=立上りで反転保持。

## 7. 間違い探し（検出ルール）

各ルール: 検出方法 → 想定原因。`severity` 目安を付す。

| id | 内容 | 検出方法 | severity |
|---|---|---|---|
| E1 | 電源未到達の負荷 | 全接点/切替を閉じた最大接続でも、コイル/表示灯の両端が hot と neutral に到達しない | high |
| E2 | 未分類シンボル | DEVICE有りかつ種別=UNKNOWN | high |
| E3 | 孤立端子 | 記号の端子がどの配線ネットにも属さない（単独ネット） | high |
| E4 | 電源欠落 | 電源ネットに hot または neutral が存在しない | high |
| E5 | 線番不整合 | 1ネットに2つ以上の異なる線番／線番の無い被接続ネット／同一線番が非連結の複数ネットに存在 | med |
| E6 | 機器情報不備 | DEVICE空／同一と思われる機器でD1・D2不揃い | med |
| E7 | 接点定格超過(HIT) | 機器キー毎の使用接点数 > TYPEの定格接点数（RU2S=2c, RU4S=4c, MS4*=2c…） | med |
| E8 | タイマ設定異常 | TON/TOFFの`CMNTJ1`が時間として解釈不能、または明らかなプレースホルダ（例 9999） | low |
| E9 | 接続点漏れ | 2本の配線がT字交差（一方の端点が他方の線分上, ≦POINT_TOL）だが `_crossPoint1` が無い | med |
| E10 | 画層誤り | 配線が非配線画層にある／記号・注記が配線画層にある疑い | low |
| E11 | 端子台中継欠落 | `L_OUTSIDE` のネットが端子台経由で `L_CONTROL` に接続されていない | med |

定格推定 `ContactRating(TYPE)`: `RU42/RU4→4c`, `RU2→2c`, `MS4→2c`, その他→不明(検査スキップ)。

## 8. 出力フォーマット（検図結果, JSON推奨）

```json
{
  "summary": { "devices": 0, "nets": 0, "power_hot": ["P1"], "power_neutral": ["N1"], "findings": 0 },
  "findings": [
    {
      "id": "E1",
      "severity": "high",
      "device": "51X|101|",
      "wireNo": ["10106"],
      "location": {"x": 0, "y": 0},
      "message": "コイル 51X-101 が電源(hot〜neutral)に到達しません。",
      "hint": "配線の断、線番違い、または電源設定を確認してください。"
    }
  ]
}
```

- `location` は該当機器/ネットの代表座標（人が図面で探せるよう）。
- 断定できないものは severity=low + hint で「要確認」として提示（過検出より見落とし防止を優先しつつ、誤指摘は hint で緩和）。

## 9. 拡張ポイント（当社運用で追記）

- 記号: 未知ブロックは §2 の CSV（`パターン,種別`）に追加。
- 定格: §7 の型式→定格 表を拡充。
- 電源: R/S/P/N 以外の綴りがあれば §5 に追加。
- 画層: 実使用の画層名が §1 と異なる場合は置換。

> 本仕様は解析済み実データ（配電盤/サーモ/手元操作盤/COS混在 等）と実証済みシミュレーターの
> 判定ロジックに基づく。全社の全図面を網羅している保証はないため、未分類・未接続の検出結果を
> 用いて規則を反復的に補完すること。
