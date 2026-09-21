# -*- coding: utf-8 -*-
"""作図規約に忠実な等電位ノード・トレーサ（茂泉様の読み方ルール）。

ルール:
  ① 接続ドット(_crossPoint1)のある交差 = 接続。無い交差 = ただの交差(非接続)。
  ② 線の端点が 別の線/機器端子/端子台ラグ に載る = 接続（T字・継続・端点接続）。
  ③ ドットの無い交差(線が互いに突き抜ける)は接続しない（誤結線防止）。

線は端点どうし・端点→線・ドット交差 でのみ繋ぎ、端子(機器)・端子台ラグを端点として拾う。
戻り: {号線: set(機器名 or 'TB')}
"""
import math
import collections
from .geometry import DrawingModel, norm

TOL = 18
CROSS_DOT = '_crossPoint1'


def _pt_seg(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_ori(a, b):
    return 'V' if abs(a[0] - b[0]) < abs(a[1] - b[1]) else 'H'


def _oriented_senban(m):
    """号線ラベルを向き付きで返す [(号線, x, y, 'V'/'H'/None)]。
    V_SENBAN=縦線ラベル / H_SENBAN=横線ラベル。向きに一致する線だけに対応付けるため。"""
    out = []
    for e in m.msp:
        if e.dxftype() == 'INSERT' and e.dxf.name in ('V_SENBAN', 'H_SENBAN'):
            g = ''
            for at in (e.attribs or []):
                if at.dxf.tag == '線番':
                    g = norm(at.dxf.text or '')
            if g:
                out.append((g, e.dxf.insert.x, e.dxf.insert.y,
                            'V' if e.dxf.name == 'V_SENBAN' else 'H'))
    return out


def _assign_gousen(m, segs, find, max_ori=130, max_any=200):
    """号線ラベル→成分。向きが分かる場合は一致する線のみ(<=max_ori)に対応付け、誤associateを防ぐ。
    向き不明のラベルは従来どおり最近傍(<=max_any)。戻り: {号線: comp}"""
    labs = _oriented_senban(m)
    known = {(g, round(x), round(y)) for g, x, y, o in labs}

    def nearest(p, ori, maxd):
        best, bd = None, maxd
        for i, (a, b) in enumerate(segs):
            if ori and _seg_ori(a, b) != ori:
                continue
            d = _pt_seg(p, a, b)
            if d < bd:
                bd, best = d, i
        return find(best) if best is not None else None

    out = collections.defaultdict(set)
    for g, x, y, ori in labs:
        c = nearest((x, y), ori, max_ori)
        if c is not None:
            out[g].add(c)   # 同一号線が複数箇所に出る場合は全成分を集める
    # 向き情報の無い号線ラベル(m.senban)も補完（向き付きに無いもののみ）
    for (v, x, y), k in zip(m.senban, m.senban_kind):
        g = norm(v)
        if not g or (g, round(x), round(y)) in known or g in out:
            continue
        c = nearest((x, y), None, max_any)
        if c is not None:
            out[g].add(c)
    return out


def _attach_connectors(m, segs, find, comp_dev, tol=70):
    """コネクタ(DEVICE=CP)・場外参照を接続端点として拾う。
    CPはハーネスで場外(別シート/ケーブル)への接続端。線端がCP位置の近くにあれば接続。"""
    conns = []
    for e in m.msp:
        if e.dxftype() != 'INSERT' or not e.attribs:
            continue
        a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
        if a.get('DEVICE', '') == 'CP':
            sym = 'CP' + ('-' + a['DEVICE1'] if a.get('DEVICE1') else '')
            conns.append((sym, e.dxf.insert.x, e.dxf.insert.y))
    if not conns:
        return
    for i, (p1, p2) in enumerate(segs):
        c = find(i)
        for p in (p1, p2):
            for (sym, cx, cy) in conns:
                if math.hypot(p[0] - cx, p[1] - cy) < tol:
                    comp_dev[c].add(sym)


_FRAME_LAYERS = {'TEMPLATE', 'ZUWAKU', 'INS_WAKU', 'TEMPLATE_HIDDEN', 'Defpoints',
                 'B_BOX', 'Dr_BOX', 'S_BOX', 'FA2_FRAME'}


def _attach_boxes(m, segs, find, comp_dev, margin=12):
    """線端が『描かれた四角形(機器・信号ボックス等)』の枠に入る場合、その箱を機器として接続。
    DEVICE属性の無い素の四角形(例: DC4-20mA信号ボックス)を拾う。枠(外形線)や巨大な枠は除外。"""
    boxes = []
    for e in m.msp:
        if e.dxftype() != 'LWPOLYLINE':
            continue
        if e.dxf.layer in _FRAME_LAYERS:
            continue
        pts = [(x, y) for x, y, *_ in e.get_points()]
        # 閉フラグ or 始点≒終点（頂点重複で閉じた矩形）を「閉」とみなす
        closed = bool(e.closed) or (len(pts) >= 4 and
                                    math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 2)
        if not closed or not (4 <= len(pts) <= 6):
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if not (30 < w < 1500 and 8 < h < 1500):
            continue
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    if not boxes:
        return
    ep = []
    for i, (p1, p2) in enumerate(segs):
        c = find(i)
        ep += [(p1[0], p1[1], c), (p2[0], p2[1], c)]
    for (x0, y0, x1, y1) in boxes:
        cx, cy = round((x0 + x1) / 2), round((y0 + y1) / 2)
        for (ex, ey, c) in ep:
            if x0 - margin <= ex <= x1 + margin and y0 - margin <= ey <= y1 + margin:
                comp_dev[c].add(f'BOX@{cx},{cy}')


def _attach_devices_by_box(m, segs, find, comp_dev, margin=15):
    """線端が機器の外形枠に入る/接する場合、その機器を成分に接続（単線図の端子無し機器対策）。
    どの成分か曖昧にならないよう、枠に入る線端が属する成分にのみ付ける。"""
    devs = getattr(m, 'devices', [])
    # segsのインデックス→成分。端点を成分付きで持つ
    ep = []  # (x, y, comp)
    for i, (p1, p2) in enumerate(segs):
        c = find(i)
        ep.append((p1[0], p1[1], c))
        ep.append((p2[0], p2[1], c))
    for d in devs:
        x0, y0, x1, y1 = min(d.box[0], d.box[2]), min(d.box[1], d.box[3]), \
            max(d.box[0], d.box[2]), max(d.box[1], d.box[3])
        for (ex, ey, c) in ep:
            if x0 - margin <= ex <= x1 + margin and y0 - margin <= ey <= y1 + margin:
                comp_dev[c].add(d.sym)


def trace(path, tol=TOL):
    """1シートを規約どおり辿り、号線→機器集合 を返す。"""
    m = DrawingModel(path)
    segs = [(p1, p2) for (p1, p2) in m.segments]
    # 接続ドット
    dots = [(e.dxf.insert.x, e.dxf.insert.y) for e in m.msp
            if e.dxftype() == 'INSERT' and e.dxf.name == CROSS_DOT]

    par = list(range(len(segs)))

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    def uni(a, b):
        par[find(a)] = find(b)

    # ② 端点→端点 / 端点→線（T字・継続）。交差(内部×内部)は繋がない。
    for i, (p1, p2) in enumerate(segs):
        for j in range(i + 1, len(segs)):
            q1, q2 = segs[j]
            if min(_pt_seg(p1, q1, q2), _pt_seg(p2, q1, q2),
                   _pt_seg(q1, p1, p2), _pt_seg(q2, p1, p2)) < tol:
                uni(i, j)

    # ① 接続ドットのある交差だけ、そのドットを通る線を結ぶ
    for d in dots:
        through = [i for i, (a, b) in enumerate(segs) if _pt_seg(d, a, b) < tol]
        for k in range(1, len(through)):
            uni(through[0], through[k])

    # 端子(機器)・端子台ラグ を最寄り線成分へ
    comp_dev = collections.defaultdict(set)

    def comp_near(p, t):
        best, bd = None, t
        for i, (a, b) in enumerate(segs):
            dd = _pt_seg(p, a, b)
            if dd < bd:
                bd, best = dd, i
        return find(best) if best is not None else None

    for t in m.terminals:
        c = comp_near((t.x, t.y), tol * 2.5)
        if c is not None:
            comp_dev[c].add(t.device)
    for (sym, x, y, box) in getattr(m, 'termblocks', []):
        c = comp_near((x, y), tol * 3.5)
        if c is not None:
            comp_dev[c].add('TB')
    # 単線図(主回路)対策: 線端が機器の枠に入る＝その機器に接続（端子ピンが無い機器を拾う）
    _attach_devices_by_box(m, segs, find, comp_dev)
    _attach_connectors(m, segs, find, comp_dev)
    _attach_boxes(m, segs, find, comp_dev)

    # 号線ラベル → 成分（向き一致で誤associate防止） → 機器集合
    out = collections.defaultdict(set)
    for g, comps in _assign_gousen(m, segs, find).items():
        for c in comps:
            out[g] |= comp_dev.get(c, set())
    return dict(out)


def trace_seiban(paths, tol=TOL):
    """複数シートを号線でマージ。戻り {号線: set(機器 or 'TB')}"""
    merged = collections.defaultdict(set)
    for p in paths:
        for g, devs in trace(p, tol=tol).items():
            merged[g] |= devs
    return dict(merged)


def trace_detail(path, tol=TOL, gap_max=95):
    """号線ごとに 結線機器 と『近接ギャップの相手候補』を返す。
    近接ギャップ = 線の端点の近く(安全許容 tol 超〜gap_max)に、まだ繋がっていない機器端子がある
    （＝わずかに届いていないだけで、ほぼ接続）。戻り: {号線: {'devices':set, 'suggest':[(機器:端子, gap)]}}
    """
    m = DrawingModel(path)
    segs = [(p1, p2) for (p1, p2) in m.segments]
    dots = [(e.dxf.insert.x, e.dxf.insert.y) for e in m.msp
            if e.dxftype() == 'INSERT' and e.dxf.name == CROSS_DOT]
    par = list(range(len(segs)))

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    def uni(a, b):
        par[find(a)] = find(b)

    for i, (p1, p2) in enumerate(segs):
        for j in range(i + 1, len(segs)):
            q1, q2 = segs[j]
            if min(_pt_seg(p1, q1, q2), _pt_seg(p2, q1, q2),
                   _pt_seg(q1, p1, p2), _pt_seg(q2, p1, p2)) < tol:
                uni(i, j)
    for d in dots:
        thr = [i for i, (a, b) in enumerate(segs) if _pt_seg(d, a, b) < tol]
        for k in range(1, len(thr)):
            uni(thr[0], thr[k])

    comp_dev = collections.defaultdict(set)
    comp_pts = collections.defaultdict(list)
    for i, (p1, p2) in enumerate(segs):
        comp_pts[find(i)] += [p1, p2]

    def comp_near(p, t):
        best, bd = None, t
        for i, (a, b) in enumerate(segs):
            dd = _pt_seg(p, a, b)
            if dd < bd:
                bd, best = dd, i
        return find(best) if best is not None else None

    for t in m.terminals:
        c = comp_near((t.x, t.y), tol * 2.5)
        if c is not None:
            comp_dev[c].add(t.device)
    for (sym, x, y, box) in getattr(m, 'termblocks', []):
        c = comp_near((x, y), tol * 3.5)
        if c is not None:
            comp_dev[c].add('TB')
    _attach_devices_by_box(m, segs, find, comp_dev)
    _attach_connectors(m, segs, find, comp_dev)
    _attach_boxes(m, segs, find, comp_dev)

    # 近接ギャップ候補: 各成分の端点近く(tol〜gap_max)に、成分外の機器端子があるか
    comp_suggest = collections.defaultdict(list)
    for comp, pts in comp_pts.items():
        own = comp_dev.get(comp, set())
        seen = set()
        for p in pts:
            best = None
            bd = gap_max
            for t in m.terminals:
                if t.device in own:
                    continue
                dd = math.hypot(p[0] - t.x, p[1] - t.y)
                if tol < dd < bd:
                    bd, best = dd, t
            if best is not None:
                key = best.device + (':' + best.name if best.name and best.name != '?' else '')
                if key not in seen:
                    seen.add(key)
                    comp_suggest[comp].append((key, round(bd, 1)))

    out = {}
    for g, comps in _assign_gousen(m, segs, find).items():
        d = out.setdefault(g, {'devices': set(), 'suggest': []})
        for c in comps:
            d['devices'] |= comp_dev.get(c, set())
            d['suggest'] += comp_suggest.get(c, [])
    return out


def trace_seiban_detail(paths, tol=TOL):
    merged = {}
    for p in paths:
        for g, d in trace_detail(p, tol=tol).items():
            m = merged.setdefault(g, {'devices': set(), 'suggest': []})
            m['devices'] |= d['devices']
            m['suggest'] += d['suggest']
    # suggest を距離順・重複除去
    for g, d in merged.items():
        seen = set()
        uniq = []
        for key, gap in sorted(d['suggest'], key=lambda x: x[1]):
            if key in seen:
                continue
            seen.add(key)
            uniq.append((key, gap))
        d['suggest'] = uniq[:3]
    return merged


def is_formed(devs):
    """結線済判定: 非TB機器2つ以上、または 機器1つ＋端子台(device→TB の1本)。"""
    nd = {d for d in devs if d != 'TB'}
    return len(nd) >= 2 or (len(nd) >= 1 and 'TB' in devs)
