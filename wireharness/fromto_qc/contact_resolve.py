# -*- coding: utf-8 -*-
"""接点シンボルの端子番号(ピン)を解決し、edge_trace で '?' になる接点を実番号候補にする。

シーケンス図では、機器本体(ブロック LA_*)は TERMINAL 属性でピンを持つが、ラダー中に
散在する『接点シンボル』は TERMINAL が空で、現状 '?' になる(全製番で a接点63/警報40/b接点24 等)。
接点の種別はブロック名で分かる:
  _LS_A*  = a接点(NO)      _LS_B*  = b接点(NC)
  _LS_AH* = a接点(警報)     _LS_BH* = b接点(警報)      _LS_CH* = c接点(切替)
標準ピン(JIS/IEC 補助接点): a接点=13-14・23-24…／b接点=11-12・21-22…／コイル=A1-A2。

ただし動力用(52 等)は同種接点が多数(13-14/53-54/73-74/83-84)でピンが一意に決まらない。
そこで『機器内にその種別の接点が1つだけ』の時のみ標準ピンを候補にする(確度:精査=人が確認)。
複数ある時は機器＋種別のみ返し、ピンは確定しない(要確認)。これにより ◎誤答ゼロ を保つ。

本モジュールは図面を読むだけの純解析。edge_trace への差し込みは別途(段階導入)。
"""
import collections
import re

import ezdxf

from . import contact_pins
from . import cross_learn


def is_aux_relay(device_base):
    """補助リレーか(記号末尾が X: 86X/FX/CX/TX/ALX/AX 等)。
    標準の補助接点付番(NO=n3-n4/NC=n1-n2)が素直に当たる機器。
    動力用接触器(52 等)は同種接点が多数でピンが一意に決まらない→対象外。"""
    b = re.sub(r'[\s\-].*$', '', str(device_base or '').upper())   # 'ALX-1'→'ALX'
    b = b.rstrip('0123456789')
    return b.endswith('X')


def contact_type(block_name):
    """ブロック名 → 接点種別。'a'/'b'/'a_alarm'/'b_alarm'/'c'/None。"""
    b = (block_name or '').upper()
    if not b.startswith('_LS'):
        return None
    head = b[:8]
    if 'AH' in head:
        return 'a_alarm'
    if 'BH' in head:
        return 'b_alarm'
    if 'CH' in head:
        return 'c'
    if b.startswith('_LS_A'):
        return 'a'
    if b.startswith('_LS_B'):
        return 'b'
    return None


def _std_pins(ctype, nth):
    """接点種別と第n接点(1始まり) → 標準ピン対。a接点=NO(n3-n4)/b接点=NC(n1-n2)。
    contact_pins.nth_aux を使う。種別不明は None。"""
    if ctype in ('a', 'a_alarm'):
        return contact_pins.nth_aux('NO', nth)
    if ctype in ('b', 'b_alarm'):
        return contact_pins.nth_aux('NC', nth)
    return None


def _sym(d):
    dev, no = d.get('DEVICE', ''), d.get('DEVICE1', '')
    return f'{dev}-{no}' if no else dev


def scan(draw_paths):
    """図面群 → {機器sym: {'unlabeled': [{'type','x','y'}...], 'labeled_pins': set()}}。
    unlabeled = TERMINAL が空の接点シンボル(=現状 '?')。labeled_pins = 本体等で番号済のピン。"""
    devs = collections.defaultdict(lambda: {'unlabeled': [], 'labeled_pins': set()})
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            if not d.get('DEVICE'):
                continue
            sym = _sym(d)
            names = []
            for k in ('TERMINAL1', 'TERMINAL2', 'TERMINAL3', 'TERMINAL4', 'TERMINAL5', 'TERMINAL6'):
                if d.get(k):
                    names += [x.strip() for x in d[k].split(',') if x.strip()]
            ct = contact_type(ins.dxf.name)
            if names:
                devs[sym]['labeled_pins'].update(names)
            elif ct:                      # 番号なしの接点シンボル
                devs[sym]['unlabeled'].append(
                    {'type': ct, 'base': d.get('DEVICE', ''),
                     'x': ins.dxf.insert.x, 'y': ins.dxf.insert.y,
                     'tb': d.get('TB', ''), 'deg': ins.dxf.rotation or 0})
    return devs


def _rot(dx, dy, deg):
    import math
    r = math.radians(deg or 0)
    c, s = math.cos(r), math.sin(r)
    return (dx * c - dy * s, dx * s + dy * c)


def _tb_endpoints(u):
    """接点シンボル1個 → 端子点の座標リスト[(x,y)...]（TBオフセット＋回転）。"""
    nums = [float(n) for n in re.findall(r'-?\d+\.?\d*', u.get('tb', ''))]
    pts = list(zip(nums[0::2], nums[1::2]))
    out = []
    for dx, dy in pts:
        rx, ry = _rot(dx, dy, u.get('deg', 0))
        out.append((round(u['x'] + rx, 1), round(u['y'] + ry, 1)))
    return out


