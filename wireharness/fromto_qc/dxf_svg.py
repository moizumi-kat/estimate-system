# -*- coding: utf-8 -*-
"""DXF図面を、オーバーレイと同一座標系のSVGに描画する（製造アドオン フェーズB）。

現場は「実際の図面上で結線状況を見ながら確認する」ため、図面そのものを描いて
From-To確認レイヤを重ねる。座標を完全一致させるため ezdxf 標準SVGではなく自前で
描画し、変換パラメータ(xmin,ymax…)を返す。オーバーレイは同じ式で座標変換する。

world(DXF, y上向き) → svg(y下向き):  X = x - xmin,  Y = ymax - y
"""
import math
import html
import ezdxf
from .geometry import promote_template_segments as _promote_template_segments

WIRE_LAYERS = {'L_CONTROL', 'L_CONTROL_H', 'L_MAIN', 'L_OUTSIDE', 'L_EARTH', 'DENSEN', 'BOSEN'}
SKIP_LAYERS = {'TEMPLATE', 'NOT_EXPORT', 'CENTER'}


def _flatten(entities, depth=0):
    """INSERTを再帰展開して基本エンティティ列にする（world座標）。"""
    out = []
    for e in entities:
        t = e.dxftype()
        if t == 'INSERT':
            if depth > 4:
                continue
            try:
                out += _flatten(e.virtual_entities(), depth + 1)
            except Exception:
                pass
        else:
            out.append(e)
    return out


def _visible_attribs(msp, doc):
    """INSERTのブロック属性(ATTRIB)のうち、実図に表示される文字を返す。
    表示可否: flags のビット1(=invisible)が立っていない、かつレイヤがON。
    機器名(DEVICE)・線番(線番)・端子(TERMINAL)・注釈(CMNTJ)等が該当。"""
    out = []
    for ins in msp.query('INSERT'):
        if not ins.attribs:
            continue
        for a in ins.attribs:
            try:
                if a.dxf.flags & 1:            # invisible 属性(管理用: MAKER/CODE/PMT 等)
                    continue
                t = (a.dxf.text or '').strip()
                if not t:
                    continue
                lay = doc.layers.get(a.dxf.layer) if a.dxf.layer else None
                if lay is not None and not lay.is_on():
                    continue
                ha = a.dxf.get('halign', 0)
                va = a.dxf.get('valign', 0)
                if (ha or va) and a.dxf.hasattr('align_point'):
                    px, py = a.dxf.align_point.x, a.dxf.align_point.y
                else:
                    px, py = a.dxf.insert.x, a.dxf.insert.y
                anchor = 'middle' if ha in (1, 4) else 'end' if ha == 2 else 'start'
                out.append({'x': px, 'y': py, 'h': a.dxf.height or 20,
                            'rot': a.dxf.rotation or 0, 'text': t,
                            'anchor': anchor, 'tag': a.dxf.tag, 'layer': a.dxf.layer})
            except Exception:
                continue
    return out


