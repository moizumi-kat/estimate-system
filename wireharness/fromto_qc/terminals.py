# -*- coding: utf-8 -*-
"""型式→端子番号 辞書の解決。

印字されない規約端子(MCCB/リレー等)を、機器の型式・部品カテゴリ・極数から補完する。
terminal_library.json を読み、(PARTS, TYPE) から端子パターンを引き、
機器の端子“位置”に端子“名”を割り当てる。
"""
import os
import re
import json

_LIB = None


def library():
    global _LIB
    if _LIB is None:
        p = os.path.join(os.path.dirname(__file__), 'terminal_library.json')
        _LIB = json.load(open(p, encoding='utf-8'))
    return _LIB


def _pole(type_str):
    m = re.search(r'(\d)\s*P', type_str or '')
    return f"{m.group(1)}P" if m else ''


def resolve_pattern(parts, type_str):
    """(部品カテゴリ, 型式) → パターン名。無ければ None。"""
    lib = library()
    pole = _pole(type_str)
    for rule in lib['match']:
        if 'type_contains' in rule and rule['type_contains'] in (type_str or ''):
            return rule['pattern']
        if rule.get('parts') and rule['parts'] == parts:
            if rule.get('pole') and rule['pole'] != pole:
                continue
            return rule['pattern']
    return None


def pattern_terminal_names(pattern_name):
    """パターンの端子名を、割付順（行優先: 上段左右→下段左右 / pins順）で返す。"""
    lib = library()
    p = lib['patterns'].get(pattern_name)
    if not p:
        return []
    if 'rows' in p:
        names = []
        for row in p['rows']:
            names += row
        return names
    if 'pins' in p:
        return p['pins']
    if 'main_rows' in p:      # contactor: 主端子のみ位置割付（コイル/補助は別途）
        names = []
        for row in p['main_rows']:
            names += row
        return names
    return []


def pattern_stud(pattern_name):
    """パターンのねじ径(圧着端子選定用)。socket=リレーソケットで圧着なし。"""
    p = library()['patterns'].get(pattern_name, {})
    return p.get('stud', '')


def positioned_terminals(insert, rotation, parts, type_str, template_points=None):
    """機器1つの端子を『端子名＋絶対座標(x,y)＋ねじ径』で返す（測長・端末加工用）。
    template_points があれば実位置を優先し、無ければ辞書coordsの公称値を使う。
    insert=(ix,iy), rotation=deg。戻り: [(name, x, y, stud), ...]"""
    import math
    pat = resolve_pattern(parts, type_str)
    if not pat:
        return []
    stud = pattern_stud(pat)
    ix, iy = insert
    r = math.radians(rotation or 0)
    c, s = math.cos(r), math.sin(r)

    def rot(dx, dy):
        return (ix + dx * c - dy * s, iy + dx * s + dy * c)
    if template_points:
        named = assign_names(template_points, pat)
        return [(nm, x, y, stud) for nm, x, y in named]
    coords = library()['patterns'].get(pat, {}).get('coords')
    if coords:
        return [(nm, *rot(dx, dy), stud) for nm, (dx, dy) in coords.items()]
    # coords未整備: 名前だけ（位置はカタログ拡張待ち）
    return [(nm, None, None, stud) for nm in pattern_terminal_names(pat)]


def assign_names(points, pattern_name):
    """端子“位置”のリスト [(x,y),...] に、パターンの端子名を空間順で割り当てる。
    行優先（yで上下段に分け、各段xで左右）に並べて names を zip する。
    戻り: [(name, x, y), ...]
    """
    lib = library()
    p = lib['patterns'].get(pattern_name)
    if not p or not points:
        return [('?', x, y) for x, y in points]
    names = pattern_terminal_names(pattern_name)
    n = len(points)
    if 'rows' in p or 'main_rows' in p:
        rows = p.get('rows') or p.get('main_rows')
        ncol = max(len(r) for r in rows)
        # yで段に分割（上段=y大, 下段=y小）
        ys = sorted({round(y) for _, y in points})
        # 段数 = len(rows)
        pts = sorted(points, key=lambda q: (-q[1], q[0]))   # 上→下, 左→右
        out = []
        for i, (x, y) in enumerate(pts):
            out.append((names[i] if i < len(names) else '?', x, y))
        return out
    # pins: 位置順(上→下,左→右)にpin順を割当（近似。実socketはVision/カタログで精緻化）
    pts = sorted(points, key=lambda q: (-q[1], q[0]))
    return [(names[i] if i < len(names) else '?', x, y) for i, (x, y) in enumerate(pts)]
