# -*- coding: utf-8 -*-
"""ハーネス生成の回帰テスト（製番別 正解率＝recall を計測）。

目的（茂泉様方針）:
  「全結線に対する recall」を主指標に、改善のたび同じ土俵で数値を追う。
  システム抽出(図面のみ) を 人手ハーネスシート と照合し、製番別の
  号線recall / 端子recall を表で出す。

データ配置（data_dir 直下、命名で自動対応づけ）:
  図面 DXF: <製番>-<種別コード><番号>[.-DCT].dxf
     D/G=内部配置図, E=スケルトン(主回路), F=シーケンス(制御),
     H=分電盤の結線図, -DCT=ロケータ採番済
  人手  : <製番>-*.txt（配線情報リスト cp932）
  製番 = 先頭3ハイフン（例 1-12188-117）。

使い方:
  from wireharness.fromto_qc import regression
  rows, avg = regression.run('/path/to/data_dir', train_dir='/path/to/harness_txts')
  print(regression.to_table(rows, avg))
  # CLI: python -m wireharness.fromto_qc.regression <data_dir> [train_dir]
"""
import os
import re
import glob
import collections
from .geometry import norm, DrawingModel
from . import harness, logical_check as lc
from . import bus_rules, locator
from .layout import Layout

_NAME = re.compile(r'(\d+-\d+-\d+)-([A-Za-z]\d+)(-DCT)?\.dxf$', re.I)


def _index(data_dir):
    """data_dir を走査して 製番→(DXF一覧, 人手txt一覧) を返す。"""
    dxf = collections.defaultdict(list)
    txt = collections.defaultdict(list)
    for f in glob.glob(os.path.join(data_dir, '**', '*.dxf'), recursive=True) + \
            glob.glob(os.path.join(data_dir, '**', '*.DXF'), recursive=True):
        m = _NAME.search(os.path.basename(f))
        if m:
            dxf[m.group(1)].append((m.group(2).upper(), bool(m.group(3)), f))
    for f in glob.glob(os.path.join(data_dir, '**', '*.txt'), recursive=True):
        m = re.match(r'(\d+-\d+-\d+)', os.path.basename(f))
        if m:
            txt[m.group(1)].append(f)
    return dxf, txt


def _inputs(files):
    """製番の DXF一覧 → (seq, skel, layout, dct, kind)。制御盤=F/E/D, 分電盤=H/G。"""
    def codes(pfx):
        return [f for c, d, f in files if c.startswith(pfx) and not d]
    seq, skel = codes('F'), codes('E')
    layout = (codes('D') + codes('G'))
    dct = [f for c, d, f in files if d]
    kind = '制御盤'
    if not seq and not skel:                 # 分電盤: H系結線図
        seq, kind = codes('H'), '分電盤'
    return seq, skel, (layout[0] if layout else None), dct, kind


def _human(paths):
    out = {}
    for p in paths:
        for g, d in lc.human_ctrl_nets_by_gousen(p).items():
            out.setdefault(g, set()).update(d)
    return out


def measure_seiban(files, human_paths, templates=None):
    """1製番の recall を計測。戻り: dict(seiban種別なし)。"""
    seq, skel, layout_path, dct, kind = _inputs(files)
    lay = None
    if layout_path:
        try:
            lay = Layout(layout_path)
        except Exception:
            lay = None
    # ロケータ別名（-DCT優先・無ければ位置ベース）
    try:
        amap = locator.alias_map(layout=lay, dct_paths=dct)
    except Exception:
        amap = {}
    fused = harness.fuse(seq, skel, layout_path)
    draw = lc.drawing_nets_by_gousen(fused, aliases=amap)
    # 母線生成を加える
    try:
        models = [DrawingModel(p) for p in seq + skel]
        for g, mem in bus_rules.generate(models, templates=templates, precision=True).items():
            gg = norm(g)
            draw.setdefault(gg, set()).update(
                {norm(d + ('-' + n if n else '')) for d, n, t in mem})
    except Exception:
        pass
    hum = _human(human_paths)
    alias, _ = lc.auto_alias(draw, hum)
    m = lc.logical_match(draw, hum, alias)
    return {'kind': kind, 'gousen_recall': m['gousen_recall%'],
            'dev_recall': m['dev_recall%'], 'n_human': len(hum), 'n_draw': len(draw)}


def run(data_dir, train_dir=None):
    """data_dir 全製番の recall を計測。train_dir を渡すと母線テンプレを学習。
    戻り: (rows, averages)。rows=[(製番, kind, 号線recall, 端子recall, 人手号線, 図面号線)]。"""
    templates = None
    if train_dir:
        try:
            templates = bus_rules.learn(sorted(glob.glob(os.path.join(train_dir, '*.txt'))))
        except Exception:
            templates = None
    dxf, txt = _index(data_dir)
    rows = []
    for seiban in sorted(dxf):
        if not txt.get(seiban):
            continue
        try:
            r = measure_seiban(dxf[seiban], txt[seiban], templates)
            rows.append((seiban, r['kind'], r['gousen_recall'], r['dev_recall'],
                         r['n_human'], r['n_draw']))
        except Exception as e:
            rows.append((seiban, '?', None, None, 0, 0))
    # 種別別平均
    avg = {}
    for kind in ('制御盤', '分電盤'):
        vals = [(g, d) for _, k, g, d, _, _ in rows if k == kind and g is not None]
        if vals:
            avg[kind] = (sum(v[0] for v in vals) / len(vals),
                         sum(v[1] for v in vals) / len(vals), len(vals))
    return rows, avg


def to_table(rows, avg):
    L = [f"{'製番':<16}{'種別':<6}{'号線recall':>10}{'端子recall':>10}{'人手':>6}{'図面':>6}",
         '-' * 56]
    for seiban, kind, g, d, nh, nd in rows:
        if g is None:
            L.append(f"{seiban:<16}{kind:<6}{'ERR':>10}")
        else:
            L.append(f"{seiban:<16}{kind:<6}{g:>9.0f}%{d:>9.0f}%{nh:>6}{nd:>6}")
    L.append('-' * 56)
    for kind, (g, d, n) in avg.items():
        L.append(f"{kind} 平均: 号線 {g:.0f}%  端子 {d:.0f}%  (n={n})")
    return '\n'.join(L)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m wireharness.fromto_qc.regression <data_dir> [train_dir]")
        sys.exit(1)
    rows, avg = run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(to_table(rows, avg))
