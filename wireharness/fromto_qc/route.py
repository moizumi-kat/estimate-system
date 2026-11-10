# -*- coding: utf-8 -*-
"""渡り（等電位ノード内の物理配線）を、優先度に応じて最適化する。

背景(茂泉様):
  ・作業性は「端子への繋ぎ込み数」を減らすほど良い。繋ぎ込み数は渡り(ルート)で決まる。
  ・渡りは一意でない(等電位ノードの張り方は自由)ので最適化できる。
  ・台帳(人の手本)は 1端子あたり平均1.1〜1.8本＝ほぼデイジーチェーン(1端子≤2本)。

優先度(priority):
  'connection' … 繋ぎ込み数最小(=デイジーチェーン, 各端子の次数≤2)。作業性最優先。既定。
  'length'     … 総配線長最小(最小全域木 MST)。ただし分岐で次数が増えうる。
  'duct'       … ダクト平準化(将来版: ダクト区間の本数を均す)。現状は connection にフォールバック。

いずれも N端子のノードは N-1本(全域木)で結線＝電線本数・総繋ぎ込み(2(N-1))は不変。
効くのは「1端子あたりの繋ぎ込み数(次数)の分布」と「総配線長」。
"""
import math


def _nn_chain(pts):
    """最近傍デイジーチェーン: 端(x+y最小)から貪欲に最近未訪問へ。次数≤2の道。
    pts: [(key,x,y)] → [(keyA,keyB)] (N端子ならN-1本)。"""
    rem = pts[:]
    start = min(rem, key=lambda t: t[1] + t[2])
    chain = [start]
    rem.remove(start)
    while rem:
        cx, cy = chain[-1][1], chain[-1][2]
        nxt = min(rem, key=lambda t: math.hypot(t[1] - cx, t[2] - cy))
        chain.append(nxt)
        rem.remove(nxt)
    return [(chain[i][0], chain[i + 1][0]) for i in range(len(chain) - 1)]


def _mst(pts):
    """最小全域木(Prim)で総配線長最小。分岐が出るので次数は≥2になりうる。"""
    if len(pts) <= 1:
        return []
    idx = {t[0]: (t[1], t[2]) for t in pts}
    keys = [t[0] for t in pts]
    inside = {keys[0]}
    edges = []
    while len(inside) < len(keys):
        best = None
        for a in inside:
            ax, ay = idx[a]
            for b in keys:
                if b in inside:
                    continue
                d = math.hypot(idx[b][0] - ax, idx[b][1] - ay)
                if best is None or d < best[0]:
                    best = (d, a, b)
        edges.append((best[1], best[2]))
        inside.add(best[2])
    return edges


def optimize_watari(terminals, priority='connection'):
    """等電位ノードの端子群 → 物理配線(端子ペア列)を優先度に応じ生成。
    terminals: [(key, x, y)]。座標の無い端子は除外(渡り生成不可)。
    戻り: [(keyA, keyB)]。"""
    pts = [t for t in terminals if t[1] is not None and t[2] is not None]
    if len(pts) <= 1:
        return []
    if priority == 'length':
        return _mst(pts)
    # 'connection'(既定) / 'duct'(将来) は繋ぎ込み最小=デイジーチェーン
    return _nn_chain(pts)


def degree_of(pairs):
    """端子ペア列 → {端子key: 繋ぎ込み数(次数)}。"""
    deg = {}
    for a, b in pairs:
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    return deg


def route_metrics(node_wires_by_terminals):
    """複数ノードの渡り結果をまとめ、繋ぎ込みの指標を返す。
    node_wires_by_terminals: [ [(key,x,y),...], ... ] ノードごとの端子群。
    戻り: {'電線','総繋ぎ込み','端子数','平均','最大','分布','多重端子(≥3)'}。"""
    import collections
    deg = collections.Counter()
    wires = 0
    for terms in node_wires_by_terminals:
        pairs = optimize_watari(terms, 'connection')
        wires += len(pairs)
        for a, b in pairs:
            deg[a] += 1
            deg[b] += 1
    vals = list(deg.values())
    return {'電線': wires, '総繋ぎ込み': sum(vals), '端子数': len(vals),
            '平均': round(sum(vals) / max(len(vals), 1), 2), '最大': max(vals) if vals else 0,
            '多重端子(≥3)': sum(1 for v in vals if v >= 3),
            '分布': dict(sorted(collections.Counter(vals).items()))}
