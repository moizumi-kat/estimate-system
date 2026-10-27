# -*- coding: utf-8 -*-
"""FromToData を図面上で確認するためのレビュー画面(自己完結HTML)を生成する。

目的(ユーザー要望 2026-10-22):
  各From-To行が『図面のどこからどこへの配線か』を、図面を見ながらハイライト線で確認したい。

仕組み:
  ・シーケンス図(DXF)の線分(LINE/LWPOLYLINE)を薄いグレーで背景描画(=図面)。
  ・edge_trace.trace_edges(..., with_pos=True) の pos_edges は、同一シート内の端点物理座標
    (機器,端子,x,y)×2 を持つ。これを配線ごとのハイライト線として重ねる。
  ・右の一覧で行をクリック/ホバーすると、その配線の端点と線が図面上で強調される。
  図面データは社内資産のため claude.ai に公開せず、ローカルで開く1枚のHTMLに全データを埋め込む。

使い方:
  python3 -m fromto_qc.review_viewer <シートDXF> [出力HTML]
"""
import collections
import json
import os
import sys

import ezdxf

from . import edge_trace
from .tracer import _build_components, TOL, _pt_seg


import math as _math


def _poly_segs(pts, closed, segs):
    for i in range(len(pts) - 1):
        segs.append([pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]])
    if closed and len(pts) > 2:
        segs.append([pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]])


def _entity_segs(e, segs):
    """1エンティティ → 線分群(背景描画用)。円弧/円はポリライン近似。"""
    t = e.dxftype()
    if t == 'LINE':
        segs.append([e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y])
    elif t == 'LWPOLYLINE':
        _poly_segs([(p[0], p[1]) for p in e.get_points()], e.closed, segs)
    elif t == 'POLYLINE':
        try:
            _poly_segs([(v.dxf.location.x, v.dxf.location.y) for v in e.vertices],
                       e.is_closed, segs)
        except Exception:
            pass
    elif t in ('CIRCLE', 'ARC'):
        cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
        if t == 'CIRCLE':
            a0, a1 = 0.0, 360.0
        else:
            a0, a1 = e.dxf.start_angle, e.dxf.end_angle
            if a1 < a0:
                a1 += 360.0
        n = max(6, int(abs(a1 - a0) / 15))
        pts = [(cx + r * _math.cos(_math.radians(a0 + (a1 - a0) * k / n)),
                cy + r * _math.sin(_math.radians(a0 + (a1 - a0) * k / n)))
               for k in range(n + 1)]
        _poly_segs(pts, t == 'CIRCLE', segs)


def _segments(msp):
    """図面の線分を [[x1,y1,x2,y2], ...] で返す(背景描画用)。
    図面の大半はブロック(INSERT=シンボル)内にあるため、INSERTは virtual_entities で
    ワールド座標に展開して線分を拾う(ネストブロックも再帰)。"""
    segs = []
    for e in msp:
        t = e.dxftype()
        if t == 'INSERT':
            try:
                for ve in e.virtual_entities():
                    _entity_segs(ve, segs)
            except Exception:
                pass
        else:
            _entity_segs(e, segs)
    return segs


def _device_labels(msp):
    """機器INSERTの (DEVICE[-DEVICE1], x, y) を返す(図面の向き確認の目印用)。"""
    out = []
    for ins in msp.query('INSERT'):
        if not ins.attribs:
            continue
        a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
        dev = a.get('DEVICE', '')
        if not dev:
            continue
        num = a.get('DEVICE1', '')
        label = f'{dev}-{num}' if num else dev
        out.append([label, round(ins.dxf.insert.x, 1), round(ins.dxf.insert.y, 1)])
    return out


def _hidden_layers(doc):
    """点線(外部)レイヤ名の集合。線種が HIDDEN 系、またはレイヤ名 L_OUTSIDE。
    図面規約: 点線=当社製品でない外部製品(客先手配等)。配線は端子台までが当社範囲。"""
    out = set()
    for ly in doc.layers:
        name = ly.dxf.name
        lt = (ly.dxf.linetype or '').upper()
        if name == 'L_OUTSIDE' or 'HIDDEN' in lt or 'PHANTOM' in lt:
            out.add(name)
    return out


