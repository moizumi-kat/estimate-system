# -*- coding: utf-8 -*-
"""ローカル実行用CLI(オフライン完結・通信不要)。

設計から供給されたDXFフォルダと製番を指定して、工場の工程フロー
(2人目=入口: ロケーター付与→不備チェック→端子番号付与 → 1人目・3人目が並行)の
確認UI(HTML)・出力(CSV/DXF)・ハブ(index)を一括生成する。

使い方:
  python -m fromto_qc.run_local <DXFフォルダ> [製番] [-o 出力先]

  - 製番を省略すると、フォルダ内で見つかった製番の一覧を表示する。
  - 出力先を省略すると <DXFフォルダ>/<製番>_工程 に生成する。
  - 生成された『<製番>_00_ハブ.html』をブラウザで開くと全工程に移動できる。

ファイルの対応(社内の命名規則):
  F* = 制御シート(From-To元)   E* = スケルトン   D*/G* = 内部配置図
  *-DCT = 機器相番号(ロケーター対応表)   *.txt = 手本(照合用・任意)
"""
import argparse
import glob
import os
import re
import sys

from . import workflow


_SEIBAN_RE = re.compile(r'(\d-\d+-\d+)')


def seibans_in(folder):
    """フォルダ内のDXF/ファイル名から製番の一覧を抽出する。"""
    out = set()
    for f in glob.glob(os.path.join(folder, '*')):
        m = _SEIBAN_RE.match(os.path.basename(f))
        if m:
            out.add(m.group(1))
    return sorted(out)


def _find(folder, seiban, letters, exclude_dct=True):
    """<製番>-<letter>*.DXF/.dxf を集める(letters 例: ['F'])。DCTは除外可。"""
    files = []
    for ext in ('DXF', 'dxf'):
        for lt in letters:
            files += glob.glob(os.path.join(folder, f'{seiban}-{lt}*.{ext}'))
    files = sorted(set(files))
    if exclude_dct:
        files = [f for f in files if 'DCT' not in os.path.basename(f).upper()]
    return files


def collect_inputs(folder, seiban):
    """製番の入力ファイル一式を返す: (seq, skel, layout, dct)。"""
    seq = _find(folder, seiban, ['F'])
    skel = _find(folder, seiban, ['E'])
    layout = _find(folder, seiban, ['D', 'G'])
    dct = sorted(glob.glob(os.path.join(folder, f'{seiban}-*-DCT.dxf')) +
                 glob.glob(os.path.join(folder, f'{seiban}-*-DCT.DXF')))
    return seq, skel, layout, dct


def run(folder, seiban, out_dir=None):
    """1製番の工程フローを生成する。戻り: run_workflow の結果 dict。"""
    seq, skel, layout, dct = collect_inputs(folder, seiban)
    if not (seq or skel or layout):
        raise SystemExit(f'入力DXFが見つかりません: {seiban} (フォルダ: {folder})')
    out_dir = out_dir or os.path.join(folder, f'{seiban}_工程')
    print(f'製番 {seiban}: 制御シート{len(seq)} スケルトン{len(skel)} '
          f'配置図{len(layout)} 機器相番号{len(dct)}')
    res = workflow.run_workflow(seiban, seq, skel, layout, dct_paths=dct, out_dir=out_dir)
    hub = res.get('hub')
    print('生成しました →', out_dir)
    if hub:
        print('ハブ(index)をブラウザで開いてください:',
              os.path.join(out_dir, hub))
    for stage in ('2人目', '1人目', '3人目'):
        st = res.get(stage, {})
        files = st.get('files', {})
        if files:
            print(f'  [{stage}] ' + ' / '.join(files.values()))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='python -m fromto_qc.run_local',
        description='設計DXFから工程フロー(確認UI・出力)を一括生成(オフライン)。')
    ap.add_argument('folder', help='設計DXFのフォルダ')
    ap.add_argument('seiban', nargs='?', help='製番(例 1-12188-132)。省略で一覧表示')
    ap.add_argument('-o', '--out', help='出力先フォルダ(省略で <folder>/<製番>_工程)')
    args = ap.parse_args(argv)

    if not os.path.isdir(args.folder):
        raise SystemExit(f'フォルダがありません: {args.folder}')
    if not args.seiban:
        sbs = seibans_in(args.folder)
        if not sbs:
            raise SystemExit(f'製番が見つかりません: {args.folder}')
        print('製番を指定してください。見つかった製番:')
        for sb in sbs:
            print('  ', sb)
        return 0
    run(args.folder, args.seiban, args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
