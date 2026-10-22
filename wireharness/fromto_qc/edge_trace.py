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
import re
import collections
import math
from .tracer import _build_components, TOL
from . import contact_resolve
from . import device_norm as dn


def _resolve_map(path):
    """接点シンボルの '?' を実番号に置換する地図 {(canon機器, round(x), round(y)): pin}。
    確実な補助リレー単一接点のみ(contact_resolve)。合致しなければ '?' のまま=回帰ゼロ。"""
    out = {}
    try:
        for (dev, x, y), pin in contact_resolve.resolve_points([path], markers=True).items():
            out[(dn.canon(dev), round(x), round(y))] = pin
    except Exception:
        pass
    return out


def _lookup(resmap, dev, x, y):
    """(機器, 座標) → 解決ピン or None。丸め誤差に備え±1の近傍も見る。"""
    cd = dn.canon(dev)
    for dx in (0, 1, -1):
        for dy in (0, 1, -1):
            p = resmap.get((cd, round(x) + dx, round(y) + dy))
            if p is not None:
                return p
    return None


def trace_edges(path, tol=TOL, with_pos=False, control_only=False):
    """シーケンス図 → 渡り辺。with_pos=True で (edges, terms, pos_edges) を返す。
    pos_edges=[((機器,端子,x,y),(機器,端子,x,y))] は同一シート内の物理座標を併記したもの
    (物理位置を正本にするための出力。名前=流儀が違っても位置が合えば同一の配線)。
    control_only=True: 主回路(L_MAIN=太物)を除外し制御(細物)結線のみ辿る(スケルトン統合用)。"""
    c = _build_components(path, control_only=control_only)
    resmap = _resolve_map(path)
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
    node_pos = {}                              # ノード → 代表座標(同一シート内の物理位置)
    all_terms = set()
    for terms in comp_terms.values():
        for (d, t, x, y) in terms:
            if t == '?':                      # 接点シンボルの未番号端子→確実な候補があれば置換
                rp = _lookup(resmap, d, x, y)
                if rp is not None:
                    t = rp
            nd = nearest_node((x, y), tol * 3)
            if nd is not None:
                term_at[nd].add((d, t))
                node_pos[nd] = (round(x, 1), round(y, 1))
                all_terms.add((d, t))

    termnodes = set(term_at)
    # --- 端子ノード間の渡り辺 ---
    # 母線(1つの配線コンポーネントに多数の端子がぶら下がる)は、人の配線では隣接端子へ順に
    # 渡る(デイジーチェーン)。完全グラフ(総当たり k(k-1)/2 本)にすると誤辺が大量に出て精度を
    # 落とすので、コンポーネント内の端子を母線に沿って並べ、隣接のみを結ぶ(k-1 本の鎖)。
    # 2端子は1本、1端子は辺なし。◎誤答ゼロ(候補=確度:中、人の採否前提)。
    comp = {}
    for seed in list(adj) + list(termnodes):
        if seed in comp:
            continue
        comp[seed] = seed
        stack = [seed]
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v not in comp:
                    comp[v] = seed
                    stack.append(v)
    comp_termnodes = collections.defaultdict(list)
    for tn in termnodes:
        comp_termnodes[comp.get(tn, tn)].append(tn)
    def _is_tb_node(n):
        return any(_is_tb(d) for d, t in term_at.get(n, ()))

    def _nearest(n, cands):
        pn = node_pos.get(n)
        if pn is None:
            return cands[0]
        return min(cands, key=lambda c: (node_pos.get(c, (1e9, 1e9))[0] - pn[0]) ** 2
                   + (node_pos.get(c, (1e9, 1e9))[1] - pn[1]) ** 2)

    node_edges = set()
    for members in comp_termnodes.values():
        if len(members) < 2:
            continue
        hubs = [n for n in members if _is_tb_node(n)]
        if hubs and len(members) > 2:
            # ハブ(端子台)がある多点ネット: 各機器はハブへ星形接続(制御ファンアウトは中継端子
            # 経由=現場回答③・実データ検証済)。機器どうしの直結は作らない。ハブ同士は渡り鎖。
            devs = [n for n in members if n not in hubs]
            horder = _rail_order(hubs, node_pos)
            for a, b in zip(horder, horder[1:]):
                node_edges.add(frozenset((a, b)))
            for dnode in devs:
                node_edges.add(frozenset((dnode, _nearest(dnode, hubs))))
            continue
        if len(members) == 2:
            node_edges.add(frozenset(members))
            continue
        order = _rail_order(members, node_pos)       # 母線に沿った順
        for a, b in zip(order, order[1:]):           # 隣接のみ結ぶ(渡り)
            node_edges.add(frozenset((a, b)))
    # --- TBノードの端子名を生成(回路=接続先機器番号, 相=接続先端子) ---
    tb_name = {}
    for node in termnodes:
        if not any(str(d).upper().startswith('TB') for d, t in term_at[node]):
            continue
        name = None
        for ne in node_edges:
            if node not in ne:
                continue
            other = next(iter(ne - {node}))
            for (d, t) in term_at[other]:
                # 実際に描かれた配線でTBに直結する主回路機器(遮断器)からのみ相を付与。
                if not re.match(r'(MCCB|ELCB)', str(d).upper()):
                    continue
                ph = _phase_of(t)
                circ = _circuit_of(d)
                if ph and circ:
                    name = f'{circ}{ph}'
                    break
            if name:
                break
        tb_name[node] = ('TB', name or '?')
        if name:
            all_terms.add(('TB', name))

    def names(node):
        return {tb_name[node]} if node in tb_name else term_at[node]

    edges = set()
    pos_edges = []
    for ne in node_edges:
        a, b = tuple(ne)
        pa, pb = node_pos.get(a), node_pos.get(b)
        for ta in names(a):
            for tb in names(b):
                if ta == tb:
                    continue
                # 面(盤)跨ぎの機器直結は存在しない(④で機器-機器 同面324:面跨ぎ1 で確認)。
                # 面を跨ぐ接続は製造側が渡り専用TB/ファーステンを挿入する(現場回答)。よって
                # 機器どうしで面が異なる直結は、捨てずに『仮の中継TB』を挟んで A→仮TB→B に分割する
                # (台番号は要確認＝人/確認ループ)。◎誤答ゼロ: 中継の存在は検証済み、番号は未確定。
                if not _is_tb(ta[0]) and not _is_tb(tb[0]):
                    fa, fb = _face(ta[0]), _face(tb[0])
                    if fa and fb and fa != fb:
                        prov = ('TB', f'仮{min(fa, fb)}{max(fa, fb)}')   # 面ペアの仮中継TB
                        edges.add(frozenset((ta, prov)))
                        edges.add(frozenset((prov, tb)))
                        if pa and pb:
                            pos_edges.append(((ta[0], ta[1], pa[0], pa[1]),
                                              (prov[0], prov[1], pa[0], pa[1])))
                            pos_edges.append(((prov[0], prov[1], pb[0], pb[1]),
                                              (tb[0], tb[1], pb[0], pb[1])))
                        continue
                edges.add(frozenset((ta, tb)))
                if pa and pb:
                    # 物理位置を正本に: 端子名(流儀)とともに同一シート内の座標を併記。
                    pos_edges.append(((ta[0], ta[1], pa[0], pa[1]),
                                      (tb[0], tb[1], pb[0], pb[1])))
    if with_pos:
        return edges, all_terms, pos_edges
    return edges, all_terms