def _external_boxes(msp, hidden):
    """外部製品(点線レイヤ/HIDDEN線種)のINSERT位置 [(x,y), ...]。接点の外部判定に使う。"""
    out = []
    for ins in msp.query('INSERT'):
        lt = (ins.dxf.linetype or '').upper()
        if ins.dxf.layer in hidden or lt == 'HIDDEN' or lt == 'PHANTOM':
            out.append((ins.dxf.insert.x, ins.dxf.insert.y))
    return out


def _senban(msp):
    """線番(SENBAN)ブロックの (線番文字列, x, y) 一覧。
    線番は電線1本の識別子で、同一線番の端子は同一ネット(人のFrom-Toの根拠)。
    外部接続の端子台には線番が端子番号として振られている(図面規約・現場確認)。"""
    out = []
    for ins in msp.query('INSERT'):
        if 'SENBAN' not in ins.dxf.name:
            continue
        a = {at.dxf.tag: (at.dxf.text or '').strip() for at in ins.attribs} if ins.attribs else {}
        val = next((v for v in a.values() if v), '')
        if val:
            out.append((val, ins.dxf.insert.x, ins.dxf.insert.y))
    return out


def senban_nets(sheet_path):
    """線番(SENBAN)ベースでネット(等電位)を構成して返す。
    戻り: [{'線番':名, 'terminals':[(機器,端子,x,y),...]}]。
    等電位コンポーネント(tracer)に線番を割り当て、同一コンポーネントの端子を1ネットとする。
    同一線番の端子は同一ネット(人のFrom-To構成と同じ根拠)。実データ(5-29026)で人のネットと
    機器構成が一致することを確認済み。線番が付かないコンポーネントは 'C<id>' の仮名とする。"""
    comp = _build_components(sheet_path)
    find, segs, comp_terms = comp['find'], comp['segs'], comp['comp_terms']

    def comp_near(px, py, t=TOL * 3.5):
        best, bd = None, t
        for i, (a, b) in enumerate(segs):
            dd = _pt_seg((px, py), a, b)
            if dd < bd:
                bd, best = dd, i
        return find(best) if best is not None else None

    doc = ezdxf.readfile(sheet_path)
    comp_sb = collections.defaultdict(list)
    senban = []
    for ins in doc.modelspace().query('INSERT'):
        if 'SENBAN' not in ins.dxf.name:
            continue
        a = {at.dxf.tag: (at.dxf.text or '').strip() for at in ins.attribs} if ins.attribs else {}
        val = next((v for v in a.values() if v), '')
        if not val:
            continue
        senban.append((val, ins.dxf.insert.x, ins.dxf.insert.y))
        c = comp_near(ins.dxf.insert.x, ins.dxf.insert.y)
        if c is not None:
            comp_sb[c].append(val)

    # 端子台(TB=_LU0001でDEVICE=TB等)をコンポーネントに付与する。tracerに登録されない内部TBが
    # あり、そのせいで『機器→TB渡り』ネット(例102F=52:73+TB)が1端子になって落ちるため。
    terms_by_comp = collections.defaultdict(set)
    for c, s in comp_terms.items():
        terms_by_comp[c] |= set(s)

    def _senban_at(x, y, r=TOL * 3.5):
        best, bd = None, r * r
        for val, sx, sy in senban:
            dd = (x - sx) ** 2 + (y - sy) ** 2
            if dd < bd:
                bd, best = dd, val
        return best
    for ins in doc.modelspace().query('INSERT'):
        a = {at.dxf.tag: (at.dxf.text or '').strip() for at in ins.attribs} if ins.attribs else {}
        if a.get('DEVICE', '') != 'TB' and not (ins.dxf.name == '_LU0001' and a.get('PARTS') == 'TB'):
            continue
        x, y = round(ins.dxf.insert.x, 1), round(ins.dxf.insert.y, 1)
        c = comp_near(x, y)
        if c is None:
            continue
        if any(d == 'TB' and abs(tx - x) < TOL and abs(ty - y) < TOL
               for d, t, tx, ty in terms_by_comp[c]):
            continue
        label = _senban_at(x, y) or a.get('DEVICE1', '') or '?'
        terms_by_comp[c].add(('TB', label, x, y))

    def norm(s):                                   # '10206' -> '102-06'(回路-枝)
        return f'{s[:3]}-{s[3:]}' if len(s) > 3 and s[:3].isdigit() and s[3:].isdigit() else s
    nets = []
    for c, terms in terms_by_comp.items():
        if len(terms) < 2:
            continue
        sb = comp_sb.get(c, [])
        name = norm(sb[0]) if sb else f'C{c}'
        nets.append({'線番': name,
                     'terminals': sorted((d, t, x, y) for (d, t, x, y) in terms)})
    return nets


