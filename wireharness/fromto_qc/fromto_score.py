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


def _skel_paths(seiban_dir):
    """スケルトン(E系列)図面のパス。受変電/動力盤のEシートには主回路＋補助接点の実配線が
    端子粒度で描かれており(L_MAIN/TEMPLATE)、手本ハーネスにもその結線が入る。本番の
    `harness_sheet.build_sheet` は seq＋skel(E) を trace_nodes で辿っているのに、採点の
    `generate_edges` は従来 F/H のみを辿り E を落としていた(＝本番より少なく測る物差しの誤り)。
    採点を本番に合わせるため E も辿る。分電盤(E無し)は空＝影響なし。"""
    out = []
    for p in ('図面データ_*E*.DXF', '図面データ_*E*.dxf'):
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
    # 本番(build_sheet)に合わせ、配線シート(F/H)に加えスケルトン(E)も辿る。Eには主回路＋
    # 補助接点の実配線が端子粒度で描かれ手本にも入る(F-only は本番より少なく測っていた)。
    seq = sorted(set(_seq_paths(seiban_dir)) | set(_skel_paths(seiban_dir)))
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
            # '?' は未解決端子。ただし端子台(TB)の '?' は台番号未付番で正常(＝_conn_edge が相手回路へ
            # 正規化して畳む)ので落とさない。非TBの '?'(真に未解決の接点)だけ落とす。
            if (ta == '?' and not str(da).upper().startswith('TB')) or \
               (tb == '?' and not str(db).upper().startswith('TB')):
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


# ---- ネット(電気的連結)単位の採点＝公式の物差し(機能同一／茂泉様確定2026-11) ----
# 手本の1ネット内の「点対点の鎖順」は製造側の任意(端子番号・渡りピン・主回路相と同じ"製造付番は
# 任意"原則がペア順にも及ぶ)。∴正しさは「2点が同じネットにあるか」で測る。ペア厳密は製造任意の
# 鎖順/相/空欄端子を図面から一意復元できず原理的に不能。機能同一(ネット一致)が最終目的と一致する。
def _letter_bases(seiban_dir):
    """設計DCTの基別ロケーター地図。{(基,文字):実機器sym} と {基:その基の回路番号集合}。"""
    base_letter = {}
    base_circs = collections.defaultdict(set)
    for p in glob.glob(os.path.join(seiban_dir, '機器相番号_*.dxf')):
        try:
            pairs = LM.extract_pairs(p)
        except Exception:
            continue
        for loc, act in pairs:
            b, lt = LM._split_locator(loc)
            if b and lt:
                dev = norm(act).upper()
                base_letter[(b, lt.upper())] = dev
                m = re.search(r'(\d{3})', dev)
                if m:
                    base_circs[b].add(m.group(1))
    return base_letter, base_circs


def _circ3(sym):
    m = re.search(r'(\d{3})', dn.strip_marks(str(sym or '')))
    return m.group(1) if m else ''


def _resolve_truth_edges_net(seiban_dir, lmap):
    """手本の conn-edge 集合。素ロケーター文字(A-O)を file→基(+相手端点の回路)で解決してから正規化。
    基は曖昧(複数基に同じ文字)なため、(1)相手端点の回路が一意に指す基、無ければ(2)ファイルの優勢な基、
    で確定。どちらも引けなければ素のまま(＝安全に未解決＝偽一致を作らない)。"""
    base_letter, base_circs = _letter_bases(seiban_dir)
    out = set()
    for f in sorted(glob.glob(os.path.join(seiban_dir, 'FromToデータ_*.txt'))):
        try:
            wires = ft.parse(f)
        except Exception:
            continue
        votes = collections.Counter()           # ファイルの優勢な基(回路番号の投票)
        for w in wires:
            for e in w['ends']:
                c = _circ3(ft.endpoint_sym(e))
                for b in base_circs:
                    if c and c in base_circs[b]:
                        votes[b] += 1
        fbase = votes.most_common(1)[0][0] if votes else None

        def res(e, comp):
            s = ft.endpoint_sym(e).upper()
            if re.fullmatch(r'[A-Z]', s):
                cc = _circ3(ft.endpoint_sym(comp))
                cand = [bb for bb in base_circs if cc and cc in base_circs[bb]]
                bb = cand[0] if len(cand) == 1 else fbase
                if bb and (bb, s) in base_letter:
                    return base_letter[(bb, s)]
            return ft.endpoint_sym(e)
        for w in wires:
            a, b = w['ends']
            out.add(_conn_edge(_nedge(res(a, b), a['term'], res(b, a), b['term'], lmap)))
    return out


