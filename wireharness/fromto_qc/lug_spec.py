# -*- coding: utf-8 -*-
"""ラグバー基準(社内標準 FS-5C-002)のコード化。太物寸法(⑥)支援・穴数算出。

資料(docs/references/ラグバーの基準_FS-5C-002.pdf)より:
  ・ラグバーの容量は主幹MCCBのトリップ容量(主幹が無ければ外線電流)で決める(表2.1)。
  ・穴は〔主幹2次1〕＋〔分岐=回路数〕＋〔盤間渡り1(渡りがある時)〕で構成され、
    回路数に合わせて横寸法が増減する(注2.1)。→穴数は固定でなく回路数で決まる。
    ∴「主幹→分岐ブレーカ直結」は穴数の機械的ルールにできない=判断項目(現場回答と一致)。
  ・25mmピッチのため60sq以上の圧着端子は隣接不可=1個おきに取付(注3.2.4)。
  ・寸法(P/L/a〜/塩ビ板L)は電線サイズ・銅帯サイズで決まる(表2.2/2.3/3.x.2)。

本モジュールは標準の表を素直に引く純関数群。値は資料の転記で、test_lug_spec.py で検証する。
50×8(600A・8頁中の後半)は本実装では容量選定のみ(ねじ表は未収録)。
"""

# 表2.1 ラグ端子容量(A) → (銅帯サイズ, 必要スペース 横×縦 mm)。容量の昇順。
CAP_BAR = [
    (30,  '15×3', (200, 200)),
    (50,  '15×3', (200, 200)),
    (100, '15×3', (200, 200)),
    (225, '20×6', (200, 250)),
    (300, '30×6', (250, 300)),
    (400, '30×8', (350, 400)),
    (600, '50×8', (400, 500)),
]

# 表2.2 銅帯サイズ → P寸法(相間, mm)
BAR_PITCH = {'15×3': 60, '20×6': 70, '30×6': 80, '30×8': 80, '50×8': 100}

# 表2.3 電線サイズ(sq) → 塩ビ板 L寸法(mm) ※盤間渡り電線がある場合
COVER_L = {14: 60, 22: 60, 38: 70, 60: 80, 100: 100, 150: 110, 200: 150, 250: 150, 325: 150}

# 穴明け基準(3章) bar → 最低L(mm, 分岐3回路以上)・分岐穴ピッチb(mm)・2列か・注記・渡り線上限sq
HOLE = {
    '15×3': {'min_L': 125, 'pitch_b': 25, 'double_row': False, 'watari_max_sq': None,
             'note': '100AF以下。'},
    '20×6': {'min_L': 125, 'pitch_b': 25, 'double_row': False, 'watari_max_sq': 100,
             'note': '225A以下。60sq以上の圧着端子は1個おきに取付(注3.2.4)。渡り線はJIS規格端子で100sqまで。'},
    '30×6': {'min_L': 140, 'pitch_b': None, 'double_row': True, 'watari_max_sq': 150,
             'note': '300A以下。分岐穴は2列。渡り線は150sqまで。'},
    '30×8': {'min_L': 140, 'pitch_b': None, 'double_row': True, 'watari_max_sq': 200,
             'note': '400A以下。分岐穴は2列。渡り線は200sqまで。'},
}

