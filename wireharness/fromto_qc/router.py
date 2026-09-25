# -*- coding: utf-8 -*-
"""ダクト網を通る配線ルート決定（優先度選択: 配線長 / ダクト容量）。

内部配置図のダクト(DCT)を水平/垂直の格子網としてグラフ化し、各電線の
From-To をダクト経由でルーティングする。優先度を選んで最適化:
  priority='length'   … 総配線長を最小化（各電線を最短経路）
  priority='capacity' … ダクト容量に収める（混雑ダクトを避けて分散）

ダクト容量: 各ダクトの断面積に対し、通す電線の断面積合計で占有率を管理。
容量優先では占有率が高いダクトの通過コストを上げ、迂回して分散させる。
"""
import heapq
import math

# 電線サイズ(sq)→概略断面積(mm2, 被覆込みの占有係数)。運用で調整可。
WIRE_AREA = {'1.25': 3.5, '2': 5, '3.5': 8, '5.5': 12, '8': 16, '14': 24, '22': 34, '38': 52}
DEFAULT_AREA = 5.0

# ダクト種別 → 有効断面積(mm2)と許容占有率。制御盤/分電盤は ID36-C-20 / ID38-C-20 を使用。
# ※ area / fill は社内基準の実値に差し替える（下記は暫定・要・茂泉様の基準値）。
#   capacity = area * fill = 配線に使える実効容量。
DUCT_TYPES = {
    'ID36-C-20': {'area': None, 'fill': None, 'w': 36, 'note': '要社内基準値'},
    'ID38-C-20': {'area': None, 'fill': None, 'w': 38, 'note': '要社内基準値'},
}
DEFAULT_DUCT_TYPE = 'ID38-C-20'
FALLBACK_DUCT_CAPACITY = 300.0     # 基準値未設定時の暫定容量


def duct_capacity(duct_type=None):
    """ダクト種別 → 配線に使える実効容量(mm2)。基準値未設定なら暫定値。"""
    t = DUCT_TYPES.get(duct_type or DEFAULT_DUCT_TYPE, {})
    area, fill = t.get('area'), t.get('fill')
    if area and fill:
        return area * fill
    return FALLBACK_DUCT_CAPACITY


def wire_area(size):
    s = str(size).replace('sq', '').strip()
    return WIRE_AREA.get(s, DEFAULT_AREA)


class DuctNetwork:
    """水平ダクト(Y群)×垂直ダクト(X群)の格子網グラフ。"""
    def __init__(self, hducts, vducts, duct_area=300.0):
        self.hy = sorted(set(round(y) for y in hducts))
        self.vx = sorted(set(round(x) for x in vducts))
        self.duct_area = duct_area          # 1ダクトの断面容量(占有可能面積)
        self.nodes = [(x, y) for x in self.vx for y in self.hy]
        self.fill = {}                      # edge(frozenset) -> 占有面積合計
        self.adj = self._build()

    def _build(self):
        adj = {n: [] for n in self.nodes}
        # 水平: 同一 hy で隣接 vx
        for y in self.hy:
            row = [(x, y) for x in self.vx]
            for a, b in zip(row, row[1:]):
                d = abs(a[0] - b[0])
                adj[a].append((b, d)); adj[b].append((a, d))
        # 垂直: 同一 vx で隣接 hy
        for x in self.vx:
            col = [(x, y) for y in self.hy]
            for a, b in zip(col, col[1:]):
                d = abs(a[1] - b[1])
                adj[a].append((b, d)); adj[b].append((a, d))
        return adj

    def entry(self, pt):
        """機器座標 → 最寄りのダクト格子ノード。"""
        if not self.nodes:
            return None
        return min(self.nodes, key=lambda n: abs(n[0] - pt[0]) + abs(n[1] - pt[1]))

    def _penalty(self, a, b, area, priority):
        base = abs(a[0] - b[0]) + abs(a[1] - b[1])
        if priority == 'capacity':
            e = frozenset((a, b))
            used = self.fill.get(e, 0.0)
            over = max(0.0, (used + area) - self.duct_area)
            return base + over * 5.0        # 容量超過を強く忌避（迂回を促す）
        return base

    def route(self, p_from, p_to, area=DEFAULT_AREA, priority='length'):
        """機器→機器をダクト経由でルーティング。戻り: (経路ノード列, 経路長)。"""
        s, t = self.entry(p_from), self.entry(p_to)
        if s is None or t is None:
            return [], abs(p_from[0]-p_to[0]) + abs(p_from[1]-p_to[1])
        # ダイクストラ（優先度でコスト重み変更）
        dist = {s: 0.0}
        prev = {}
        pq = [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == t:
                break
            if d > dist.get(u, 1e18):
                continue
            for v, _w in self.adj[u]:
                nd = d + self._penalty(u, v, area, priority)
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        # 経路復元
        path = []
        u = t
        while u in prev or u == s:
            path.append(u)
            if u == s:
                break
            u = prev[u]
        path.reverse()
        # 占有を加算＋実長（機器→入口＋ダクト経路＋出口→機器）
        length = abs(p_from[0]-s[0]) + abs(p_from[1]-s[1]) + abs(p_to[0]-t[0]) + abs(p_to[1]-t[1])
        for a, b in zip(path, path[1:]):
            length += abs(a[0]-b[0]) + abs(a[1]-b[1])
            e = frozenset((a, b))
            self.fill[e] = self.fill.get(e, 0.0) + area
        return path, length

    def utilization(self):
        """各ダクト区間の占有率（0-1超で過密）。"""
        return {e: round(v / self.duct_area, 2) for e, v in self.fill.items()}


def route_wires(wires, layout, priority='length', duct_type=None):
    """wires: [{'from_pos':(x,y),'to_pos':(x,y),'size':...}] を優先度でルーティング。
    layout: Layout（hducts と、垂直ダクトVX）。duct_type: 'ID36-C-20'/'ID38-C-20'等（容量に反映）。
    戻り: [(wire, path, length)], 総延長, 占有率。"""
    net = DuctNetwork(layout.hducts, getattr(layout, 'vducts', []),
                      duct_area=duct_capacity(duct_type))
    # 容量優先は混雑を避けるため、太い線から先に確定（貪欲）
    order = sorted(range(len(wires)), key=lambda i: -wire_area(wires[i].get('size', '')))
    if priority != 'capacity':
        order = list(range(len(wires)))
    results = [None] * len(wires)
    total = 0.0
    for i in order:
        w = wires[i]
        a = wire_area(w.get('size', ''))
        path, length = net.route(w['from_pos'], w['to_pos'], a, priority)
        results[i] = (w, path, length)
        total += length
    return results, total, net.utilization()