def _union_find_nets(conn_edges):
    """conn-edge 集合 → (find関数, 全ノード集合)。"""
    par = {}

    def find(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    nodes = set()
    for e in conn_edges:
        pts = list(e)
        for p in pts:
            nodes.add(p)
        if len(pts) == 2:
            par[find(pts[0])] = find(pts[1])
    return find, nodes


def score_connectivity_net(seiban_dir):
    """【公式の物差し】ネット単位の機能同一採点。手本の各辺(a,b)について、生成側が a,b を同じネット
    (連結成分)に入れていれば hit。製造任意の鎖順/相/空欄端子を吸収する。素ロケーター文字は file→基解決。
    戻り: {seiban,truth,present,hit,recall,present_rate}。present=両端点とも生成側に存在する辺数
    (＝機器カバレッジの指標。recall が低くても present が高ければ鎖順でなくカバレッジが律速)。"""
    lmap = locator_resolution(seiban_dir)
    gen = {_conn_edge(e) for e in generate_edges(seiban_dir, lmap)}
    find, gnodes = _union_find_nets(gen)
    truth = _resolve_truth_edges_net(seiban_dir, lmap)
    tot = hit = present = 0
    for e in truth:
        pts = list(e)
        if len(pts) != 2:
            continue
        tot += 1
        a, b = pts
        if a in gnodes and b in gnodes:
            present += 1
            if find(a) == find(b):
                hit += 1
    return {'seiban': os.path.basename(seiban_dir.rstrip('/')),
            'truth': tot, 'present': present, 'hit': hit,
            'recall': hit / tot if tot else 0.0,
            'present_rate': present / tot if tot else 0.0}


# ---- 本番生成器(harness_produce.produce)をネット採点する【②実生成一致＝本番の実力】 ----
# ★採点はスタブ generate_edges でなく本番出力で行う(0x・茂泉様確定)。素ロケーター文字は
#   『相手端点の盤(F/H配線シート)で一意の実機器』＋ファイルの盤で解決(歴史的手法「F盤内候補へ絞る」)。
def _fold_term(sym, term):
    f = _cp.function(term, sym)
    return f if f in ('NO', 'NC') else _nterm(term)


def _dev_panel_map(seiban_dir):
    """実機器sym(_nsym) → 盤index(F/H配線シート順 1,2,…)。素ロケーター文字を盤で一意化するため。"""
    out = {}
    for i, p in enumerate(_seq_paths(seiban_dir), 1):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {k.dxf.tag: (k.dxf.text or '').strip() for k in ins.attribs}
            s = a.get('DEVICE', '') + a.get('DEVICE1', '')
            if s:
                out.setdefault(_nsym(s, {}), i)
    return out


def _letter_panel_map(seiban_dir, dev_panel):
    """DCT: letter → {盤index: 実機器sym}。同じ文字が複数盤にある曖昧を盤で解消する。"""
    out = collections.defaultdict(dict)
    for p in glob.glob(os.path.join(seiban_dir, '機器相番号_*.dxf')):
        try:
            pairs = LM.extract_pairs(p)
        except Exception:
            continue
        for loc, act in pairs:
            b, lt = LM._split_locator(loc)
            if b and lt:
                dev = norm(act).upper()
                pan = dev_panel.get(_nsym(dev, {}))
                if pan:
                    out[lt.upper()][pan] = dev
    return out


def _produce_nets(seiban_dir):
    """本番生成器 produce() の出力を正規化ネット(ノード集合のリスト)で返す。
    戻り (nets, node2net, prod_syms)。TB→(TB,回路)・接点→NO/NC。配線図が無い等で不可なら空。"""
    from . import harness_produce as HP
    seq = _seq_paths(seiban_dir)
    if not seq:
        return [], {}, set()
    skel = _skel_paths(seiban_dir)
    lay = (glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.DXF')) +
           glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.dxf')))
    try:
        r = HP.produce(seq, skel_paths=skel,
                       seiban=os.path.basename(seiban_dir.rstrip('/')).split('_')[0],
                       layout_path=lay[0] if lay else None)
    except Exception:
        return [], {}, set()

    def msym(m):
        dev = (m.get('device') or '').strip(); no = (m.get('no') or '').strip()
        return f'{dev}-{no}' if (no and not re.fullmatch(r'[A-Za-z]+', no)) else dev
    nets = []
    node2net = collections.defaultdict(set)
    prod_syms = set()
    for row in r.get('sheet', []):
        circs = set()
        members = []
        for m in row.get('members', []):
            s = _nsym(msym(m), {})
            prod_syms.add(s)
            members.append((s, m.get('terminal') or ''))
            c = _circ_num(msym(m))
            if c and not s.upper().startswith('TB'):
                circs.add(c)
        ns = set()
        for (s, t) in members:
            if s.upper().startswith('TB'):
                for c in (circs or {''}):
                    ns.add(('TB', c))
            else:
                ns.add((s, _fold_term(s, t)))
        if ns:
            idx = len(nets)
            nets.append(ns)
            for n in ns:
                node2net[n].add(idx)
    return nets, node2net, prod_syms


def score_production_net(seiban_dir):
    """【本番採点＝②実生成一致】本番 produce() 出力をネット単位で採点。場外ケーブル(R-F/L-F)は対象外。
    素ロケーター文字は相手端点の盤(無ければファイルの盤)で一意の実機器へ解決。
    戻り: {seiban,truth,present,hit,recall,present_rate}。present=両端が本番出力に在る辺数(カバレッジ寄り指標)。"""
    lmap = locator_resolution(seiban_dir)
    nets, node2net, prod_syms = _produce_nets(seiban_dir)
    dev_panel = _dev_panel_map(seiban_dir)
    letmap = _letter_panel_map(seiban_dir, dev_panel)

    def is_ext(s):
        return bool(re.search(r'R[-]?F|L[-]?F', str(s)))

    def node(ns, term, other_ns):
        if ns.upper().startswith('TB'):
            return ('TB', _circ_num(other_ns) or _circ_num(ns))
        return (ns, _fold_term(ns, term))
    tot = hit = present = 0
    for fi, f in enumerate(sorted(glob.glob(os.path.join(seiban_dir, 'FromToデータ_*.txt'))), 1):
        try:
            wires = ft.parse(f)
        except Exception:
            continue

        def resolve_one(sym, comp):
            s = sym.upper()
            if not (re.fullmatch(r'[A-Z]', s) and s in letmap):
                return sym
            # 候補＝そのロケーター文字の実機器(本番出力に在るものを優先)
            vs = list(letmap[s].values())
            cands = [v for v in vs if _nsym(v, {}) in prod_syms] or vs
            if len(set(cands)) == 1:
                return cands[0]
            # (1) 相手端点の回路と一致する候補(最も確実: M↔回路201の機器 → 86X201)
            cc = _circ_num(comp)
            if cc:
                m = [c for c in cands if _circ_num(c) == cc]
                if len(set(m)) == 1:
                    return m[0]
            # (2) 相手端点の盤(F/H配線シート)で一意化
            pan = dev_panel.get(_nsym(comp, {})) or dev_panel.get(_nsym(comp, lmap))
            if pan and pan in letmap[s] and letmap[s][pan] in cands:
                return letmap[s][pan]
            # (3) ファイルの盤(最後の手当て)
            if fi in letmap[s] and letmap[s][fi] in cands:
                return letmap[s][fi]
            return sym
        # 反復解決(pass1)：実機器を相手に持つ辺で各文字→実機器を投票で確定し、
        # letter↔letter 辺にも適用する(ファイル内で同じ文字＝同じ実機器)。
        vote = collections.defaultdict(collections.Counter)
        for w in wires:
            a, b = w['ends']
            ra, rb = ft.endpoint_sym(a), ft.endpoint_sym(b)
            for sym, comp in ((ra, rb), (rb, ra)):
                dev = resolve_one(sym, comp)
                if dev != sym:
                    vote[sym.upper()][dev] += 1
        letter_map = {s: c.most_common(1)[0][0] for s, c in vote.items() if c}

        def resolve(sym, comp):
            s = sym.upper()
            if re.fullmatch(r'[A-Z]', s) and s in letter_map:
                return letter_map[s]
            return resolve_one(sym, comp)
        for w in wires:
            a, b = w['ends']
            ra, rb = ft.endpoint_sym(a), ft.endpoint_sym(b)
            if is_ext(ra) or is_ext(rb):
                continue
            na = _nsym(resolve(ra, rb), lmap)
            nb = _nsym(resolve(rb, ra), lmap)
            x = node(na, a['term'], nb)
            y = node(nb, b['term'], na)
            if x == y:
                continue
            tot += 1
            if x in node2net and y in node2net:
                present += 1
                if node2net[x] & node2net[y]:
                    hit += 1
    return {'seiban': os.path.basename(seiban_dir.rstrip('/')),
            'truth': tot, 'present': present, 'hit': hit,
            'recall': hit / tot if tot else 0.0,
            'present_rate': present / tot if tot else 0.0}
