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
from .geometry import norm
from . import device_norm as dn


def _seq_paths(seiban_dir):
    """配線(シーケンス)図面のパス。制御盤=F系列、分電盤=H系列(配線図)。
    分電盤の図面系列は G(外形)/H(配線)/M/P(仕様)/Z(社内表) で、配線は H系列にある。
    系列コードのH(H001〜/H101〜)を狙い、製番中のHと誤爆しないよう H[0-9] で拾う。"""
    pats = ('図面データ_*F*.DXF', '図面データ_*F*.dxf',
            '図面データ_*H[0-9]*.DXF', '図面データ_*H[0-9]*.dxf')
    out = []
    for p in pats:
        out += glob.glob(os.path.join(seiban_dir, p))
    return sorted(set(out))


def locator_resolution(seiban_dir):
    """{ロケーター表記(大文字): 設計実機器sym(大文字)}。
    優先順位(◎誤答ゼロ):
      1) 設計DCT(機器相番号)があれば、その採番(基-文字→実機器)を正解として直接採用。
         手本が依拠する採番そのもので最も正確。位置ベース再生成は機器名の粒度差で誤る。
      2) DCTが無い場合のみ、locator_assign(内部配置図D001の実機器＋位置)で我々が付番。
      3) それでも引けない素letterは ④ピン照合 で補完。
    ④が使う表記(基前置 '2-A' / 素letter 'A')の双方を引けるよう登録する。"""
    layout = (glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.DXF')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.dxf')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.DXF')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.dxf')))
    m = {}
    dcts = glob.glob(os.path.join(seiban_dir, '機器相番号_*.dxf'))
    seq = _seq_paths(seiban_dir)
    # 1) 設計DCT(機器相番号)があれば、その採番(基-文字→実機器)を正解として直接採用する。
    #    手本が依拠する採番そのものなので最も正確。位置ベース再生成(幾何assign/基指定)は
    #    機器名の粒度差(例 DTX1↔DTX11)で誤マッピングを生むため、DCTがある限り使わない(◎誤答ゼロ)。
    if dcts:
        pairs = []
        for p in dcts:
            pairs += LM.extract_pairs(p)
        bare = collections.Counter(LM._split_locator(loc)[1].upper()
                                   for loc, _ in pairs if LM._split_locator(loc)[1])
        for loc, act in pairs:
            b, lt = LM._split_locator(loc)
            if not lt:
                continue
            dev = norm(act).upper()                         # 図面側symと揃える(表記ハイフン除去: '2-1'→'21')
            if b:
                m[f'{b}-{lt}'.upper()] = dev                # 基-文字(基で一意)
            if bare[lt.upper()] == 1:                       # 素letterは一意な時のみ(曖昧回避)
                m.setdefault(lt.upper(), dev)
        if m:
            return m
    # 2) DCTが無い場合のみ、我々が位置ベースで採番して代替する(内部配置図D001の実機器＋位置)。
    a = LA.assign(layout)
    bare2 = collections.Counter()
    for loc, actual in a['by_locator'].items():
        m[loc.upper()] = actual.upper()
        bare2[loc.split('-')[-1].upper()] += 1
    for loc, actual in a['by_locator'].items():           # 素letterも(一意な時のみ)
        lt = loc.split('-')[-1].upper()
        if bare2[lt] == 1:
            m.setdefault(lt, actual.upper())
    if m:
        return m
    # 3) 最終フォールバック: ④ピン照合
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
    seq = _seq_paths(seiban_dir)
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


def _circ_num(sym):
    """機器記号 → 回路番号(3桁)。例 '52-102'→'102', 'TB-13'→''(TBは台番号で回路でない)。"""
    m = re.search(r'(\d{3})', dn.strip_marks(str(sym or '')))
    return m.group(1) if m else ''


def _conn_edge(edge):
    """機能エッジ + 端子台の回路正規化。
    端子台(TB)の名前は『台番号』(TB-13 等)で、これは人が内部配置図で割り当てる工程(回路図から
    導けない)。ツールの責務は『回路単位でどの端子台へ繋ぐか』までなので、TB端点を相手機器の
    回路番号で正規化し、台番号付与(人/確認ループ)と切り離して結線生成の実力を測る。"""
    e = [(s, t) for (s, t) in _func_edge(edge)]
    circs = [_circ_num(s) for (s, t) in e]
    out = []
    for i, (s, t) in enumerate(e):
        if str(s).upper().startswith('TB'):
            other = (circs[1 - i] if len(e) == 2 else '') or _circ_num(s)
            out.append(('TB', other))
        else:
            out.append((s, t))
    return frozenset(out)


def score_connectivity(seiban_dir):
    """連結採点(ハーネス工程の実力)。機能等価＋端子台の回路正規化で、台番号付与を切り離して測る。
    『どの端子とどの端子(どの回路の端子台)を繋ぐか』の生成網羅を表す。戻り: 同上。"""
    lmap = locator_resolution(seiban_dir)
    t = {_conn_edge(e) for e in truth_edges(seiban_dir, lmap)}
    g = {_conn_edge(e) for e in generate_edges(seiban_dir, lmap)}
    hit = len(t & g)
    return {'seiban': os.path.basename(seiban_dir.rstrip('/')),
            'truth': len(t), 'gen': len(g), 'hit': hit,
            'recall': hit / len(t) if t else 0.0,
            'precision': hit / len(g) if g else 0.0}
