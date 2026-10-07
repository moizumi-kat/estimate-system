# DWG → DXF 変換(前処理)

一部の工程資料は DWG で届く(②機器相番号_…DCT, ⑤太物経路_…)。ezdxf は DXF 専用のため、
解析前に DXF へ変換する。`dwg_convert.py` が変換器を呼ぶ(ローカル変換・データは外部に出さない)。

## 変換器の用意(いずれか)

1. **GNU LibreDWG `dwg2dxf`(OSS・推奨)**
   ```sh
   curl -O https://ftp.gnu.org/gnu/libredwg/libredwg-0.14.tar.xz
   tar xf libredwg-0.14.tar.xz && cd libredwg-0.14
   ./configure --disable-bindings --disable-python && make -j4
   # => programs/dwg2dxf
   export DWG2DXF=$PWD/programs/dwg2dxf
   ```
   実データ(AutoCAD 2013 = AC1027)で全DWG→属性保持DXFの変換を確認済み。

2. **貴社CAD(BricsCAD/AutoCAD)**: `SAVEAS`/バッチで DXF 書き出し。社内に留まり再現も確実。

3. **ODA File Converter(ODA公式・無償)**: DWG↔DXF 一括変換の定番。

## 使い方

```python
from fromto_qc import dwg_convert as dc
dc.ensure_dxf('機器相番号_…-DCT.dwg')   # 横に .dxf を作りパスを返す(既にあれば再利用)
dc.convert_dir('<製番フォルダ>')          # 配下の全DWGを一括変換
```

環境変数 `DWG2DXF` に実行ファイルパスを設定するか、PATH に置く。見つからなければ
`ConverterNotFound` を送出する(どの変換器も無い旨と対処を案内)。

## 注意

- OSS変換のため、寸法線/ハッチ等の描画再現は保証されない。ブロック属性(DEVICE/DEVICE1/PMT/
  TYPE 等=解析で使う情報)は保持される。重要判断は従来どおり Vision で裏取りする。
- 変換器バイナリ(約15MB)はリポジトリに含めない。各環境で用意する。
