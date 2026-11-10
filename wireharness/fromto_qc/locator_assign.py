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
import collections
import glob
import os
import re

import ezdxf

from .geometry import norm

_RELAY_TYPE = re.compile(r'RU\d*S', re.I)
_PANEL_GAP = 200.0     # これ以上xが離れたら別の盤(基)とみなす
# （DCT実データ検証: 盤内の横幅は約180-210、盤間ギャップは約290以上。
#   旧値300だと盤間296の製番で隣接盤が誤併合したため200に調整。）


# ロケーター対象の小部品種別: 補助リレー(AXR)・タイマ(TM)。
# DCT実データで、A=タイマ(MS4S) → B,C…=補助リレー(RU4S/RU2S/MY4/G7K…) の順で採番される。
_LOCATOR_PARTS = {'AXR', 'TM'}


def _is_locator_part(attrs):
    """ロケーター対象の小部品か(補助リレー・タイマ)。PARTS=AXR/TM または 型式 RU?S。"""
    return attrs.get('PARTS') in _LOCATOR_PARTS or bool(_RELAY_TYPE.search(attrs.get('TYPE', '')))


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


def _all_device_parts(layout_paths):
    """設計内部配置図群 → 実機器名を持つ全機器 [{'sym','base','num','x','y','type'}]。
    _is_locator_part で絞らない(基割当方式では、設計が対象機器を決めるため)。
    凡例行(DEVICE2=':')は除外。同一symは最初の位置を採用。"""
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
            if a.get('DEVICE2') == ':':
                continue
            dev, no = a.get('DEVICE', ''), a.get('DEVICE1', '')
            if not dev:
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


_YROW_TOL = 40.0   # 同一行とみなすyの許容差（作図上、同じ行でもyが数単位ズレる）


def _reading_order(panel, yrow_tol=_YROW_TOL):
    """盤内の読み順で部品を並べる: 上→下で行にまとめ、同一行(±yrow_tol)は左→右。
    DCT実データで、同じ行の部品でも作図上 y が 0.25 程度ズレるため、厳密な
    (-y, x) ソートでは行内の左右順が崩れる(例: 117盤2の H/I/J)。行に束ねてから
    左→右に並べることで、読み順を正しく再現する。"""
    if not panel:
        return []
    ps = sorted(panel, key=lambda r: -r['y'])
    rows, cur = [], [ps[0]]
    for pr in ps[1:]:
        if abs(pr['y'] - cur[-1]['y']) <= yrow_tol:
            cur.append(pr)
        else:
            rows.append(cur)
            cur = [pr]
    rows.append(cur)
    out = []
    for row in rows:
        out += sorted(row, key=lambda r: r['x'])   # 行内 左→右
    return out


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
        ordered = _reading_order(panel)                            # 上→下(行まとめ), 左→右
        letters = _letters(len(ordered))
        rows = []
        for lt, pr in zip(letters, ordered):
            loc = f'{bi}-{lt}' if prefix else lt
            by_actual[pr['sym']] = loc
            by_locator[loc] = pr['sym']
            rows.append((loc, pr))
        panel_out.append(rows)
    return {'by_actual': by_actual, 'by_locator': by_locator, 'panels': panel_out}


def basis_from_dct(dct_paths):
    """設計のロケーターDCT(_X_DEVICE: 実名→'基-文字')から 実機器→基番号 を読む。
    「基は設計から受け取り、文字は我々が生成」方式の、基入力(検証・本番)に使う。
    戻り: {norm(実機器sym): 基番号(str)}。"""
    from . import locator as _locator
    out = {}
    for dev, info in _locator.letters_from_dct(dct_paths).items():
        out[dev] = info['rail']       # dev は letters_from_dct 内で norm 済み
    return out


def assign_by_basis(layout_paths, basis_of, prefix=True):
    """設計から受け取った基割当(basis_of)で採番し、文字(A,B,C…)は我々が生成する。
    茂泉様確定方針(選択肢2): 基番号・基への所属は設計(手本/DCT)が持つので受け取り、
    我々は「基内の読み順(上→下,左→右)で文字を振る」ことに専念する。幾何での基推定を
    使わないため、基番号ズレ(例 5-21071 が全体で基2)や基境界の誤り(5-21094)が起きない。

    basis_of: {norm(実機器sym): 基番号} … 設計が持つ、各機器の基への所属。
              basis_of に在る機器だけを対象にする(＝設計がロケーター対象とした機器のみ)。
    戻り: assign と同形式 {'by_actual','by_locator','panels'}。"""
    # 候補フィルタ(_is_locator_part)は使わず、設計(basis_of)が対象機器を決める。
    parts = _all_device_parts(layout_paths)
    groups = collections.defaultdict(list)
    for pr in parts:
        b = basis_of.get(norm(pr['sym']))
        if b is not None:
            groups[b].append(pr)

    def _bkey(s):
        s = str(s)
        return (0, int(s)) if s.isdigit() else (1, 0, s)
    by_actual, by_locator, panel_out = {}, {}, []
    for b in sorted(groups, key=_bkey):
        ordered = _reading_order(groups[b])            # 上→下(行まとめ), 左→右
        rows = []
        for lt, pr in zip(_letters(len(ordered)), ordered):
            loc = f'{b}-{lt}' if prefix else lt
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


def read_printed(dct_paths, layout_paths, tol=8.0):
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
