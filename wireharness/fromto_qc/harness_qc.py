# -*- coding: utf-8 -*-
"""ハーネス化のための検図（harness-readiness QC）。

目的: 「この図面をハーネスFrom-Toに変換するには、どこを直せばよいか」を具体箇所つきで指摘する。
ハーネス生成を止めている原因＝結線を号線で一意にグループ化できない箇所、に絞って検出する。
公式作図仕様(OFFICIAL_ANALYSIS_SPEC)の接続機構(L_OUTSIDE/_LU*/_crossPoint1)に準拠。

検出項目（ハーネス化ブロッカー）:
  H1 号線欠落     : 機器2つ以上が繋がるネットに号線が無い → この電線に番号が付けられない
  H2 孤立端子     : 機器の端子がどの配線にも繋がらない → 結線漏れ/端子ずれ
  H3 号線分離     : 同一号線が非連結の複数ネットに存在 → 線番重複 or 結線漏れ
  H4 号線混在     : 1つのネットに異なる号線が複数 → 号線の取り違え/T字漏れ
  H5 外部線中継漏れ: L_OUTSIDE のネットが端子台(_LU*)経由で内部に繋がっていない
"""
import collections
from .geometry import pt_seg_dist, UF, POINT_TOL, TERMINAL_TOL, norm


def _components(model, tol=8):
    """公式仕様の接続機構で電線を連結成分化し、成分ごとに端子・機器・号線・外部フラグを付ける。"""
    segs = model.segments
    def qn(p): return (round(p[0] / tol) * tol, round(p[1] / tol) * tol)
    uf = UF()
    for a, b in segs:
        uf.union(qn(a), qn(b))
    nodes = set()
    for a, b in segs:
        nodes.add(a); nodes.add(b)
    for (px, py) in list(nodes):
        for (ax, ay), (bx, by) in segs:
            if (px, py) in ((ax, ay), (bx, by)):
                continue
            d, t = pt_seg_dist(px, py, ax, ay, bx, by)
            if d <= tol and 0.02 < t < 0.98:
                uf.union(qn((px, py)), qn((ax, ay)))
    for cx, cy, ctol in model._junction_points():
        near = [p for p in nodes if abs(p[0] - cx) <= ctol and abs(p[1] - cy) <= ctol]
        for p in near[1:]:
            uf.union(qn(near[0]), qn(p))
    nlist = list(nodes)
    comp = collections.defaultdict(lambda: {'terms': [], 'devices': set(), 'senban': [],
                                            'outside': False, 'xy': None})
    # 外部(L_OUTSIDE)セグメントの成分を記録
    for e in model.msp:
        if e.dxftype() == 'LINE' and e.dxf.layer == 'L_OUTSIDE':
            p = (round(e.dxf.start.x, 1), round(e.dxf.start.y, 1))
            if p in nodes:
                comp[uf.find(qn(p))]['outside'] = True
    # 端子割付
    for t in model.terminals:
        if not nlist:
            break
        nn = min(nlist, key=lambda n: (n[0] - t.x) ** 2 + (n[1] - t.y) ** 2)
        if abs(nn[0] - t.x) + abs(nn[1] - t.y) <= tol * 3:
            r = uf.find(qn(nn))
            comp[r]['terms'].append(t)
            comp[r]['devices'].add(t.device)
            if comp[r]['xy'] is None:
                comp[r]['xy'] = (t.x, t.y)
    # 号線割付（ラベル位置の最寄りノードの成分へ）
    for (v, x, y), k in zip(model.senban, model.senban_kind):
        if not nlist:
            break
        nn = min(nlist, key=lambda n: (n[0] - x) ** 2 + (n[1] - y) ** 2)
        comp[uf.find(qn(nn))]['senban'].append(v)
        if comp[uf.find(qn(nn))]['xy'] is None:
            comp[uf.find(qn(nn))]['xy'] = (x, y)
    return uf, qn, comp


def check(model, tol=8):
    """ハーネス化ブロッカーを検出。戻り: [{'id','severity','xy','message'}...]"""
    uf, qn, comp = _components(model, tol)
    issues = []
    # H1 号線欠落 / H4 号線混在
    for r, c in comp.items():
        ndev = len(c['devices'])
        sset = set(s for s in c['senban'] if s)
        if ndev >= 2 and not sset:
            issues.append({'id': 'H1', 'severity': 'high', 'xy': c['xy'],
                           'message': f"号線欠落: 機器 {sorted(c['devices'])} が繋がるネットに号線が無い"})
        if len(sset) >= 2:
            issues.append({'id': 'H4', 'severity': 'med', 'xy': c['xy'],
                           'message': f"号線混在: 1ネットに号線 {sorted(sset)}（T字漏れ/取り違え疑い）"})
    # H3 号線分離（同一号線が非連結の複数成分に）
    sid_comps = collections.defaultdict(set)
    for r, c in comp.items():
        for s in set(c['senban']):
            if s:
                sid_comps[s].add(r)
    for s, roots in sid_comps.items():
        if len(roots) >= 2:
            xy = next((comp[r]['xy'] for r in roots if comp[r]['xy']), None)
            issues.append({'id': 'H3', 'severity': 'med', 'xy': xy,
                           'message': f"号線分離: 号線 {s} が非連結の {len(roots)} ネットに存在（重複/結線漏れ疑い）"})
    # H2 孤立端子（どの成分にも入らない端子＝単独）
    assigned = set()
    for c in comp.values():
        for t in c['terms']:
            assigned.add(id(t))
    for t in model.terminals:
        if id(t) not in assigned:
            issues.append({'id': 'H2', 'severity': 'high', 'xy': (t.x, t.y),
                           'message': f"孤立端子: {t.device}:{t.name} がどの配線にも繋がっていない"})
    # H5 外部線中継漏れ（L_OUTSIDE成分に内部機器が無い＝端子台で内部に繋がっていない）
    for r, c in comp.items():
        if c['outside'] and len(c['devices']) < 2:
            issues.append({'id': 'H5', 'severity': 'med', 'xy': c['xy'],
                           'message': "外部線中継漏れ: L_OUTSIDE のネットが端子台経由で内部に繋がっていない疑い"})
    return issues


def summarize(issues):
    c = collections.Counter(i['id'] for i in issues)
    return dict(c)
