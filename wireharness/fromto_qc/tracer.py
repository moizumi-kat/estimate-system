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

    # 号線ラベル → 成分 → 機器集合
    out = collections.defaultdict(set)
    for (v, x, y), k in zip(m.senban, m.senban_kind):
        g = norm(v)
        if not g:
            continue
        c = comp_near((x, y), 200)
        if c is not None:
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
    for (v, x, y), k in zip(m.senban, m.senban_kind):
        g = norm(v)
        if not g:
            continue
        c = comp_near((x, y), 200)
        if c is None:
            continue
        d = out.setdefault(g, {'devices': set(), 'suggest': []})
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
