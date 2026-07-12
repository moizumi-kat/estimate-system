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


def best_terminal_block(cluster_points, terminal_blocks, mode='manhattan'):
    """既存端子台の中から、cluster_points 全点への総線長が最小の端子台を選ぶ。
    terminal_blocks: {端子台名:(x,y,z)}。戻り: (名前, 位置, 総線長) or None。"""
    if not terminal_blocks:
        return None
    best = None
    for name, pos in terminal_blocks.items():
        s = sum(dist3(pos, p, mode) for p in cluster_points)
        if best is None or s < best[2]:
            best = (name, pos, s)
    return best


def optimal_wiring(net_devices, positions, terminal_blocks=None, max_per_node=2, mode='manhattan'):
    """機器to機器の最適配線を返す。中継は『図面にある既存端子台』から選ぶ。
    net_devices: この号線に接続する機器名リスト。
    positions: {機器名:(x,y,z)}。
    terminal_blocks: {端子台名:(x,y,z)} 図面に配置済みの端子台。中継候補。
    戻り: {"wires":[(a,b,length)], "relays":[{"tb":名, "pos":(x,y,z), "to":[...]}], "total":総長}
    2本/端子制約を超える機器があれば、既存端子台の中で線長最小のものを中継に使う。
    """
    nodes = {d: positions[d] for d in net_devices if d in positions}
    if len(nodes) < 2:
        return {"wires": [], "relays": [], "total": 0.0, "note": "位置不明or2点未満"}
    edges = mst_edges(nodes, mode)
    deg = {}
    for a, b, _ in edges:
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    relays = []
    over = [n for n, k in deg.items() if k > max_per_node]
    for n in over:
        nbrs = [b for a, b, _ in edges if a == n] + [a for a, b, _ in edges if b == n]
        cluster = [nodes[n]] + [nodes[x] for x in nbrs]
        tb = best_terminal_block(cluster, terminal_blocks, mode)
        if tb:
            relays.append({"tb": tb[0], "pos": tuple(round(v, 1) for v in tb[1]), "to": [n] + nbrs})
        else:
            # 端子台情報が無い場合のみ幾何中央（図面端子台の指定を促す）
            relays.append({"tb": None, "pos": tuple(round(v, 1) for v in geometric_median(cluster)),
                           "to": [n] + nbrs, "note": "既存端子台の位置を渡してください"})
    total = sum(l for _, _, l in edges)
    note = "全機器2本以内" if not over else ("既存端子台を中継に使用" if terminal_blocks else "中継要・端子台未指定")
    return {"wires": [(a, b, round(l, 1)) for a, b, l in edges],
            "relays": relays, "total": round(total, 1), "note": note}
