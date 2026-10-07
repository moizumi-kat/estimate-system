# -*- coding: utf-8 -*-
"""DWG → DXF 変換(ローカル・データを外部に出さない)。

一部の工程資料(②機器相番号_…DCT, ⑤太物経路_…)は DWG で届く。ezdxf は DXF 専用で
DWG を読めないため、ハーネス解析の前処理として DXF へ変換する。

変換器は GNU LibreDWG の `dwg2dxf`(オープンソース, ローカル実行)を使う。導入方法:
  - Ubuntu 等でビルド: LibreDWG リリース(ftp.gnu.org/gnu/libredwg/)を
    `./configure --disable-bindings && make` し、`programs/dwg2dxf` を得る。
  - あるいは貴社CAD(BricsCAD/AutoCAD)や ODA File Converter で DXF 書き出ししたものを使う。
バイナリはリポジトリに含めない。場所は環境変数 DWG2DXF か PATH、既知のビルド先から探す。
実データ(AutoCAD 2013=AC1027)で全DWG→属性保持DXFの変換を確認済み。
※OSS変換のため寸法/ハッチ等の描画再現は保証されない。重要判断は Vision で裏取りする方針。
"""
import os
import shutil
import subprocess

_CANDIDATE_PATHS = [
    os.environ.get('DWG2DXF'),
    shutil.which('dwg2dxf'),
]


def find_converter():
    """dwg2dxf 実行ファイルのパスを返す。見つからなければ None。
    探索順: 環境変数 DWG2DXF → PATH。"""
    for p in _CANDIDATE_PATHS:
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return None


class ConverterNotFound(RuntimeError):
    pass


def convert(dwg_path, out_dxf=None, overwrite=True):
    """1つの DWG を DXF に変換し、出力パスを返す。
    out_dxf 省略時は同じ場所に拡張子 .dxf で出力。変換器が無ければ ConverterNotFound。"""
    conv = find_converter()
    if not conv:
        raise ConverterNotFound(
            'dwg2dxf が見つかりません。環境変数 DWG2DXF に LibreDWG の dwg2dxf パスを '
            '設定するか、CAD/ODA で DXF 書き出ししてください。')
    if out_dxf is None:
        out_dxf = os.path.splitext(dwg_path)[0] + '.dxf'
    if os.path.exists(out_dxf) and not overwrite:
        return out_dxf
    r = subprocess.run([conv, '-y', '-o', out_dxf, dwg_path],
                       capture_output=True, text=True)
    # dwg2dxf はプレビュー節のスキップ等を stderr に出すが、DXF が生成されれば成功とみなす
    if not (os.path.exists(out_dxf) and os.path.getsize(out_dxf) > 0):
        raise RuntimeError(f'DWG変換失敗: {dwg_path}\n{r.stderr[:500]}')
    return out_dxf


def ensure_dxf(path, overwrite=False):
    """DWG なら変換して DXF パスを返す。DXF ならそのまま返す。
    既に横に .dxf があればそれを返す(overwrite=False 時)。変換器が無く DWG の場合は例外。"""
    if path.lower().endswith('.dxf'):
        return path
    if path.lower().endswith('.dwg'):
        cand = os.path.splitext(path)[0] + '.dxf'
        if os.path.exists(cand) and not overwrite:
            return cand
        return convert(path, cand, overwrite=overwrite)
    return path


def convert_dir(directory, overwrite=False):
    """ディレクトリ配下(再帰)の全 DWG を横に DXF 変換する。戻り: [(dwg, dxf|None, error)]。"""
    results = []
    for root, _dirs, files in os.walk(directory):
        for fn in files:
            if fn.lower().endswith('.dwg'):
                src = os.path.join(root, fn)
                try:
                    results.append((src, ensure_dxf(src, overwrite=overwrite), None))
                except Exception as e:
                    results.append((src, None, str(e)))
    return results
