# -*- coding: utf-8 -*-
"""主回路のルール生成（ステップ③・主回路）。

主回路（3相/単相の引き込み→遮断器→負荷）は、図面では相ラインで描かれ
端子1〜6がTB端子として抽出できない。しかし標準化されているため、
遮断器リストから決定論で生成する（見積システムの分岐回路コード方式と同じ考え）。

各電力遮断器につき:
  母線(R/S/T等) → 遮断器 入力端子(3φ=1/3/5, 1φ=1/3) … LUG（大銅端子）
  遮断器 出力端子(3φ=2/4/6, 1φ=2/4) → 負荷/TB

遮断器リストは本番では器具表/スケルトンから取得（見積システムの抽出を流用）。
"""
IN_3P = ['1', '3', '5']
OUT_3P = ['2', '4', '6']
IN_1P = ['1', '3']
OUT_1P = ['2', '4']
PHASE_COLOR = {'1': '赤', '3': '白', '5': '青', '2': '赤', '4': '白', '6': '青'}


def generate_main(breakers, source='母線', size='HIV'):
    """breakers: [{'device','no','phases'(3|1),'load'(dev,no) or None,'size'}]
    → 主回路の物理電線行（harness_sheet風）。入力端子はLUG対象になる。
    """
    rows = []
    for b in breakers:
        dev, no = b['device'], b.get('no', '')
        n = b.get('phases', 3)
        ins = IN_3P if n == 3 else IN_1P
        outs = OUT_3P if n == 3 else OUT_1P
        wsize = b.get('size', '')
        # 引き込み → 入力端子（LUG）
        for it in ins:
            rows.append({'gousen': f"{no}{PHASE_COLOR.get(it,'')}", 'size': wsize, 'wtype': size,
                         'from': (source, '', PHASE_COLOR.get(it, '')),
                         'to': (dev, no, it),
                         'door_from': False, 'door_to': False, 'marker': ''})
        # 出力端子 → 負荷
        load = b.get('load')
        if load:
            for ot in outs:
                rows.append({'gousen': f"{no}{PHASE_COLOR.get(ot,'')}", 'size': wsize, 'wtype': size,
                             'from': (dev, no, ot),
                             'to': (load[0], load[1] if len(load) > 1 else '', PHASE_COLOR.get(ot, '')),
                             'door_from': False, 'door_to': False, 'marker': ''})
    return rows


def breakers_from_harness(paths):
    """検証用: 人手ハーネスから電力遮断器リストを推定（LUGが繋がる入力端子で相数判定）。
    本番ではこの関数の代わりに器具表/スケルトンから遮断器リストを作る。"""
    import collections

    def C(c, i):
        return c[i].strip() if len(c) > i else ''
    inputs = collections.defaultdict(set)   # (dev,no) -> {入力端子}
    for p in paths:
        lines = open(p, 'rb').read().decode('cp932', errors='replace').splitlines()
        for j, l in enumerate(lines):
            c = l.split('\t')
            if C(c, 5) == 'LUG' and j + 1 < len(lines):
                nx = lines[j + 1].split('\t')
                dev, no, term = C(nx, 5), C(nx, 6), C(nx, 7)
                if dev in ('MCCB', 'ELCB', 'CP', 'ELB') and term in ('1', '3', '5'):
                    inputs[(dev, no)].add(term)
    breakers = []
    for (dev, no), terms in inputs.items():
        phases = 3 if len(terms) >= 3 or '5' in terms else 2 if len(terms) == 2 else 1
        breakers.append({'device': dev, 'no': no, 'phases': 3 if phases == 3 else 1})
    return breakers