# ねじ表(表3.x.1) bar → 電線sq → {'main':(穴,ねじ), 'branch':(..), 'watari':(..)}
# main=主幹2次(MX1/X1) branch=分岐(MY/Z) watari=盤間渡り(MX2/X2)。'-'=該当無し。
_N = ('-', '-')
SCREW = {
    '15×3': {   # 表3.1.1
        8:   {'main': ('M5', 'M5ねじ'),      'branch': ('M5', 'M5ねじ'),   'watari': ('M5', 'M5ねじ')},
        14:  {'main': ('M6', 'M6ねじ'),      'branch': ('M6', 'M6ねじ'),   'watari': ('M6', 'M6ねじ')},
        22:  {'main': ('M6', 'M6ねじ'),      'branch': ('M6', 'M6ねじ'),   'watari': ('M6', 'M6ねじ')},
        38:  {'main': ('φ9', 'M8スタッド'),  'branch': ('M6', 'M6ねじ'),   'watari': ('φ9', 'M8スタッド')},
    },
    '20×6': {   # 表3.2.1
        8:   {'main': _N,                 'branch': ('M5', 'M5ねじ'),      'watari': ('M5', 'M5ねじ')},
        14:  {'main': _N,                 'branch': ('M6', 'M6ねじ'),      'watari': ('M6', 'M6ねじ')},
        22:  {'main': _N,                 'branch': ('M6', 'M6ねじ'),      'watari': ('M6', 'M6ねじ')},
        38:  {'main': ('M8', 'M8ボルト'),  'branch': ('M8', 'M8ボルト'),    'watari': ('M8', 'M8ボルト')},
        60:  {'main': ('M8', 'M8ボルト'),  'branch': ('M8', 'M8ボルト'),    'watari': ('M8', 'M8ボルト')},
        100: {'main': ('M8', 'M8スタッド'),'branch': ('M8', 'M8スタッド'),  'watari': ('M8', 'M8スタッド')},
        150: {'main': ('M8', 'M8スタッド'),'branch': _N,                   'watari': _N},
    },
    '30×6': {   # 表3.3.1
        8:   {'main': _N,                  'branch': ('M5', 'M5ねじ'),       'watari': ('M5', 'M5ねじ')},
        14:  {'main': _N,                  'branch': ('M6', 'M6ねじ'),       'watari': ('M6', 'M6ねじ')},
        22:  {'main': _N,                  'branch': ('M6', 'M6ねじ'),       'watari': ('M6', 'M6ねじ')},
        38:  {'main': _N,                  'branch': ('M8', 'M8ボルト'),     'watari': ('M8', 'M8ボルト')},
        60:  {'main': _N,                  'branch': ('M8', 'M8ボルト'),     'watari': ('M8', 'M8ボルト')},
        100: {'main': ('M10', 'M10スタッド'),'branch': ('M10', 'M10スタッド'),'watari': ('M10', 'M10スタッド')},
        150: {'main': ('M10', 'M10スタッド'),'branch': ('M10', 'M10スタッド'),'watari': ('M10', 'M10スタッド')},
    },
    '30×8': {   # 表3.4.1
        8:   {'main': _N,                  'branch': ('M5', 'M5ねじ'),       'watari': ('M5', 'M5ねじ')},
        14:  {'main': _N,                  'branch': ('M6', 'M6ねじ'),       'watari': ('M6', 'M6ねじ')},
        22:  {'main': _N,                  'branch': ('M6', 'M6ねじ'),       'watari': ('M6', 'M6ねじ')},
        38:  {'main': _N,                  'branch': ('M8', 'M8ボルト'),     'watari': ('M8', 'M8ボルト')},
        60:  {'main': _N,                  'branch': ('M8', 'M8ボルト'),     'watari': ('M8', 'M8ボルト')},
        100: {'main': _N,                  'branch': ('M10', 'M10スタッド'),  'watari': ('M10', 'M10スタッド')},
        150: {'main': ('M10', 'M10スタッド'),'branch': ('M10', 'M10スタッド'),'watari': ('M10', 'M10スタッド')},
        200: {'main': ('M12', 'M12スタッド'),'branch': ('M12', 'M12スタッド'),'watari': ('M12', 'M12スタッド')},
        250: {'main': ('M12', 'M12スタッド'),'branch': _N,                   'watari': _N},
    },
}

_SQ_STEPS = (8, 14, 22, 38, 60, 100, 150, 200, 250, 325)


def select_bar(amp):
    """主幹トリップ容量(A) → 銅帯サイズ。表2.1で amp 以上の最小容量の行を採る。
    600A 超は最大(50×8)を返す。"""
    for cap, bar, _space in CAP_BAR:
        if amp <= cap:
            return bar
    return CAP_BAR[-1][1]