def _is_tb(sym):
    return str(sym or '').upper().startswith('TB')


def _face(sym):
    """機器記号 → 面(盤)番号。回路番号(3桁)の先頭桁が面を表す(TB台番号の先頭桁＝面とも一致。
    ④の実データで確認)。回路番号が読めなければ ''。"""
    m = re.search(r'(\d{3})', dn.strip_marks(str(sym or '')))
    return m.group(1)[0] if m else ''


def _rail_order(nodes, node_pos):
    """端子ノード群を『母線に沿った順』に並べる。母線の主軸(長辺)方向で射影ソート。
    水平母線→x昇順(同xはy)、垂直母線→y降順(上から下。同yはx)。位置不明は末尾側。"""
    def pos(n):
        return node_pos.get(n, (0.0, 0.0))
    xs = [pos(n)[0] for n in nodes]
    ys = [pos(n)[1] for n in nodes]
    horizontal = (max(xs) - min(xs)) >= (max(ys) - min(ys))
    if horizontal:
        return sorted(nodes, key=lambda n: (pos(n)[0], -pos(n)[1]))
    return sorted(nodes, key=lambda n: (-pos(n)[1], pos(n)[0]))


def _phase_of(term):
    """接続先端子 → TBの相(U/V/W/E)。主回路端子1-6→相, 接地→E, 制御→None。"""
    t = str(term or '').strip().upper()
    t = re.sub(r'\(.*?\)', '', t)
    if re.fullmatch(r'[1-6]', t):
        return ['U', 'V', 'W'][(int(t) - 1) // 2]
    if 'E' in t or t in ('ET',):
        return 'E'
    return None


def _circuit_of(dev):
    m = re.search(r'(\d+)', str(dev or ''))
    return m.group(1) if m else ''
