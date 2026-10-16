# -*- coding: utf-8 -*-
"""統合From-To生成 vs ④(作業者正解) の総合採点(回帰計測)。

これまでの部品を統合して From-To を生成し、④と突合して再現率/適合率を測る:
  制御配線 = edge_trace(実配線トレース)
  主回路   = main_circuit(LUG→遮断器→TB・接地)
  SPD分岐  = fixed_wiring(固定端点の定型配線・候補)
突合時は正規化(device_norm=○◎マーク除去, 端子の空白除去, ロケーター解決)を施す。
ロケーター解決は凡例(locator_map)＋欠落は④のピン照合(resolve_by_terminals)で近似する。

注意(現状の上限要因):
  ・制御配線トレースの網羅が上限(edge_traceの機器レベル再現が頭打ち＋端子名'?'は突合不可)。
  ・④のロケーターは手配図面③(手書き)の割当で、設計DCTの凡例と異なる場合があり整合が難しい。
  ・太物中心の盤は主回路の生成が少ない(太物はハーネス対象外=別工程)。
数値はこれらを含む厳格な(機器,端子)完全一致。母数を増やすには制御生成の網羅改善が要る。
"""
import collections
import glob
import os
import re

import ezdxf

from . import fromto_truth as ft
from . import edge_trace
from . import main_circuit
from . import fixed_wiring
from . import locator_map as LM
from . import locator_assign as LA
from . import device_norm as dn


def locator_resolution(seiban_dir):
    """{ロケーター表記(大文字): 設計実機器sym(大文字)}。
    主軸は locator_assign(内部配置図D001の実機器＋位置から我々が決定的に付番)。
    ④が使う表記(基前置 '2-A' / 素letter 'A')の双方を引けるよう登録する。
    D001に実機器名が無い等で付番できない場合は、従来(凡例＋ピン照合)にフォールバック。"""
    layout = (glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.DXF')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.dxf')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.DXF')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.dxf')))
    m = {}
    a = LA.assign(layout)
    bare = collections.Counter()
    for loc, actual in a['by_locator'].items():
        m[loc.upper()] = actual.upper()
        bare[loc.split('-')[-1].upper()] += 1
    for loc, actual in a['by_locator'].items():           # 素letterも(一意な時のみ)
        lt = loc.split('-')[-1].upper()
        if bare[lt] == 1:
            m.setdefault(lt, actual.upper())
    if m:
        return m
    # --- フォールバック: 凡例＋④ピン照合 ---
    dcts = glob.glob(os.path.join(seiban_dir, '機器相番号_*.dxf'))
    seq = (glob.glob(os.path.join(seiban_dir, '図面データ_*F*.DXF')) +
           glob.glob(os.path.join(seiban_dir, '図面データ_*F*.dxf')))
    for p in dcts:
        for loc, act in LM.extract_pairs(p):
            _, lt = LM._split_locator(loc)
            if lt:
                m[lt.upper()] = act.upper()
    r = ft.load_seiban(seiban_dir)
    letter_pins = collections.defaultdict(set)
    for w in r['wires']:
        for e in w['ends']:
            s = ft.endpoint_sym(e)
            if re.fullmatch(r'[A-Z]', s) and s.upper() not in m:
                t = (e['term'] or '').strip()
                if re.fullmatch(r'\d+', t):
                    letter_pins[s.upper()].add(t)
    if letter_pins:
        cand = collections.defaultdict(set)
        for p in seq:
            try:
                doc = ezdxf.readfile(p)
            except Exception:
                continue
            for ins in doc.modelspace().query('INSERT'):
                if not ins.attribs:
                    continue
                a2 = {k.dxf.tag: (k.dxf.text or '').strip() for k in ins.attribs}
                sym = f"{a2.get('DEVICE','')}{a2.get('DEVICE1','')}".upper()
                for tt in (a2.get('TERMINAL1', ''), a2.get('TERMINAL2', '')):
                    for tok in re.findall(r'\d+', tt):
                        cand[sym].add(tok)
        for lt, (sym, _conf) in LM.resolve_by_terminals(letter_pins, cand).items():
            if sym:
                m.setdefault(lt, sym.upper())
    return m