def required_space(amp):
    """主幹容量(A) → 必要スペース(横, 縦)mm(表2.1)。回路数で横は増減する点に注意(注2.1)。"""
    for cap, _bar, space in CAP_BAR:
        if amp <= cap:
            return space
    return CAP_BAR[-1][2]


def bar_pitch(bar):
    """銅帯サイズ → 相間P寸法(mm, 表2.2)。"""
    return BAR_PITCH.get(bar)


def _sq_key(sq):
    """与えた電線sqを、表の区分(2〜8は8に丸め、以降は該当段)に対応付ける。"""
    sq = float(sq)
    if sq <= 8:
        return 8
    for s in _SQ_STEPS:
        if sq <= s:
            return s
    return _SQ_STEPS[-1]


def cover_L(sq):
    """電線サイズ(sq) → 塩ビ板L寸法(mm, 表2.3)。盤間渡り電線がある場合に用いる。"""
    k = _sq_key(sq)
    # 表2.3は14sq以上。2〜8sqは14sq相当(60)を下限として返す。
    return COVER_L.get(k, 60 if k <= 14 else COVER_L[325])


def screw(bar, sq, position):
    """(銅帯, 電線sq, 位置'main'/'branch'/'watari') → (穴, ねじ種別)。資料に無ければ None。
    位置: main=主幹2次, branch=分岐, watari=盤間渡り。"""
    tbl = SCREW.get(bar)
    if not tbl:
        return None
    k = _sq_key(sq)
    row = tbl.get(k)
    if row is None:            # 表の最小段より小さい/大きい時は近い段
        avail = sorted(tbl)
        k = min(avail, key=lambda a: abs(a - k))
        row = tbl[k]
    return row.get(position)


def hole_layout(bar, n_branch, watari=False):
    """銅帯サイズ・分岐回路数・盤間渡り有無 → 穴構成。
    戻り: {'bar','holes','主幹穴','分岐穴','渡り穴','min_L','double_row','pitch_b','note',
           'every_other_60sq'}。
    穴数 = 主幹2次1 + 分岐 n + 渡り(渡りがある時1)。"""
    h = HOLE.get(bar, {})
    watari_holes = 1 if watari else 0
    holes = 1 + int(n_branch) + watari_holes
    return {
        'bar': bar,
        'holes': holes,
        '主幹穴': 1,
        '分岐穴': int(n_branch),
        '渡り穴': watari_holes,
        'min_L': h.get('min_L'),
        'double_row': h.get('double_row', False),
        'pitch_b': h.get('pitch_b'),
        'watari_max_sq': h.get('watari_max_sq'),
        'every_other_60sq': bar == '20×6',   # 注3.2.4(20×6のみ単列25mmピッチ)
        'note': h.get('note', ''),
    }


def spec_for(amp, n_branch, watari=False, branch_sq=8):
    """主幹容量・分岐数から、採用銅帯とその穴構成・分岐ねじ・P寸法をまとめて返す(太物寸法支援)。
    branch_sq: 分岐電線の代表サイズ(ねじ選定用)。"""
    bar = select_bar(amp)
    lay = hole_layout(bar, n_branch, watari=watari)
    lay['bar_pitch_P'] = bar_pitch(bar)
    lay['分岐ねじ'] = screw(bar, branch_sq, 'branch')
    lay['主幹容量A'] = amp
    return lay


def count_branches(draw_paths, load_factor=None):
    """図面群 → 細物分岐の回路数を数える(tb_phase利用)。戻り: {'細物':n, '太物':m, '回路':[...]}。
    ラグバーの分岐穴数は細物分岐の数に相当する(主幹・太物は別)。"""
    from . import tb_phase
    pm = tb_phase.build_phase_map(draw_paths, load_factor=load_factor)
    futo = [c for c, i in pm.items() if i.get('futo')]
    hoso = [c for c, i in pm.items() if not i.get('futo')]
    return {'細物': len(hoso), '太物': len(futo),
            '細物回路': sorted(hoso, key=lambda c: (len(c), c)),
            '太物回路': sorted(futo, key=lambda c: (len(c), c))}
