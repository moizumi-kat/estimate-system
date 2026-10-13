# -*- coding: utf-8 -*-
"""ロケーター(仮名称)を内部配置図から『我々が』決定的に付番する。

背景(茂泉様): 補助リレー等 同型式の小部品が並ぶと、From/Toの起点がどの部品か分かりにくい。
フル名(型式＋機器番号)はスペース上描けないので、短い仮名(A,B,C…)=ロケーターを付ける。
既存のロケーター生成(凡例)は図面版(設計DCT/手配図面③)で食い違うため依存しない。

本モジュールは、設計の内部配置図(図面データ D001/G001 等)が『実機器名＋物理位置』を持つことを
利用し、我々が一貫したルールでロケーターを割り当てる(凡例不要)。盤(基)ごとに読み順
(上→下、左→右)で A,B,C… を付け、複数盤は基番号を前置(例 2-A)。これを From-To と図面注記の
両方で使えば、名称の不一致は起きない(◎誤答ゼロの土台)。

read_printed(): 設計DCT(機器相番号)に印字済みのロケーター文字を、位置一致で実機器へ対応づける
(既存印字を踏襲したい場合や検証用)。両図面は同一座標系で重なる。
"""
import glob
import os
import re

import ezdxf

_RELAY_TYPE = re.compile(r'RU\d*S', re.I)
_PANEL_GAP = 300.0     # これ以上xが離れたら別の盤(基)とみなす


def _is_locator_part(attrs):
    """ロケーター対象の小部品か(補助リレー等)。PARTS=AXR または 型式 RU?S。"""
    return attrs.get('PARTS') == 'AXR' or bool(_RELAY_TYPE.search(attrs.get('TYPE', '')))


def actual_parts(layout_paths):
    """設計内部配置図群 → [{'sym','base','num','x','y','type'}]。実機器名を持つ小部品。
    sym=DEVICE+DEVICE1(例 'ALX2')。凡例行(DEVICE2=':')は除外。"""
    seen = {}
    for p in (layout_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {k.dxf.tag: (k.dxf.text or '').strip() for k in ins.attribs}
            if a.get('DEVICE2') == ':' or not _is_locator_part(a):
                continue
            dev, no = a.get('DEVICE', ''), a.get('DEVICE1', '')
            if not dev:                       # 実機器名が無い(ロケーター文字のみ)図面はスキップ
                continue
            sym = f'{dev}{no}'
            try:
                x, y = round(ins.dxf.insert.x, 1), round(ins.dxf.insert.y, 1)
            except Exception:
                x, y = 0.0, 0.0
            seen.setdefault(sym, {'sym': sym, 'base': dev.upper(), 'num': no,
                                  'x': x, 'y': y, 'type': a.get('TYPE', '')})
    return list(seen.values())


def _cluster_panels(parts, gap=_PANEL_GAP):
    """x座標で盤(基)に分割。戻り: [[part,...], ...] を基の左から順。"""
    if not parts:
        return []
    ps = sorted(parts, key=lambda r: r['x'])
    panels = [[ps[0]]]
    for pr in ps[1:]:
        if pr['x'] - panels[-1][-1]['x'] > gap:
            panels.append([pr])
        else:
            panels[-1].append(pr)
    return panels


def _letters(n):
    """A,B,...,Z,AA,AB,… を n 個。"""
    out = []
    i = 0
    while len(out) < n:
        s, k = '', i
        while True:
            s = chr(ord('A') + k % 26) + s
            k = k // 26 - 1
            if k < 0:
                break
        out.append(s)
        i += 1
    return out


def assign(layout_paths, basis_prefix='auto'):
    """内部配置図群 → ロケーター割当。盤(基)ごとに読み順(上→下,左→右)で A,B,C…。
    basis_prefix: 'auto'=盤が複数なら基番号を前置(2-A) / True=常に前置 / False=前置しない。
    戻り: {'by_actual': {実機器sym: ロケーター}, 'by_locator': {ロケーター: 実機器sym},
           'panels': [[(ロケーター, part)...]...]}。"""
    parts = actual_parts(layout_paths)
    panels = _cluster_panels(parts)
    multi = len(panels) > 1
    prefix = (basis_prefix is True) or (basis_prefix == 'auto' and multi)
    by_actual, by_locator, panel_out = {}, {}, []
    for bi, panel in enumerate(panels, start=1):
        ordered = sorted(panel, key=lambda r: (-r['y'], r['x']))   # 上→下, 左→右
        letters = _letters(len(ordered))
        rows = []
        for lt, pr in zip(letters, ordered):
            loc = f'{bi}-{lt}' if prefix else lt
            by_actual[pr['sym']] = loc
            by_locator[loc] = pr['sym']
            rows.append((loc, pr))
        panel_out.append(rows)
    return {'by_actual': by_actual, 'by_locator': by_locator, 'panels': panel_out}


def legend(layout_paths, basis_prefix='auto'):
    """ロケーター凡例の行 [(ロケーター, 実機器sym, 型式)] を基・読み順で返す(注記・表示用)。"""
    a = assign(layout_paths, basis_prefix)
    out = []
    for rows in a['panels']:
        for loc, pr in rows:
            out.append((loc, pr['sym'], pr['type']))
    return out


def read_printed(dct_paths, layout_paths, tol=5.0):
    """設計DCT(機器相番号)に印字済みのロケーター文字を、位置一致で実機器へ対応づける。
    戻り: {実機器sym: 印字ロケーター}。両図面は同一座標で重なる前提(近接tol)。"""
    # DCT: 位置→印字文字(DEVICE1 が単文字 等)
    printed = []
    for p in (dct_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {k.dxf.tag: (k.dxf.text or '').strip() for k in ins.attribs}
            if a.get('DEVICE2') == ':' or not _is_locator_part(a):
                continue
            lt = a.get('DEVICE1', '') or a.get('DEVICE', '')
            if not re.fullmatch(r'[A-Z]', lt):
                continue
            try:
                printed.append((lt, round(ins.dxf.insert.x, 1), round(ins.dxf.insert.y, 1)))
            except Exception:
                pass
    out = {}
    for pr in actual_parts(layout_paths):
        best, bd = None, tol
        for lt, x, y in printed:
            d = abs(pr['x'] - x) + abs(pr['y'] - y)
            if d <= bd:
                bd, best = d, lt
        if best:
            out[pr['sym']] = best
    return out
