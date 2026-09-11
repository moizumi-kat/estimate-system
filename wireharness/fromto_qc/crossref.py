# -*- coding: utf-8 -*-
"""シート横断結合: シーケンス(接続論理＋端子番号) × 内部配置図(物理位置) を機器名で結合。

判明した実データの事実:
  - 電線には属性が無い（ネット番号も両端参照も無し）→ 接続は幾何で追うしかない。
  - 機器ブロックには属性がある。役割がシートで分かれている:
      * シーケンス図 … DEVICE/DEVICE1(機器名) と TERMINAL1..(端子番号) と TB(端子相対座標)。
                       = 「どの端子がどの号線に繋がるか」という接続論理の源。
      * 内部配置図   … DEVICE/DEVICE1 と PMT(外形枠) と TYPE、そして盤面の物理座標(insert)。
                       ブロック内 TEMPLATE POINT に端子ごとの物理オフセットを持つ部品もある(MCCB等)。
                       = 「盤のどこに在るか」という測長の源。
  この2つを機器名で突き合わせると、From-To の各端点に
      端子番号(シーケンス) + 物理位置(内部配置図) + 端子高さz(型式辞書) が揃い、測長が確定する。
  片シートにしか無い機器(例: ヒューズ F-WL1 は配置図のみ)も、和集合で回収できる。
"""
import math
import ezdxf
from .geometry import norm, _rot
from . import terminals as T


def physical_devices(layout_path):
    """内部配置図から機器の物理情報を抽出。
    戻り: {devnorm: {'sym','x','y','box','type','parts','term_pts':[(x,y),...]}}。
    term_pts はブロック内 TEMPLATE POINT を回転・移動した端子の物理位置（有る部品のみ）。"""
    doc = ezdxf.readfile(layout_path)
    msp = doc.modelspace()
    out = {}
    for e in msp:
        if e.dxftype() != 'INSERT' or not e.attribs:
            continue
        a = {at.dxf.tag: at.dxf.text for at in e.attribs}
        dev = a.get('DEVICE', '')
        parts = a.get('PARTS', '')
        # 端子台は DEVICE=TB101 のように DEVICE1 無しで名に番号が入る形もある
        if not dev or dev in ('Lug', 'CABLE', 'CH', 'NP') or \
           parts in ('銘板', '端子ｶﾊﾞｰ', 'TB取付金具', 'ﾊﾝﾄﾞﾙ', 'CH', 'Lug'):
            continue
        sym = f"{dev}-{a.get('DEVICE1','')}" if a.get('DEVICE1') else dev
        key = norm(sym)
        ins = e.dxf.insert
        deg = e.dxf.rotation or 0
        # 端子の物理位置（ブロック内 TEMPLATE POINT）
        term_pts = []
        blk = doc.blocks.get(e.dxf.name)
        if blk is not None:
            for be in blk:
                if be.dxftype() == 'POINT' and be.dxf.layer == 'TEMPLATE':
                    rx, ry = _rot(be.dxf.location.x, be.dxf.location.y, deg)
                    term_pts.append((round(ins.x + rx, 1), round(ins.y + ry, 1)))
        box = None
        try:
            l, t, r, b = [float(x) for x in a.get('PMT', '').split(',')[:4]]
            cs = [_rot(l, t, deg), _rot(r, t, deg), _rot(l, b, deg), _rot(r, b, deg)]
            xs = [ins.x + c[0] for c in cs]
            ys = [ins.y + c[1] for c in cs]
            box = (min(xs), min(ys), max(xs), max(ys))
        except Exception:
            box = (ins.x - 18, ins.y - 18, ins.x + 18, ins.y + 18)
        # 既出(同名が複数INSERT=多極を分割配置)なら端子点を統合
        if key in out:
            out[key]['term_pts'] += term_pts
        else:
            out[key] = {'sym': sym, 'x': round(ins.x, 1), 'y': round(ins.y, 1),
                        'box': box, 'type': a.get('TYPE', ''), 'parts': parts, 'term_pts': term_pts}
    return out


def terminal_positions(dev_info):
    """機器の端子物理位置を返す。TEMPLATE点が有ればそれ、無ければ型式辞書のcoords、
    それも無ければ機器中心。戻り: [(name_or_None, x, y, z)]。zは型式辞書の端子高さ。"""
    pat = T.resolve_pattern(dev_info.get('parts', ''), dev_info.get('type', ''))
    z = T.pattern_z(pat) if pat else 0
    if dev_info['term_pts']:
        named = T.assign_names(dev_info['term_pts'], pat) if pat else \
            [('?', x, y) for x, y in dev_info['term_pts']]
        return [(nm, x, y, z) for nm, x, y in named]
    if pat:
        pts = T.positioned_terminals((dev_info['x'], dev_info['y']), 0,
                                     dev_info.get('parts', ''), dev_info.get('type', ''))
        if pts:
            return [(nm, x, y, z if zz is None else zz) for nm, x, y, zz, _ in pts]
    return [(None, dev_info['x'], dev_info['y'], z)]


def merge_devices(seq_models, layout_path):
    """シーケンス(接続論理)と内部配置図(物理)を機器名で結合した機器台帳。
    戻り: {devnorm: {'sym','phys':(x,y)|None,'box','type','parts','seq_terms':set(端子番号),
                     'term_pos':[(name,x,y,z)],'sheets':set()}}"""
    phys = physical_devices(layout_path)
    ledger = {}
    for key, info in phys.items():
        ledger[key] = {'sym': info['sym'], 'phys': (info['x'], info['y']), 'box': info['box'],
                       'type': info['type'], 'parts': info['parts'],
                       'seq_terms': set(), 'term_pos': terminal_positions(info), 'sheets': {'layout'}}
    for m in seq_models:
        for t in m.terminals:
            key = norm(t.device)
            slot = ledger.setdefault(key, {'sym': t.device, 'phys': None, 'box': None, 'type': '',
                                           'parts': '', 'seq_terms': set(), 'term_pos': [], 'sheets': set()})
            if t.name and t.name != '?':
                slot['seq_terms'].add(t.name)
            slot['sheets'].add('seq')
    return ledger


def device_positions(ledger):
    """機器→物理座標(x,y,z) 辞書（測長・ルーティング用）。z=端子高さの代表値。"""
    pos = {}
    for key, s in ledger.items():
        if s['phys']:
            z = s['term_pos'][0][3] if s['term_pos'] else 0
            pos[s['sym']] = (s['phys'][0], s['phys'][1], z)
    return pos


def terminal_blocks(ledger):
    """端子台(PARTS=TB / 名がTBで始まる)の物理位置 {名:(x,y,z)}（中継候補）。"""
    tbs = {}
    for key, s in ledger.items():
        if s['phys'] and (s['parts'] == 'TB' or str(s['sym']).upper().startswith('TB')):
            z = s['term_pos'][0][3] if s['term_pos'] else 0
            tbs[s['sym']] = (s['phys'][0], s['phys'][1], z)
    return tbs