def render(paths, extents=None):
    """paths(DXF複数) を1枚のSVGボディにまとめて返す。
    戻り: {'body':str, 'xmin','ymin','xmax','ymax','w','h', 'labels':[...]} """
    ents = []
    attrs = []
    promoted = []   # TEMPLATE レイヤに描かれた実配線（号線付き／接続点接触）を可視化
    for p in paths:
        doc = ezdxf.readfile(p)
        msp = doc.modelspace()
        ents += _flatten(msp)
        attrs += _visible_attribs(msp, doc)
        try:
            promoted += _promote_template_segments(msp, WIRE_LAYERS)
        except Exception:
            pass

    # 範囲
    xs, ys = [], []
    prims = []

    def acc(x, y):
        xs.append(x)
        ys.append(y)

    for e in ents:
        t = e.dxftype()
        lay = e.dxf.layer
        if lay in SKIP_LAYERS:
            continue
        try:
            if t == 'LINE':
                a, b = e.dxf.start, e.dxf.end
                acc(a.x, a.y)
                acc(b.x, b.y)
                prims.append(('L', lay, a.x, a.y, b.x, b.y))
            elif t == 'LWPOLYLINE':
                pts = [(pt[0], pt[1]) for pt in e.get_points('xy')]
                for x, y in pts:
                    acc(x, y)
                prims.append(('P', lay, pts, bool(e.closed)))
            elif t == 'POLYLINE':
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                for x, y in pts:
                    acc(x, y)
                prims.append(('P', lay, pts, bool(e.is_closed)))
            elif t == 'CIRCLE':
                c = e.dxf.center
                r = e.dxf.radius
                acc(c.x - r, c.y - r)
                acc(c.x + r, c.y + r)
                prims.append(('C', lay, c.x, c.y, r))
            elif t == 'ARC':
                c = e.dxf.center
                r = e.dxf.radius
                acc(c.x - r, c.y - r)
                acc(c.x + r, c.y + r)
                prims.append(('A', lay, c.x, c.y, r, e.dxf.start_angle, e.dxf.end_angle))
            elif t in ('SOLID', 'TRACE'):
                vs = [e.dxf.vtx0, e.dxf.vtx1, e.dxf.vtx3, e.dxf.vtx2]
                pts = [(v.x, v.y) for v in vs]
                for x, y in pts:
                    acc(x, y)
                prims.append(('S', lay, pts))
            elif t == 'TEXT':
                s = (e.dxf.text or '').strip()
                if not s:
                    continue
                ins = e.dxf.insert
                h = e.dxf.height or 30
                rot = e.dxf.rotation or 0
                acc(ins.x, ins.y)
                prims.append(('T', lay, ins.x, ins.y, h, rot, s))
            elif t == 'MTEXT':
                s = (e.plain_text() or '').strip()
                if not s:
                    continue
                ins = e.dxf.insert
                h = e.dxf.char_height or 30
                acc(ins.x, ins.y)
                prims.append(('T', lay, ins.x, ins.y, h, 0, s.split('\n')[0]))
        except Exception:
            continue

    # TEMPLATE レイヤの実配線（昇格分）を電線として描画対象に追加
    for (a, b) in promoted:
        acc(a[0], a[1])
        acc(b[0], b[1])
        prims.append(('L', 'L_CONTROL', a[0], a[1], b[0], b[1]))

    if extents:
        xmin, ymin, xmax, ymax = extents
    else:
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
    w, h = xmax - xmin, ymax - ymin

    def X(x):
        return round(x - xmin, 1)

    def Y(y):
        return round(ymax - y, 1)

    # レイヤ→クラス（色はSVG側CSSで指定）
    def cls(lay):
        if lay in WIRE_LAYERS:
            if lay == 'L_MAIN':
                return 'wm'
            if lay == 'L_EARTH':
                return 'we'
            return 'wc'
        return 'gm'

    out = []
    for pr in prims:
        k = pr[0]
        lay = pr[1]
        c = cls(lay)
        if k == 'L':
            _, _, x1, y1, x2, y2 = pr
            out.append(f'<line class="{c}" x1="{X(x1)}" y1="{Y(y1)}" x2="{X(x2)}" y2="{Y(y2)}"/>')
        elif k == 'P':
            _, _, pts, closed = pr
            d = 'M' + ' L'.join(f'{X(x)} {Y(y)}' for x, y in pts) + (' Z' if closed else '')
            out.append(f'<path class="{c}" d="{d}"/>')
        elif k == 'C':
            _, _, cx, cy, r = pr
            out.append(f'<circle class="{c}" cx="{X(cx)}" cy="{Y(cy)}" r="{round(r,1)}"/>')
        elif k == 'A':
            _, _, cx, cy, r, a0, a1 = pr
            out.append(_arc(X(cx), Y(cy), r, a0, a1, c))
        elif k == 'S':
            _, _, pts = pr
            d = 'M' + ' L'.join(f'{X(x)} {Y(y)}' for x, y in pts) + ' Z'
            out.append(f'<path class="sol" d="{d}"/>')
        elif k == 'T':
            _, _, x, y, hh, rot, s = pr
            s = html.escape(s)
            tr = f' transform="rotate({-rot} {X(x)} {Y(y)})"' if rot else ''
            out.append(f'<text class="tx" x="{X(x)}" y="{Y(y)}" font-size="{round(hh,1)}"{tr}>{s}</text>')

    # ブロック属性の表示文字（機器名・線番・端子・注釈）
    for a in attrs:
        s = html.escape(a['text'])
        x, y = X(a['x']), Y(a['y'])
        tag = a['tag']
        cls = 'sn' if tag == '線番' else ('dv' if tag in ('DEVICE', 'DEVICE1', 'DEVICE2') else
              ('tm' if tag.startswith('TERMINAL') else 'tx'))
        tr = f' transform="rotate({-a["rot"]} {x} {y})"' if a['rot'] else ''
        out.append(f'<text class="{cls}" x="{x}" y="{y}" font-size="{round(a["h"],1)}" '
                   f'text-anchor="{a["anchor"]}"{tr}>{s}</text>')

    return {'body': '\n'.join(out), 'xmin': xmin, 'ymin': ymin, 'xmax': xmax,
            'ymax': ymax, 'w': round(w, 1), 'h': round(h, 1)}


def _arc(cx, cy, r, a0, a1, c):
    # world角(反時計・y上) を svg(y下) に。svg角 = -world角
    sa, ea = -a0, -a1
    x1 = cx + r * math.cos(math.radians(sa))
    y1 = cy + r * math.sin(math.radians(sa))
    x2 = cx + r * math.cos(math.radians(ea))
    y2 = cy + r * math.sin(math.radians(ea))
    sweep = 1 if ((a1 - a0) % 360) else 1
    large = 1 if ((a1 - a0) % 360) > 180 else 0
    return (f'<path class="{c}" d="M{round(x1,1)} {round(y1,1)} '
            f'A{round(r,1)} {round(r,1)} 0 {large} {sweep} {round(x2,1)} {round(y2,1)}"/>')