def _nsym(sym, lmap):
    s = dn.strip_marks(str(sym)).upper()
    base = s.split('-')[0]
    if re.fullmatch(r'[A-Z]', base) and base in lmap:
        s = lmap[base]
    return dn.canon(s)                     # ハイフン等の表記ゆれを吸収(FX-1≡FX1)


def _nterm(t):
    return re.sub(r'\s+', '', str(t or '')).upper()


def _nedge(a_sym, a_t, b_sym, b_t, lmap):
    return frozenset(((_nsym(a_sym, lmap), _nterm(a_t)), (_nsym(b_sym, lmap), _nterm(b_t))))


def generate_edges(seiban_dir, lmap=None):
    """統合生成の正規化エッジ集合を返す(制御＋主回路＋SPD)。"""
    if lmap is None:
        lmap = locator_resolution(seiban_dir)
    seq = (glob.glob(os.path.join(seiban_dir, '図面データ_*F*.DXF')) +
           glob.glob(os.path.join(seiban_dir, '図面データ_*F*.dxf')))
    draws = (glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.DXF')) +
             glob.glob(os.path.join(seiban_dir, '図面データ_*E*.DXF')) +
             glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.DXF')))
    gen = set()
    for p in seq:
        try:
            edges, _ = edge_trace.trace_edges(p)
        except Exception:
            continue
        for e in edges:
            (da, ta), (db, tb) = tuple(e)
            if ta == '?' or tb == '?':
                continue
            gen.add(_nedge(da, ta, db, tb, lmap))
    for w in main_circuit.generate(draws) + fixed_wiring.generate(draws):
        fa, ta = (w['from'].split(':', 1) + [''])[:2]
        fb, tb = (w['to'].split(':', 1) + [''])[:2]
        gen.add(_nedge(fa, ta, fb, tb, lmap))
    return gen


def truth_edges(seiban_dir, lmap=None):
    """④の正規化エッジ集合。"""
    if lmap is None:
        lmap = locator_resolution(seiban_dir)
    out = set()
    for w in ft.load_seiban(seiban_dir)['wires']:
        a, b = w['ends']
        out.add(_nedge(ft.endpoint_sym(a), a['term'], ft.endpoint_sym(b), b['term'], lmap))
    return out


def score_seiban(seiban_dir):
    """1製番の採点。戻り: {truth,gen,hit,recall,precision}。"""
    lmap = locator_resolution(seiban_dir)
    t = truth_edges(seiban_dir, lmap)
    g = generate_edges(seiban_dir, lmap)
    hit = len(t & g)
    return {'seiban': os.path.basename(seiban_dir.rstrip('/')),
            'truth': len(t), 'gen': len(g), 'hit': hit,
            'recall': hit / len(t) if t else 0.0,
            'precision': hit / len(g) if g else 0.0}


def score_root(root):
    """複数製番の採点一覧。"""
    return [score_seiban(d) for d in sorted(glob.glob(os.path.join(root, '*/')))
            if ft.load_seiban(d)['wires']]


# ---- 機能ベース採点(同種接点は等価とみなす) ----
from . import contact_pins as _cp


def _func_edge(edge):
    """正規化エッジ(frozenset((sym,term)…)) → 機能エッジ。a接点/b接点は NO/NC に畳む。
    同じ機能の端子なら実番号が違っても一致とみなす(ユーザー整理: 同種接点は電気的に等価)。"""
    def ft_(sym, t):
        f = _cp.function(t, sym)
        return f if f in ('NO', 'NC') else t
    return frozenset((s, ft_(s, t)) for (s, t) in edge)


def score_functional(seiban_dir):
    """機能ベースの採点。実番号でなく『同じ機能の端子か』で一致を見る。
    戻り: {truth,gen,hit,recall,precision}。"""
    lmap = locator_resolution(seiban_dir)
    t = {_func_edge(e) for e in truth_edges(seiban_dir, lmap)}
    g = {_func_edge(e) for e in generate_edges(seiban_dir, lmap)}
    hit = len(t & g)
    return {'seiban': os.path.basename(seiban_dir.rstrip('/')),
            'truth': len(t), 'gen': len(g), 'hit': hit,
            'recall': hit / len(t) if t else 0.0,
            'precision': hit / len(g) if g else 0.0}