def _terminals(sheet_path):
    """2人目(端子番号)を座標付き+確度で返す [[機器, 端子, x, y, tier], ...]。
    tier は接点番号の確度(3段階):
      'drawn'   … 図面に端子番号が記載(確実)            → 青
      'inferred'… 図面は空欄だが contact_resolve で解決(要判断) → 濃い黄色
      'unknown' … 番号を決められない                    → 赤(脇に?)
    どの接点も必ず1点として出す(○で『そこに接点がある』ことを明示)。"""
    comp = _build_components(sheet_path)
    resmap = edge_trace._resolve_map(sheet_path)
    seen, out = set(), []
    for terms in comp['comp_terms'].values():
        for (d, t, x, y) in terms:
            if t != '?':                         # 図面に番号あり=確実
                tt, tier = t, 'drawn'
            else:
                rp = edge_trace._lookup(resmap, d, x, y)
                if rp is not None:               # 推論で解決=要判断
                    tt, tier = rp, 'inferred'
                else:                            # 不明
                    tt, tier = '?', 'unknown'
            k = (d, tt, round(x), round(y))
            if k in seen:
                continue
            seen.add(k)
            out.append([d, tt, round(x, 1), round(y, 1), tier])
    return out


def _wire_graph(segs, tol):
    """配線線分群 → (ノード座標[], 隣接adj)。端点を tol で同一視してグラフ化。"""
    nodes = []
    grid = collections.defaultdict(list)

    def node_of(p):
        gx, gy = int(p[0] // tol), int(p[1] // tol)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in grid[(gx + dx, gy + dy)]:
                    if (nodes[i][0] - p[0]) ** 2 + (nodes[i][1] - p[1]) ** 2 < tol * tol:
                        return i
        i = len(nodes)
        nodes.append((p[0], p[1]))
        grid[(gx, gy)].append(i)
        return i

    adj = collections.defaultdict(set)
    for (a, b) in segs:
        ia, ib = node_of(a), node_of(b)
        if ia != ib:
            adj[ia].add(ib)
            adj[ib].add(ia)
    return nodes, adj


def _nearest_node(nodes, p, maxd):
    best, bd = None, maxd * maxd
    for i, q in enumerate(nodes):
        dd = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
        if dd < bd:
            bd, best = dd, i
    return best


def _route(nodes, adj, pa, pb, tol):
    """2端点間を、描かれた配線線分に沿って辿る折れ線 [[x,y],...]。
    見つからなければ None(呼び側で直線にフォールバック)。幾何最短(ダイクストラ)。"""
    sa = _nearest_node(nodes, pa, tol * 3)
    sb = _nearest_node(nodes, pb, tol * 3)
    if sa is None or sb is None or sa == sb:
        return None
    import heapq
    dist = {sa: 0.0}
    prev = {}
    pq = [(0.0, sa)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == sb:
            break
        if d > dist.get(u, 1e18):
            continue
        ux, uy = nodes[u]
        for v in adj[u]:
            vx, vy = nodes[v]
            nd = d + ((ux - vx) ** 2 + (uy - vy) ** 2) ** 0.5
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if sb not in dist:
        return None
    path = [sb]
    while path[-1] != sa:
        path.append(prev[path[-1]])
    path.reverse()
    pts = [list(pa)] + [[nodes[i][0], nodes[i][1]] for i in path] + [list(pb)]
    return pts


def _extra_contacts(msp, comp, existing):
    """接点シンボル(LS_系)で端子が未検出のもの(例: 押ボタンのラダー接点=属性空)を拾い、
    その接続点(配線端点)に『不明接点』を足す。番号は決められないので tier='unknown'(赤?)。
    既に検出済みの接点の近傍には足さない(重複回避)。◎誤答ゼロ: 存在のみ示し番号は付けない。"""
    ex = [(t[2], t[3]) for t in existing]

    def covered(x, y, r=TOL * 1.5):
        return any((x - px) ** 2 + (y - py) ** 2 < r * r for px, py in ex)

    eps = set()
    for a, b in comp['segs']:
        eps.add((round(a[0], 1), round(a[1], 1)))
        eps.add((round(b[0], 1), round(b[1], 1)))
    out, added = [], []

    def near_added(x, y, r=TOL * 1.6):
        return any((x - ax) ** 2 + (y - ay) ** 2 < r * r for ax, ay in added)

    for ins in msp.query('INSERT'):
        n = ins.dxf.name
        if 'LS_' not in n:                       # 接点/スイッチ系シンボルのみ
            continue
        ox, oy = ins.dxf.insert.x, ins.dxf.insert.y
        # シンボルの縦範囲にある配線端点=接点の上下端子。横ずれは小さく限定。
        cand = sorted([(x, y) for (x, y) in eps
                       if abs(x - ox) <= TOL * 1.2 and abs(y - oy) <= TOL * 4],
                      key=lambda p: p[1])
        for (x, y) in cand:
            if covered(x, y) or near_added(x, y):   # 既存接点/近接追加点は除く(重複回避)
                continue
            added.append((x, y))
            out.append(['接点', '?', round(x, 1), round(y, 1), 'unknown'])
    return out


def extract(sheet_path):
    """1シートの図面geometryと配線(pos_edges)を抽出して dict で返す。"""
    doc = ezdxf.readfile(sheet_path)
    msp = doc.modelspace()
    segs = _segments(msp)
    comp = _build_components(sheet_path)
    _, _, pos = edge_trace.trace_edges(sheet_path, with_pos=True)
    terminals = _terminals(sheet_path)
    # 接点○の位置を『実際の接続点(配線が機器に接続する点=配線線分の端点)』へスナップする。
    # (端子の代表座標はシンボル基準で線上に乗ることがあるため、配線端点に合わせて見た目を正す)
    endpts = []
    for (a, b) in comp['segs']:
        endpts.append(a)
        endpts.append(b)
    egrid = collections.defaultdict(list)
    for i, pq in enumerate(endpts):
        egrid[(int(pq[0] // TOL), int(pq[1] // TOL))].append(i)

    def _snap(x, y, maxd=TOL * 3):
        best, bd = None, maxd * maxd
        gx, gy = int(x // TOL), int(y // TOL)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in egrid[(gx + dx, gy + dy)]:
                    ex, ey = endpts[i]
                    dd = (ex - x) ** 2 + (ey - y) ** 2
                    if dd < bd:
                        bd, best = dd, (ex, ey)
        return best if best else (x, y)
    for t in terminals:
        sx, sy = _snap(t[2], t[3])
        t[2], t[3] = round(sx, 1), round(sy, 1)
    # 未検出の接点(押ボタンのラダー接点など)を接続点に足す(赤?=不明)。
    terminals += _extra_contacts(msp, comp, terminals)
    # --- 外部判定＋線番(端子台番号) ---
    # 点線(L_OUTSIDE/HIDDEN)の機器=外部製品(客先手配等)。その接点は当社の接点でない→tier='external'。
    # 外部へは端子台(TB)でつながり、その端子番号は線番(SENBAN)で図面に振られている。
    hidden = _hidden_layers(doc)
    exts = _external_boxes(msp, hidden)
    senban = _senban(msp)

    def _near_ext(x, y, r=TOL * 3):
        return any((x - ex) ** 2 + (y - ey) ** 2 < r * r for ex, ey in exts)

    def _senban_at(x, y, r=TOL * 3.5):
        best, bd = None, r * r
        for val, sx, sy in senban:
            dd = (x - sx) ** 2 + (y - sy) ** 2
            if dd < bd:
                bd, best = dd, val
        return best
    for t in terminals:
        dev, term, x, y, tier = t
        if _near_ext(x, y):                          # 外部製品の接点
            t[4] = 'external'
            continue
        if str(dev).upper().startswith('TB') or term == '?':
            sb = _senban_at(x, y)                    # 端子台/不明点に線番(=端子番号)を付与
            if sb:
                t[1] = sb
                t[4] = 'drawn' if str(dev).upper().startswith('TB') else t[4]
    # 配線経路: 2端子を直線で結ぶのでなく、実際に描かれた配線線分に沿って辿る折れ線を付す。
    nodes, adj = _wire_graph(comp['segs'], TOL)
    edges = []
    for (da, ta, xa, ya), (db, tb, xb, yb) in pos:
        path = _route(nodes, adj, (xa, ya), (xb, yb), TOL)
        edges.append({
            'from': f'{da}:{ta}', 'to': f'{db}:{tb}',
            'ax': xa, 'ay': ya, 'bx': xb, 'by': yb,
            'path': path,                         # [[x,y],...] or None(直線フォールバック)
        })
    # bbox(線分・端点・機器を含む)
    xs, ys = [], []
    for s in segs:
        xs += [s[0], s[2]]; ys += [s[1], s[3]]
    for e in edges:
        xs += [e['ax'], e['bx']]; ys += [e['ay'], e['by']]
    for t in terminals:
        xs.append(t[2]); ys.append(t[3])
    if not xs:
        xs, ys = [0, 1], [0, 1]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    return {
        'sheet': os.path.basename(sheet_path),
        'segments': segs,
        'devices': _device_labels(msp),
        'edges': edges,
        'terminals': terminals,
        'nets': senban_nets(sheet_path),           # 線番ベースのネット(等電位)
        'bbox': bbox,
    }


def build_html(data):
    """抽出データ → 自己完結HTML文字列。"""
    payload = json.dumps(data, ensure_ascii=False)
    return _TEMPLATE.replace('/*__DATA__*/', payload)


def write_html(sheet_path, out_path=None):
    data = extract(sheet_path)
    if out_path is None:
        out_path = os.path.splitext(sheet_path)[0] + '_確認.html'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(build_html(data))
    return out_path, len(data['edges'])


_TEMPLATE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>配線確認</title>
<style>
  :root{--bg:#ffffff;--fg:#1a1a1a;--line:#c8c8c8;--panel:#f4f4f6;--border:#dddde2;
        --hl:#e4002b;--hldev:#0066cc;--okbg:#e8f5e9;--sel:#fff3cd;
        --tdrawn:#0a62d6;--tinfer:#b8860b;--tunknown:#e4002b;}
  @media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
        --bg:#14151a;--fg:#e8e8ea;--line:#3a3b42;--panel:#1d1e25;--border:#2c2d35;
        --hl:#ff5a76;--hldev:#5aaaff;--okbg:#1f3a24;--sel:#4a3f1a;
        --tdrawn:#5aaaff;--tinfer:#e0b84d;--tunknown:#ff5a76;}}
  :root[data-theme="dark"]{--bg:#14151a;--fg:#e8e8ea;--line:#3a3b42;--panel:#1d1e25;
        --border:#2c2d35;--hl:#ff5a76;--hldev:#5aaaff;--okbg:#1f3a24;--sel:#4a3f1a;
        --tdrawn:#5aaaff;--tinfer:#e0b84d;--tunknown:#ff5a76;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 system-ui,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif;}
  header{padding:8px 14px;border-bottom:1px solid var(--border);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
  header b{font-size:14px}
  .wrap{display:flex;height:calc(100vh - 46px)}
  .view{flex:1 1 60%;min-width:0;position:relative;overflow:hidden;background:var(--bg)}
  svg{width:100%;height:100%;cursor:grab}
  svg.drag{cursor:grabbing}
  .seg{stroke:var(--line);stroke-width:1;vector-effect:non-scaling-stroke}
  .hl{stroke:var(--hl);stroke-width:3;vector-effect:non-scaling-stroke;stroke-linecap:round}
  .hlpt{fill:none;stroke:var(--hl);stroke-width:2.5;vector-effect:non-scaling-stroke}
  .devlbl{fill:var(--hldev);font-size:11px;font-weight:600}
  .devdot{fill:var(--hldev)}
  /* 接点マーカー: 各接点を塗りつぶし●で描き、確度で色分け(青=確実/濃黄=要判断/赤=不明)。 */
  .tring{stroke:none}
  .tlbl{font-size:9px;font-weight:700;paint-order:stroke;stroke:var(--bg);stroke-width:2px}
  .t-drawn .tring{fill:var(--tdrawn)} .t-drawn .tlbl{fill:var(--tdrawn)}
  .t-inferred .tring{fill:var(--tinfer)} .t-inferred .tlbl{fill:var(--tinfer)}
  .t-unknown .tring{fill:var(--tunknown)} .t-unknown .tlbl{fill:var(--tunknown)}
  .t-external .tring{fill:none;stroke:#999;stroke-width:1.4;stroke-dasharray:2 2}
  .t-external .tlbl{fill:#999}
  label.tg{display:inline-flex;gap:4px;align-items:center;cursor:pointer}
  .list{flex:0 0 40%;max-width:520px;overflow:auto;border-left:1px solid var(--border);background:var(--panel)}
  table{border-collapse:collapse;width:100%}
  th,td{padding:5px 8px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
  th{position:sticky;top:0;background:var(--panel);z-index:1}
  tr.row{cursor:pointer}
  tr.row:hover{background:var(--sel)}
  tr.sel{background:var(--sel);outline:2px solid var(--hl)}
  tr.ok td.c{background:var(--okbg)}
  .c{width:34px;text-align:center;cursor:pointer;font-weight:700}
  .hint{opacity:.7;font-size:12px}
  button{font:inherit;padding:3px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--fg);cursor:pointer}
</style>
</head>
<body>
<header>
  <b id="title">配線確認</b>
  <span class="hint">行をクリック→図面上で配線をハイライト / 左端セルで OK 記録 / ホイールで拡大・ドラッグで移動</span>
  <button id="reset">全体表示</button>
  <label class="tg"><input type="checkbox" id="tgterm" checked>端子番号(2人目)</label>
  <span class="hint" id="count"></span>
</header>
<div class="wrap">
  <div class="view">
    <svg id="svg"><g id="scene">
      <g id="segs"></g><g id="terms"></g><g id="hl"></g><g id="devs"></g>
    </g></svg>
  </div>
  <div class="list">
    <table><thead><tr><th class="c">✓</th><th>#</th><th>From</th><th>To</th></tr></thead>
    <tbody id="tb"></tbody></table>
  </div>
</div>
<script>
const DATA = /*__DATA__*/;
const [minX,minY,maxX,maxY] = DATA.bbox;
const W = Math.max(1,maxX-minX), H = Math.max(1,maxY-minY), PAD = 0.04*Math.max(W,H);
// DXFはy上向き。SVGはy下向き→ y' = (maxY - y) で反転。
const fy = y => (maxY - y);
const svg = document.getElementById('svg');
const scene = document.getElementById('scene');
svg.setAttribute('viewBox', `${minX-PAD} ${fy(maxY)-PAD} ${W+2*PAD} ${H+2*PAD}`);
const baseVB = [minX-PAD, fy(maxY)-PAD, W+2*PAD, H+2*PAD];

// 背景(図面)
const gseg = document.getElementById('segs');
let sb = '';
for(const s of DATA.segments){ sb += `<line class="seg" x1="${s[0]}" y1="${fy(s[1])}" x2="${s[2]}" y2="${fy(s[3])}"/>`; }
gseg.innerHTML = sb;

// 接点(2人目): 各接点に○を描き確度で色分け。青=判断できた(図面に番号)、濃黄=要判断
// (推論で解決)、赤=不明(脇に赤い?)。番号は○の右上に表示。
const gterm = document.getElementById('terms');
let tbuf = '';
for(const t of (DATA.terminals||[])){
  const [dev,term,x,y,tier]=t;
  const cls = 't-'+(tier||'unknown');
  tbuf += `<g class="${cls}"><circle class="tring" cx="${x}" cy="${fy(y)}" r="5"/>`;
  if(tier==='unknown'){ tbuf += `<text class="tlbl" x="${x+8}" y="${fy(y)-5}">?</text>`; }
  else { tbuf += `<text class="tlbl" x="${x+8}" y="${fy(y)-5}">${term}</text>`; }
  tbuf += `</g>`;
}
gterm.innerHTML = tbuf;
document.getElementById('tgterm').addEventListener('change',e=>{
  gterm.style.display = e.target.checked ? '' : 'none';
});

// 一覧
const tb = document.getElementById('tb');
DATA.edges.forEach((e,i)=>{
  const tr=document.createElement('tr'); tr.className='row'; tr.dataset.i=i;
  tr.innerHTML=`<td class="c" data-ok="1">·</td><td>${i+1}</td><td>${e.from}</td><td>${e.to}</td>`;
  tb.appendChild(tr);
});
document.getElementById('count').textContent = `配線 ${DATA.edges.length} 本 / 端子 ${(DATA.terminals||[]).length} 点`;
document.getElementById('title').textContent = '配線確認 — ' + DATA.sheet;

const ghl=document.getElementById('hl'), gdev=document.getElementById('devs');
function highlight(i){
  const e=DATA.edges[i];
  // 配線は実際に描かれた経路(path)に沿って強調。無ければ2端子直線にフォールバック。
  let wire;
  if(e.path && e.path.length>1){
    const pts=e.path.map(p=>`${p[0]},${fy(p[1])}`).join(' ');
    wire=`<polyline class="hl" fill="none" points="${pts}"/>`;
  }else{
    wire=`<line class="hl" x1="${e.ax}" y1="${fy(e.ay)}" x2="${e.bx}" y2="${fy(e.by)}"/>`;
  }
  ghl.innerHTML=wire
    +`<circle class="hlpt" cx="${e.ax}" cy="${fy(e.ay)}" r="18"/>`
    +`<circle class="hlpt" cx="${e.bx}" cy="${fy(e.by)}" r="18"/>`;
  gdev.innerHTML=`<circle class="devdot" cx="${e.ax}" cy="${fy(e.ay)}" r="4"/>`
    +`<text class="devlbl" x="${e.ax+22}" y="${fy(e.ay)}">${e.from}</text>`
    +`<circle class="devdot" cx="${e.bx}" cy="${fy(e.by)}" r="4"/>`
    +`<text class="devlbl" x="${e.bx+22}" y="${fy(e.by)}">${e.to}</text>`;
  document.querySelectorAll('tr.sel').forEach(t=>t.classList.remove('sel'));
  const tr=tb.querySelector(`tr[data-i="${i}"]`); if(tr){tr.classList.add('sel'); tr.scrollIntoView({block:'nearest'});}
}
tb.addEventListener('click',ev=>{
  const tr=ev.target.closest('tr.row'); if(!tr)return;
  if(ev.target.classList.contains('c')){ tr.classList.toggle('ok');
    ev.target.textContent = tr.classList.contains('ok')?'OK':'·'; return; }
  highlight(+tr.dataset.i);
});

// ズーム・パン
let vb=[...baseVB];
function apply(){ svg.setAttribute('viewBox', vb.join(' ')); }
svg.addEventListener('wheel',ev=>{ev.preventDefault();
  const r=svg.getBoundingClientRect();
  const mx=vb[0]+(ev.clientX-r.left)/r.width*vb[2];
  const my=vb[1]+(ev.clientY-r.top)/r.height*vb[3];
  const k=ev.deltaY<0?0.85:1.18;
  vb[0]=mx-(mx-vb[0])*k; vb[1]=my-(my-vb[1])*k; vb[2]*=k; vb[3]*=k; apply();
},{passive:false});
let drag=null;
svg.addEventListener('pointerdown',ev=>{drag=[ev.clientX,ev.clientY,vb[0],vb[1]];svg.classList.add('drag');svg.setPointerCapture(ev.pointerId);});
svg.addEventListener('pointermove',ev=>{if(!drag)return;const r=svg.getBoundingClientRect();
  vb[0]=drag[2]-(ev.clientX-drag[0])/r.width*vb[2]; vb[1]=drag[3]-(ev.clientY-drag[1])/r.height*vb[3]; apply();});
svg.addEventListener('pointerup',()=>{drag=null;svg.classList.remove('drag');});
document.getElementById('reset').addEventListener('click',()=>{vb=[...baseVB];apply();});
if(DATA.edges.length) highlight(0);
</script>
</body>
</html>
"""


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    out, n = write_html(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(f'生成: {out} (配線 {n} 本)')
