# -*- coding: utf-8 -*-
"""アップロードされた DXF を検図バケット(seq/skel/layout/table)に自動仕分けする。

検図エンジン(run_check.run)は seq/skel/layout/table の4系統を受け取るが、
現場では図面を種別ラベル無しで束ねてアップロードする。そこで各図面の内容
(レイヤ構成・電線数・確認表マーク)から種別を推定する。ファイル名先頭に
seq_/skel_/layout_/table_ の接頭辞があればそれを優先(人手オーバーライド)。

判定は排他(1図面=1系統)にして、run_check側の二重計上を避ける。
"""
import os
import collections

# 主回路(スケルトン)/制御(シーケンス)を分ける電線レイヤ
_MAIN_LAYERS = {'L_MAIN'}
_CTRL_LAYERS = {'L_CONTROL', 'L_OUTSIDE', 'L_EARTH'}
# 内部配置図(物理レイアウト)を示す幾何レイヤ(ダクト/レール/寸法箱など)
_LAYOUT_HINT = {'DCT', 'RAIL', 'Dim_BoxTool', 'INS_WAKU', 'TEMPLATE'}
# 確認表を示すテキスト
_TABLE_TEXT = ('確認', 'エコ電線', 'ｴｺ電線', '仕様', '要否')
_DOT_CHARS = ('●', '○', '◎', '〇')

_PREFIX = {'seq_': 'seq', 'skel_': 'skel', 'layout_': 'layout',
           'lay_': 'layout', 'table_': 'table', 'hyo_': 'table'}


def _signals(model):
    """DrawingModel から仕分け用シグナルを集計する。"""
    lay = collections.Counter()
    dots = 0
    tabtext = False
    for e in model.msp:
        if e.dxf.hasattr('layer'):
            lay[e.dxf.layer] += 1
        if e.dxftype() == 'TEXT':
            t = (e.dxf.text or '').strip()
            if t in _DOT_CHARS:
                dots += 1
            elif any(k in t for k in _TABLE_TEXT):
                tabtext = True
        elif e.dxftype() == 'MTEXT':
            t = e.text or ''
            if any(k in t for k in _TABLE_TEXT):
                tabtext = True
    main = sum(lay[k] for k in _MAIN_LAYERS)
    ctrl = sum(lay[k] for k in _CTRL_LAYERS)
    layout_hint = sum(lay[k] for k in _LAYOUT_HINT)
    return dict(main=main, ctrl=ctrl, wire=main + ctrl,
                layout_hint=layout_hint, dots=dots, tabtext=tabtext,
                layers=lay)


def classify(model, filename=None):
    """1図面の系統を推定して返す: 'seq' | 'skel' | 'layout' | 'table'。
    ファイル名接頭辞があれば最優先。"""
    if filename:
        base = os.path.basename(filename).lower()
        for pfx, role in _PREFIX.items():
            if base.startswith(pfx):
                return role
    s = _signals(model)
    # 1) 確認表: 確認/仕様テキスト＋●印があり配線が無い
    if s['tabtext'] and s['dots'] >= 3 and s['wire'] == 0:
        return 'table'
    # 2) 内部配置図: 配線が無く物理レイアウト(ダクト/レール等)がある
    if s['wire'] == 0 and s['layout_hint'] >= 3:
        return 'layout'
    # 3) スケルトン: 主回路電線が制御電線以上(容量・接地の検査対象)
    if s['main'] > 0 and s['main'] >= s['ctrl']:
        return 'skel'
    # 4) シーケンス: 制御電線が主体(既定)
    if s['ctrl'] > 0 or s['wire'] > 0:
        return 'seq'
    # 配線もヒントも無い→内容から判断できない
    return 'layout' if s['layout_hint'] > 0 else 'seq'


def classify_files(models):
    """[(filename, DrawingModel), ...] を系統別バケットに仕分けて返す。
    戻り: {'seq':[...], 'skel':[...], 'layout':[...], 'table':[...],
           'map':[(filename, role), ...]}
    layout/table は run_check が1枚ずつ取るため、複数来た場合は先頭を採用。
    """
    buckets = {'seq': [], 'skel': [], 'layout': [], 'table': []}
    mapping = []
    for fn, m in models:
        role = classify(m, fn)
        buckets[role].append(m)
        mapping.append((os.path.basename(fn) if fn else '', role))
    return {
        'seq': buckets['seq'],
        'skel': buckets['skel'],
        'layout': buckets['layout'][0] if buckets['layout'] else None,
        'table': buckets['table'][0] if buckets['table'] else None,
        'layout_all': buckets['layout'],
        'table_all': buckets['table'],
        'map': mapping,
    }
