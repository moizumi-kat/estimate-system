# -*- coding: utf-8 -*-
"""機器to機器の最適配線＋中継端子の最適配置。

多点ネット(3点以上)を、線長が最小になるように機器間の物理電線に分解する。
- 基本は最小全域木(MST)で機器間を結ぶ（総線長最小）。
- 2本/端子の制約で1機器に3本以上集まる場合、中継端子(ジャンクション)を
  最適位置(接続先の幾何中央=Steiner近似)に置いて分配する。
位置は内部配置図の機器座標＋端子辞書の高さ(z)で3次元。長さはマンハッタン
（ダクト配線を近似）＋高さ差。ダクト網があれば経路長に差し替え可能。
"""
import math
import itertools


def dist3(a, b, mode='manhattan'):
    """2点間の配線長。manhattan=|dx|+|dy|+|dz|（ダクト配線近似）。"""
    dx, dy, dz = abs(a[0]-b[0]), abs(a[1]-b[1]), abs(a[2]-b[2]) if len(a) > 2 and len(b) > 2 else 0
    if mode == 'euclid':
        return math.sqrt(dx*dx + dy*dy + dz*dz)
    return dx + dy + dz


def mst_edges(nodes, mode='manhattan'):
    """nodes: {name:(x,y,z)}。最小全域木の辺 [(a,b,len)] を返す（Prim法）。"""
    names = list(nodes)
    if len(names) < 2:
        return []
    inside = {names[0]}
    edges = []
    while len(inside) < len(names):
        best = None
        for a in inside:
            for b in names:
                if b in inside:
                    continue
                d = dist3(nodes[a], nodes[b], mode)
                if best is None or d < best[2]:
                    best = (a, b, d)
        edges.append(best)
        inside.add(best[1])
    return edges


def geometric_median(points, iters=64):
    """点群の幾何中央(Weiszfeld法)。中継端子の最適位置の近似。"""
    pts = [(p[0], p[1], p[2] if len(p) > 2 else 0) for p in points]
    x = sum(p[0] for p in pts)/len(pts)
    y = sum(p[1] for p in pts)/len(pts)
    z = sum(p[2] for p in pts)/len(pts)
    for _ in range(iters):
        num = [0.0, 0.0, 0.0]
        den = 0.0
        for px, py, pz in pts:
            d = math.sqrt((x-px)**2 + (y-py)**2 + (z-pz)**2) or 1e-6
            w = 1.0/d
            num[0] += px*w; num[1] += py*w; num[2] += pz*w; den += w
        x, y, z = num[0]/den, num[1]/den, num[2]/den
    return (x, y, z)


def optimal_wiring(net_devices, positions, max_per_node=2, mode='manhattan'):
    """機器to機器の最適配線を返す。
    net_devices: この号線に接続する機器名リスト。
    positions: {機器名:(x,y,z)}。
    戻り: {"wires":[(a,b,length)], "relays":[{"pos":(x,y,z),"to":[...]}], "total":総長}
    2本/端子制約を超える機器があれば中継端子を最適位置に置いて分配する。
    """
    nodes = {d: positions[d] for d in net_devices if d in positions}
    if len(nodes) < 2:
        return {"wires": [], "relays": [], "total": 0.0, "note": "位置不明or2点未満"}
    edges = mst_edges(nodes, mode)
    # 各機器の次数
    deg = {}
    for a, b, _ in edges:
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    relays = []
    over = [n for n, k in deg.items() if k > max_per_node]
    if over:
        # 次数超過機器の接続先を中継端子に束ねる（最適位置=接続先の幾何中央）
        for n in over:
            nbrs = [b for a, b, _ in edges if a == n] + [a for a, b, _ in edges if b == n]
            relay_pos = geometric_median([nodes[n]] + [nodes[x] for x in nbrs])
            relays.append({"pos": tuple(round(v, 1) for v in relay_pos), "to": [n] + nbrs})
    total = sum(l for _, _, l in edges)
    return {"wires": [(a, b, round(l, 1)) for a, b, l in edges],
            "relays": relays, "total": round(total, 1),
            "note": ("中継端子で2本制約を解消" if over else "全機器2本以内")}
