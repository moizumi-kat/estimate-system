# -*- coding: utf-8 -*-
"""渡り(端子↔端子の直結配線)をセグメント(描かれた線分)レベルで抽出する。

人のハーネス(From-To)の『渡り』は、図面に実際に描かれた1本1本の配線=
「ある端子から、他の端子を挟まずに線分がつながっている次の端子まで」である。
等電位ノード(tracer._build_components)にまとめる手前の線分グラフを使い、
端子ノード間の直結辺を復元する。これが人の渡り分割に対応する。

戻り trace_edges(path): (edges, terms)
  edges = set(frozenset({(dev, term), (dev, term)}))  直結の渡り1本ごと
  terms = set((dev, term))  図面から読めた端子
※ 粒度(辺の本数)は人の渡りと概ね一致。精度は端子の読み取り網羅に依存する。
"""
import collections
import math
from .tracer import _build_components, TOL


def trace_edges(path, tol=TOL):
    c = _build_components(path)
    segs, comp_terms = c['segs'], c['comp_terms']
    pts = []
    for (p1, p2) in segs:
        pts.append((p1[0], p1[1]))
        pts.append((p2[0], p2[1]))
    par = list(range(len(pts)))

    def f(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    grid = collections.defaultdict(list)
    for i, p in enumerate(pts):
        gx, gy = int(p[0] // tol), int(p[1] // tol)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid[(gx + dx, gy + dy)]:
                    if (pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2 < tol * tol:
                        par[f(i)] = f(j)
        grid[(gx, gy)].append(i)

    adj = collections.defaultdict(set)
    for si in range(len(segs)):
        a, b = f(2 * si), f(2 * si + 1)
        if a != b:
            adj[a].add(b)
            adj[b].add(a)

    def nearest_node(p, maxd):
        best, bd = None, maxd
        for i, q in enumerate(pts):
            dd = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
            if dd < bd * bd:
                bd, best = math.sqrt(dd), i
        return f(best) if best is not None else None

    term_at = collections.defaultdict(set)
    all_terms = set()
    for terms in comp_terms.values():
        for (d, t, x, y) in terms:
            nd = nearest_node((x, y), tol * 3)
            if nd is not None:
                term_at[nd].add((d, t))
                all_terms.add((d, t))

    termnodes = set(term_at)
    edges = set()
    for start in termnodes:
        seen = {start}
        stack = [start]
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v in seen:
                    continue
                if v in termnodes and v != start:
                    for ta in term_at[start]:
                        for tb in term_at[v]:
                            if ta != tb:
                                edges.add(frozenset((ta, tb)))
                else:
                    seen.add(v)
                    stack.append(v)
    return edges, all_terms