def resolve(draw_paths):
    """図面群 → 接点シンボルのピン解決候補。
    戻り: [{'device','type','pins'(対 or None),'confidence','basis'}]。
      ・機器内にその種別の接点が1つだけ → 標準ピンを候補(確度:精査)。
      ・複数 → ピン未確定(要確認、機器＋種別のみ)。
    ◎誤答ゼロ: 確定は出さない(全て人の確認前提)。"""
    out = []
    for sym, info in scan(draw_paths).items():
        by_type = collections.Counter(u['type'] for u in info['unlabeled'])
        seen = collections.Counter()
        for u in sorted(info['unlabeled'], key=lambda z: (-z['y'], z['x'])):
            ct = u['type']
            idx = seen[ct]
            seen[ct] += 1
            relay = is_aux_relay(u.get('base', sym))
            pins = _std_pins(ct, idx + 1)
            # 確実に出せるのは『補助リレーで、その種別の接点が機器内に1つ』の時のみ。
            if relay and by_type[ct] == 1 and pins:
                out.append({'device': sym, 'type': ct, 'pins': pins,
                            'confidence': '確度:精査', 'x': u['x'], 'y': u['y'],
                            'basis': f'補助リレーの{ct}接点が1つ→標準ピン(NO=n3n4/NC=n1n2)候補'})
            else:
                # 動力用(52等)/多接点はピン未確定(機器＋種別のみ)。参考ピンは pins_guess。
                reason = '動力用/非リレー' if not relay else f'{ct}接点が複数'
                out.append({'device': sym, 'type': ct, 'pins': None, 'pins_guess': pins,
                            'confidence': '要確認', 'x': u['x'], 'y': u['y'],
                            'basis': f'{reason}→ピン未確定(要確認)'})
    return out


# 接点種別 → 要確認マーカー(多接点でピンが一意に決まらない時に使う)
_TYPE_MARK = {'a': 'a?', 'b': 'b?', 'a_alarm': 'aL?', 'b_alarm': 'bL?', 'c': 'c?'}


def _kind_of(ct):
    if ct in ('a', 'a_alarm'):
        return 'NO'
    if ct in ('b', 'b_alarm'):
        return 'NC'
    return None


# 遮断器(MCCB/ELCB)の警報接点の標準端子: ALc=共通, ALa=a接点側, ALb=b接点側(④で確認)。
_ALARM_PINS = {'a_alarm': ('ALc', 'ALa'), 'b_alarm': ('ALc', 'ALb')}


def _is_breaker(sym):
    return cross_learn.device_type(sym).upper().startswith(('MCCB', 'ELCB'))


def resolve_points(draw_paths, markers=False, inv=None):
    """接点候補を『端点座標単位』で返す。戻り: {(device, x, y): pin}。
    edge_trace の '?' 端点を (device, 近傍座標) で引いて置換するための地図。
    割付規則(現場確認・ユーザー整理): 同種接点は電気的に等価なので『空き端子を小さい順に割付』。
    ・補助リレー単一接点 → 標準ピン(13-14/11-12)。
    ・多接点(52等) → 機器型の接点在庫(contact_inventory, ④学習)を小さい順に割付=実番号候補。
    ・在庫が無い型は markers=True なら 接点種別マーカー(a?/b?)。◎誤答ゼロ(等価なので割付自由)。"""
    from . import contact_inventory
    inv = inv if inv is not None else contact_inventory.load()
    pt_pin = {}
    for sym, info in scan(draw_paths).items():
        groups = collections.defaultdict(list)
        for u in info['unlabeled']:
            groups[u['type']].append(u)
        for ct, us in groups.items():
            us_sorted = sorted(us, key=lambda z: (-z['y'], z['x']))   # 読み順(割付順)
            kind = _kind_of(ct)
            inv_pairs = [tuple(p) for p in inv.get(cross_learn.device_type(sym), {}).get(kind, [])]
            single_relay = is_aux_relay(us[0].get('base', sym)) and len(us) == 1
            for i, u in enumerate(us_sorted):
                eps = _tb_endpoints(u)
                if len(eps) != 2:
                    continue
                top, bot = sorted(eps, key=lambda p: -p[1])     # 上(大y)が先
                if single_relay:
                    pins = _std_pins(ct, 1)
                elif i < len(inv_pairs):
                    pins = inv_pairs[i]                         # 在庫から小さい順(等価)
                elif ct in _ALARM_PINS and _is_breaker(sym):
                    pins = _ALARM_PINS[ct]                      # 遮断器の警報接点(ALc/ALa/ALb)
                else:
                    pins = None
                if pins:
                    pt_pin[(sym, top[0], top[1])] = pins[0]
                    pt_pin[(sym, bot[0], bot[1])] = pins[1]
                elif markers:
                    mk = _TYPE_MARK.get(ct)
                    if mk:
                        pt_pin.setdefault((sym, top[0], top[1]), mk)
                        pt_pin.setdefault((sym, bot[0], bot[1]), mk)
    return pt_pin


def summary(draw_paths):
    """解決可否の集計。戻り: {'resolved':n(単一接点), 'ambiguous':m(複数接点), 'by_type':{...}}。"""
    r = resolve(draw_paths)
    resolved = [x for x in r if x['pins']]
    amb = [x for x in r if not x['pins']]
    bt = collections.Counter(x['type'] for x in resolved)
    return {'resolved': len(resolved), 'ambiguous': len(amb),
            'total': len(r), 'by_type': dict(bt)}
