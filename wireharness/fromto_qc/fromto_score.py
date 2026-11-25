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
    c = dn.canon(s)                        # ハイフン等の表記ゆれを吸収(FX-1≡FX1)
    # LUG端子台(LugTB=CKT型圧着端子ブロック)は引込TBと同一機器(図面でLugTB-6とTB-6の二重ラベル・
    # 同一CKT型を確認)。手本は'LUG-TB<n>'、本番(引込)は'TB-<n>'と別名で呼ぶため、TB畳み込みに乗せて
    # 一致させる。素の'LUG'(分電盤引込)や'LUG-<n>'(5-12073電圧センス)は'TB'を含まず不変=回帰ゼロ。
    c = re.sub(r'^LUGTB(?=\d)', 'TB', c)
    # 中継/渡り/MC-DT等の端子台(手本 '中継-TB2'/'MCDT1-TB2'等)は本番の中継端子台(TB-仮<n>)と同じ
    # 渡り用TB。接頭辞を外してTB畳み込みに乗せ、回路でTBノードとして一致させる(出現率/被覆向上)。
    c = re.sub(r'^(中継|渡り|MCDT\d*)-?TB(?=\d)', 'TB', c)
    # フルネーム別名(PARTS由来・1:1検証済)。lmapに canon(フル名)→機器名 が在れば適用。
    # 単文字キー(ロケーター)とは衝突しない(キーが別空間)＝後方互換(退行ゼロ)。
    if lmap:
        a = lmap.get(c)
        if a and a != c:
            return a
    return c


def _parts_alias(seiban_dir):
    """図面ブロックの PARTS(人の呼称) → 機器名(DEVICE[-DEVICE1]) の1:1別名を作る。
    手本は一部機器を DEVICE コードでなく PARTS(人の呼称)で記す(例 'CPU' を '伝送ﾕﾆｯﾄ')。
    由来は図面属性＝推測でない。曖昧回避のため **1つの PARTS が1機器に限る** ものだけ採用
    (MCCB/ELCB 等の裸クラス名は多機器に対応＝除外)。将来の分電盤でも同機構で吸収。"""
    import glob as _glob
    parts2dev = {}
    for p in sorted(set(_glob.glob(os.path.join(seiban_dir, '図面データ_*.dxf'))) |
                    set(_glob.glob(os.path.join(seiban_dir, '図面データ_*.DXF')))):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            pt = d.get('PARTS', ''); dev = d.get('DEVICE', ''); dv1 = d.get('DEVICE1', '')
            if not pt or not dev:
                continue
            raw = dev + ('-' + dv1 if dv1 and not re.fullmatch(r'[A-Za-z]+', dv1) else '')
            devn = dn.canon(dn.strip_marks(raw).upper())
            key = dn.canon(dn.strip_marks(pt).upper())
            if key == devn:
                continue               # PARTS==DEVICE は別名不要
            parts2dev.setdefault(key, set()).add(devn)
    # 1:1 のみ採用(多対1=曖昧は捨てる＝◎誤答回避)
    return {k: next(iter(v)) for k, v in parts2dev.items() if len(v) == 1}


def _parts_full(seiban_dir):
    """図面ブロックの (canon(PARTS), canon(機器名)) 全ペア。部分一致別名(人の略称)の照合用。"""
    import glob as _glob
    pairs = set()
    for p in sorted(set(_glob.glob(os.path.join(seiban_dir, '図面データ_*.dxf'))) |
                    set(_glob.glob(os.path.join(seiban_dir, '図面データ_*.DXF')))):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            pt = d.get('PARTS', ''); dev = d.get('DEVICE', ''); dv1 = d.get('DEVICE1', '')
            if not pt or not dev:
                continue
            raw = dev + ('-' + dv1 if dv1 and not re.fullmatch(r'[A-Za-z]+', dv1) else '')
            pairs.add((dn.canon(dn.strip_marks(pt).upper()), dn.canon(dn.strip_marks(raw).upper())))
    return pairs


def _main_breaker_alias(seiban_dir):
    """主幹(漏電付MCCB)の ELCB-<n> ↔ MCCB-<n> 別名。{canon('ELCB-n'): canon('MCCB-n')}。
    主幹は漏電付MCCB(例 NF125-NCVZ・SPEC4='AL')で、図面は全接点を MCCB-<n> と記すが、手本は
    漏電/警報接点を同一物の ELCB-<n> と呼ぶ。図面に MCCB-<n> が在り同番の ELCB-<n> ブロックが無い
    ものだけ別名化(分岐ELCBは実在するので対象外＝曖昧なし)。build_sheet の主幹接点ネット(MCCB)と
    手本の ELCB を一致させる。"""
    import glob as _glob
    mccb = set(); elcb = set(); earthleak = set()
    for p in sorted(set(_glob.glob(os.path.join(seiban_dir, '図面データ_*.dxf'))) |
                    set(_glob.glob(os.path.join(seiban_dir, '図面データ_*.DXF')))):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev = d.get('DEVICE', ''); dv1 = d.get('DEVICE1', '')
            if not re.fullmatch(r'\d+', str(dv1)):
                continue
            if dev == 'MCCB':
                mccb.add(dv1)
                # 漏電付(SPEC4='AL' か 型式に mA/NCVZ/NV/BV 等)＝ELCB としても呼ばれ得る
                if 'AL' in d.get('SPEC4', '').upper() or re.search(r'MA|NCVZ|^NV|^BV', d.get('TYPE', '').upper()):
                    earthleak.add(dv1)
            elif dev == 'ELCB':
                elcb.add(dv1)
    out = {}
    for n in (mccb & earthleak) - elcb:        # MCCB(漏電付)で同番ELCBブロックが無い番号のみ
        out[dn.canon(('ELCB-' + n).upper())] = dn.canon(('MCCB-' + n).upper())
    return out


def _nterm(t):
    s = re.sub(r'\s+', '', str(t or '')).upper()
    # 端子番号の位置記述(上/下/左/右＝同一端子の上下段・製造位置)は畳む。手本'2_下'↔本番'2'。
    s = re.sub(r'[_\-]?[上下左右]+$', '', s)
    return s


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
    """機器記号 → 回路番号(3桁)。例 '52-102'→'102', 'TB-13'→''(TBは台番号で回路でない)。
    全数字名(51104=熱動51の回路104)は機器種別prefixを含み先頭3桁'511'が回路でないため、
    先頭の3桁以上の数字runの【末尾3桁】を回路とする(51104→104 / 52104→104 / 2301→301)。"""
    m = re.search(r'(\d{3,})', dn.strip_marks(str(sym or '')))
    return m.group(1)[-3:] if m else ''


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


def _letter_assign_map(seiban_dir):
    """DCTが無い製番(例 5-12043)用: locator_assign(内部配置図D001の実機器+位置)から
    letter → {基番号: 実機器sym}。曖昧な素letter(A=1-A/2-A/…)を『相手端点の回路』で解消する材料。
    キーは基番号(盤indexでない)だが、resolve_one の解決は主に (1)相手回路一致 で効くため、
    候補集合さえ揃えば十分。DCTがある製番では呼ばない(_letter_panel_map を優先)。"""
    out = collections.defaultdict(dict)
    layout = (glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.DXF')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.dxf')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.DXF')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.dxf')))
    if not layout:
        return out
    try:
        a = LA.assign(layout)
    except Exception:
        return out
    for loc, actual in a.get('by_locator', {}).items():
        parts = loc.split('-')
        lt = parts[-1].upper()
        b = parts[0] if len(parts) > 1 else '0'
        if re.fullmatch(r'[A-Z]', lt):
            try:
                key = int(re.sub(r'\D', '', b) or '0')
            except Exception:
                key = 0
            out[lt][key] = actual.upper()
    return out


def _letter_base_map(seiban_dir):
    """letter → {基番号(int): 実機器sym}。手本が基を明記した locator(例 '1I'=基1の文字I, '1-A')を
    曖昧さ無く解決するための地図。DCT(機器相番号)があればそれを基キーで採る(最も正確)。無ければ
    位置ベース採番(_letter_assign_map)。基が手本に明記されている時だけ使う＝推測ゼロ・◎誤答ゼロ。"""
    out = collections.defaultdict(dict)
    dcts = glob.glob(os.path.join(seiban_dir, '機器相番号_*.dxf'))
    if dcts:
        for p in dcts:
            try:
                pairs = LM.extract_pairs(p)
            except Exception:
                continue
            for loc, act in pairs:
                b, lt = LM._split_locator(loc)
                if b and lt and re.fullmatch(r'[A-Z]', lt.upper()):
                    try:
                        key = int(re.sub(r'\D', '', b) or '0')
                    except Exception:
                        continue
                    out[lt.upper()][key] = norm(act).upper()
        if out:
            return out
    return _letter_assign_map(seiban_dir)


# TB台番号を設計レイアウト(回路群内の最寄りストリップ)で付番して照合する(0af: 台番号は回路番号から導出)。
# False に戻せば従来の('TB',回路)畳み込みに戻る(退行時の安全弁)。
_TB_STRIP = True
_tb_ctx_cache = {}

# デバッグ: score_production_net の欠落(present欠落/netgap)を収集する(診断用・既定OFF)。
_COLLECT_GAPS = False
_LAST_GAPS = []


def _spec_bases(s):
    """TB形式文字列から基本形式トークン集合(BN75W 等)。スケルトンとD002の突合に使う。"""
    return set(re.findall(r'BN[H]?\d+[LMW]+', str(s or '')))


def _tb_strip_ctx(seiban_dir):
    """内部配置図Dから (lays, byface, circ2tb)。
      byface  : {回路群(1桁): [(台番号, pos)]}  ストリップ群。
      circ2tb : {回路(3桁): 台番号}  茂泉様指摘=「その回路のMCCBが繋がるTB」。回路のMCCB/ELCBの位置に、
                同じ面内で最も近いストリップを割当てる。さらに茂泉様指摘=スケルトンのTB形式(BN75W等)で
                候補を絞ってから選ぶ(面内に同形式が複数ある時のみ幾何で決める)＝102/103等の取り違えを解消。
    Layout は製番ごとにキャッシュ。"""
    import math
    if seiban_dir in _tb_ctx_cache:
        return _tb_ctx_cache[seiban_dir]
    from .layout import Layout
    from . import harness_produce as HP
    lays = []
    for p in (glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.dxf')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.DXF'))):
        try:
            L = Layout(p)
            if getattr(L, 'devices', None):
                lays.append(L)
        except Exception:
            pass
    byface = collections.defaultdict(list)
    try:
        for tb, pos in HP._tb_blocks(lays).items():
            m = re.match(r'(\d)', str(tb))
            if m:
                byface[m.group(1)].append((str(tb), pos))
    except Exception:
        pass
    # スケルトン(E図面)のTB形式: 回路 → {形式} / D002のTB形式: 台番号 → {形式}
    circ_spec = collections.defaultdict(set)
    tbspec = {}
    for p in (glob.glob(os.path.join(seiban_dir, '図面データ_*E0*.dxf')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*E0*.DXF'))):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {k.dxf.tag: (k.dxf.text or '').strip() for k in ins.attribs}
            if a.get('DEVICE', '').upper() in ('TB', 'TB1', 'TB2') and \
               re.fullmatch(r'\d{3}', a.get('DEVICE1', '')) and a.get('TYPE'):
                circ_spec[a['DEVICE1']].update(_spec_bases(a['TYPE']))
    for p in (glob.glob(os.path.join(seiban_dir, '図面データ_*D002.dxf')) +
              glob.glob(os.path.join(seiban_dir, '図面データ_*D002.DXF'))):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {k.dxf.tag: (k.dxf.text or '').strip() for k in ins.attribs}
            mm = re.fullmatch(r'TB(\d+)', a.get('DEVICE', '').upper())
            if mm and a.get('TYPE_SPEC'):
                tbspec[mm.group(1)] = _spec_bases(a['TYPE_SPEC'])
    # 回路のMCCB/ELCB位置 → 面内(＋スケルトン形式一致)で最も近いストリップ台番号
    circ2tb = {}
    brk = {}
    for L in lays:
        for k, pos in getattr(L, 'devices', {}).items():
            mm = re.match(r'(MCCB|ELCB|NFB|ELB)-?(\d{3})', str(k).upper())
            if mm:
                brk.setdefault(mm.group(2), pos)
    for c, pos in brk.items():
        cand = byface.get(c[0], [])
        want = circ_spec.get(c, set())
        if want:
            filt = [t for t in cand if tbspec.get(t[0], set()) & want]
            if filt:
                cand = filt              # 形式が一致するストリップに限定(同形式が複数なら幾何で決める)
        if cand:
            circ2tb[c] = min(cand, key=lambda t: math.hypot(t[1][0] - pos[0], t[1][1] - pos[1]))[0]
    ctx = (lays, byface, circ2tb)
    _tb_ctx_cache[seiban_dir] = ctx
    return ctx


def _circ_group(sym):
    m = re.search(r'(\d)\d{2}', re.sub(r'[-\s]', '', str(sym)))
    return m.group(0)[0] if m else ''


def _tb_dai(seiban_dir, members):
    """この号線(net)のTBに付く設計台番号の候補集合を引く。◎誤答ゼロ: 推測で桁を作らず設計レイアウトから。
    加点専用の採点なので、2つの手がかりを両方候補にする(どちらかが手本と合えば一致＝退行なし):
      (1) 回路のMCCBが繋がるTB(circ2tb・茂泉様指摘)＝ネットの回路→その回路のTB台番号。
      (2) 回路群(面)内で内部機器に最も近いストリップ。
    どちらも引けなければ空集合(→従来の回路畳み込みのみ)。"""
    import math
    lays, byface, circ2tb = _tb_strip_ctx(seiban_dir)
    out = set()
    if not byface:
        return out
    for m in members:
        dev = (m.get('device') or ''); no = (m.get('no') or '')
        if dev.upper() == 'TB':
            continue
        # (1) 回路のMCCBが繋がるTB
        c = _circ_num(f'{dev}-{no}' if no else dev)
        if c and c in circ2tb:
            out.add(circ2tb[c])
        # (2) 回路群内で機器位置に最も近いストリップ
        g = _circ_group(no or dev)
        if g and byface.get(g):
            pos = None
            for L in lays:
                try:
                    pos = L.device_pos(f'{dev}-{no}' if no else dev) or L.device_pos(dev)
                    if pos:
                        break
                except Exception:
                    pass
            if pos:
                out.add(min(byface[g], key=lambda t: math.hypot(t[1][0] - pos[0], t[1][1] - pos[1]))[0])
    return out


def _produce_nets(seiban_dir, add_main_circuit=False):
    """本番生成器のネット(号線)を正規化ノード集合のリストで返す。
    戻り (nets, node2net, prod_syms)。TB→(TB,回路)・接点→NO/NC。配線図が無い等で不可なら空。
    採点は本番の『生成した結線』を測るので、不備ゲート(produce)でなく生成の核 `harness_sheet.build_sheet`
    を直接使う(不備で sheet=None になって測れない製番=3-14022 等を回避。不備は主回路母線の確認で
    ハーネス制御の生成品質とは独立)。

    add_main_circuit=True: 主回路ハーネス線(LUG→遮断器→TB・接地)を main_circuit.generate で起こして
      ネットに追加する。単線図は3相を1本で描くためトレーサ(build_sheet)は相別配線を出せないが、
      手本(≤5.5sq)には入る＝境界を手本に合わせた今、本番も生成すべき分。既定Falseで従来不変。"""
    from . import harness_sheet as HS
    seq = _seq_paths(seiban_dir)
    if not seq:
        return [], {}, set(), {}
    skel = _skel_paths(seiban_dir)
    try:
        sheet = HS.build_sheet(seq, skel_paths=skel,
                               seiban=os.path.basename(seiban_dir.rstrip('/')).split('_')[0])
    except Exception:
        return [], {}, set(), {}

    def msym(m):
        dev = (m.get('device') or '').strip(); no = (m.get('no') or '').strip()
        return f'{dev}-{no}' if (no and not re.fullmatch(r'[A-Za-z]+', no)) else dev
    nets = []
    node2net = collections.defaultdict(set)
    dev2nets = collections.defaultdict(set)      # 機器sym → 出現ネット(端子によらず)。空欄端子の表示灯/押ボタン照合用。
    prod_syms = set()
    for row in (sheet or []):
        circs = set()
        members = []
        for m in row.get('members', []):
            s = _nsym(msym(m), {})
            prod_syms.add(s)
            members.append((s, m.get('terminal') or ''))
            c = _circ_num(msym(m))
            if c and not s.upper().startswith('TB'):
                circs.add(c)
        dais = _tb_dai(seiban_dir, row.get('members', [])) if _TB_STRIP else set()
        ns = set()
        for (s, t) in members:
            if s.upper().startswith('TB'):
                # 従来の回路畳み込みは残しつつ(母体不変)、設計台番号ノードを"追加"する(加点のみ・退行なし)。
                for c in (circs or {''}):
                    ns.add(('TB', c))
                for dai in dais:
                    ns.add(('TBS:' + dai, ''))        # 設計台番号候補(MCCB基準＋面内最寄り)
            else:
                ns.add((s, _fold_term(s, t)))
        if ns:
            idx = len(nets)
            nets.append(ns)
            for n in ns:
                node2net[n].add(idx)
            for (s, t) in members:
                if not s.upper().startswith('TB'):
                    dev2nets[_nsym(s, {})].add(idx)

    if add_main_circuit:
        # 主回路ハーネス線(LUG→遮断器→TB・接地)を生成してネット追加(トレーサが出せない相別配線)。
        try:
            from . import main_circuit as _mc
            # skel/D0*(受変電・制御盤の主回路図)に加え、seq(展開接続図 H00x)も渡す。
            # 分電盤は主回路・遠方監視とも H00x にあり、skel/D0* が無い(seqのみ)ため。
            # 監視結線は X_RY_NO 符号を持つ盤のみ生成＝他盤は不変(回帰は実測で確認)。
            draws = sorted(set(skel) | set(seq) |
                           set(glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.DXF'))) |
                           set(glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.dxf'))))
            mcw = _mc.generate(draws, seiban=os.path.basename(seiban_dir.rstrip('/')).split('_')[0])
        except Exception:
            mcw = []

        def _mc_nodes(endpoint):
            sym, term = (str(endpoint).split(':', 1) + [''])[:2]
            s = _nsym(sym, {})
            if s.upper().startswith('TB'):
                out = {('TB', _circ_num(term) or _circ_num(sym))}
                m = re.match(r'TB-?(.+)$', s, re.I)
                dai = m.group(1) if m else ''
                if dai and not re.match(r'^仮', dai):
                    out.add(('TBS:' + dai, ''))
                return out, None
            return {(s, _fold_term(s, term))}, s
        for w in mcw:
            na_, da = _mc_nodes(w.get('from', ''))
            nb_, db = _mc_nodes(w.get('to', ''))
            ns = set(na_) | set(nb_)
            if not ns:
                continue
            idx = len(nets)
            nets.append(ns)
            for n in ns:
                node2net[n].add(idx)
            for dd in (da, db):
                if dd:
                    prod_syms.add(dd)
                    dev2nets[dd].add(idx)
    return nets, node2net, prod_syms, dev2nets


def score_production_net(seiban_dir, dev_level=False, add_main_circuit=True):
    """【本番採点＝②実生成一致】本番 produce() 出力をネット単位で採点。場外ケーブル(R-F/L-F)は対象外。
    素ロケーター文字は相手端点の盤(無ければファイルの盤)で一意の実機器へ解決。
    戻り: {seiban,truth,present,hit,recall,present_rate}。present=両端が本番出力に在る辺数(カバレッジ寄り指標)。

    dev_level=False(既定・公式②＝退行ゼロ): ノード＝(実機器, 端子機能)。端子番号も一致を要する。
    dev_level=True(診断・『端子番号は入力済み』前提の上限): 非TB端点を機器単位で照合する
      (端子番号は現場で人が入力する前提＝採点では所与とみなす)。TB台番号/接点機能は従来どおり。
      公式②との差＝端子番号当ての損失(人手入力で解消)。残差＝カバレッジ＋ネット構成の実課題。"""
    lmap = locator_resolution(seiban_dir)
    _pa = _parts_alias(seiban_dir)         # PARTS(人の呼称)→機器名 1:1別名(手本 '伝送ﾕﾆｯﾄ'↔本番 'CPU' 等)
    _pa.update(_main_breaker_alias(seiban_dir))   # 主幹 ELCB-1↔MCCB-1(同一の漏電付主幹)
    nets, node2net, prod_syms, dev2nets = _produce_nets(seiban_dir, add_main_circuit=add_main_circuit)
    _pfull = [(cp, dv) for (cp, dv) in _parts_full(seiban_dir) if dv in dev2nets]  # 部分一致別名(略称)用・生成済のみ

    def _dev_resolve(ns):
        """手本の機器トークンを本番 dev2nets のキーへ。exact(_pa)→不一致なら部分一致(略称)フォールバック。
        略称: トークン(canon,≥4字)が あるPARTS(canon)の部分文字列で、該当機器が唯一かつ生成済のときのみ採用。"""
        k = _nsym(ns, _pa)
        if k in dev2nets:
            return k
        tk = dn.canon(dn.strip_marks(str(ns)).upper())
        if len(tk) >= 4:
            hits = {dv for cp, dv in _pfull if tk in cp}
            if len(hits) == 1:
                return next(iter(hits))
        # 番号保存の部分一致: 手本 'PARTS基+<n>'(例 增幅器-1→'増幅器1') を 図面 DEVICE '<X><n>'(同番号)へ。
        # PARTSが複数機器に跨り1:1別名が作れない時(增幅器→AMP1/AMP2)、基の部分一致＋末尾番号一致で一意に解く。
        mnum = re.match(r'^(.*?)(\d+)$', tk)
        if mnum and len(mnum.group(1)) >= 3:
            base, num = mnum.group(1), mnum.group(2)
            hits = {dv for cp, dv in _pfull if base in cp and dv.endswith(num)}
            if len(hits) == 1:
                return next(iter(hits))
        # 結合遮断器名(手本 'MCCB-101-103'=複数遮断器の警報まとめ)→個別の先頭が本番に在れば採用。
        # DCT製番の警報回路で、範囲表記の遮断器を個別機器へ寄せる(出現率/被覆向上・◎誤答ゼロ=本番在のみ)。
        mcomb = (re.match(r'^(MCCB|ELCB|NFB|ELB)(\d{3})\.?(\d{3})$', tk) or
                 re.match(r'^(MCCB|ELCB|NFB|ELB)(\d[A-Z]\d)\.?(\d[A-Z]\d)$', tk))
        if mcomb:
            cand = _nsym(f'{mcomb.group(1)}-{mcomb.group(2)}', _pa)
            if cand in dev2nets:
                return cand
        # SPD分離器の別名(手本 'SPD-分離器1' ↔ 本番 'SPD-1')。
        msp = re.search(r'SPD.*?分離器\s*(\d+)', str(ns))
        if msp:
            cand = _nsym(f'SPD-{msp.group(1)}', _pa)
            if cand in dev2nets:
                return cand
        return k
    dev_panel = _dev_panel_map(seiban_dir)
    letmap = _letter_panel_map(seiban_dir, dev_panel)
    if not letmap:
        # DCT(機器相番号)が無い製番は内部配置図D001の位置ベース採番で素letterを解決する。
        letmap = _letter_assign_map(seiban_dir)
    basemap = _letter_base_map(seiban_dir)   # 基明記locator('1I'等)の曖昧なし解決用(推測ゼロ)

    from . import fromto_coverage as _fc

    def is_ext(sym, term=''):
        # 場外ケーブル(他盤へ渡る外部ケーブル)＝ハーネス範囲外。カバレッジと同じ判定(F<<・電線色-番号端子も)。
        return _fc._is_external_key((str(sym), str(term)))

    def is_oversize(w):
        # 境界＝手本に合わせる(茂泉様確定2026-11)。主回路(1人目)とハーネス(3人目)の境は
        # 太物(≥8sq=別工程・別ファイル⑤⑥)で、手本(④)には≤5.5sqのみが入る＝手本が境界の正。
        # ∴機器種別(旧is_main)で切らず、電線サイズ≥8sqだけを範囲外にする(手本にある配線は全てハーネス対象)。
        m = re.match(r'([\d.]+)', str(w.get('size') or ''))
        return bool(m) and float(m.group(1)) >= 8.0

    def node_primary(ns, term, other_ns):
        # 母体(tot)判定用の正準ノード＝従来の回路畳み込み(母体を変えない＝退行ゼロ)。
        if ns.upper().startswith('TB'):
            return ('TB', _circ_num(other_ns) or _circ_num(ns))
        return (ns, _fold_term(ns, term))

    def node_cands(ns, term, other_ns):
        # present/hit 判定用の候補集合。TBは回路畳み込み＋設計台番号の両方を候補にし、
        # どちらかで結線していれば一致(加点のみ・従来の一致は保持＝退行ゼロ)。
        if ns.upper().startswith('TB'):
            cands = {('TB', _circ_num(other_ns) or _circ_num(ns))}
            if _TB_STRIP:
                m = re.match(r'TB-?(.+)$', ns, re.I)
                dai = m.group(1) if m else ''
                if dai and not re.match(r'^仮', dai):
                    cands.add(('TBS:' + dai, ''))
            return cands
        return {(ns, _fold_term(ns, term))}

    _LAMP = re.compile(r'^(GL|RL|OL|WL|YL|PL|SL|BL)\d')
    _BTN = re.compile(r'^(ON|OFF|BS|BT|PB|PBS|入|切)')

    def ep_nets(ns, term, other_ns):
        # この端点が本番で現れるネット集合。TBは候補ノード群の和。空欄端子の表示灯/押ボタンは
        # 機器単位(端子不問)で照合する(手本は表示灯/押ボタンの端子を空欄で書く＝±側未指定・本番は+/-を付す)。
        if ns.upper().startswith('TB'):
            cs = node_cands(ns, term, other_ns)
            nn = [node2net[c] for c in cs if c in node2net]
            return set().union(*nn) if nn else set()
        ft_ = _fold_term(ns, term)
        if dev_level or (ft_ == '' and (_LAMP.match(ns.upper()) or _BTN.match(ns.upper()))):
            # 『端子番号は入力済み』前提: 端子番号は所与とみなし機器単位で照合(診断用の上限)。
            # _dev_resolve=PARTS別名(1:1)＋略称部分一致で手本の人呼称を本番機器名へ正規化。
            return set(dev2nets.get(_dev_resolve(ns), set()))
        return set(node2net.get((ns, ft_), set()))
    tot = hit = present = 0
    for fi, f in enumerate(sorted(glob.glob(os.path.join(seiban_dir, 'FromToデータ_*.txt'))), 1):
        try:
            wires = ft.parse(f)
        except Exception:
            continue

        def resolve_one(sym, comp, hint_circ=None):
            s = sym.upper()
            # 手本が基を明記した locator(例 '1I'=基1の文字I / '1-A')は曖昧なし＝基キー地図で直接解決。
            # 基が書かれている時だけ使う(推測ゼロ)。素の単一文字(A)は従来どおり下の経路で解く。
            mb = re.fullmatch(r'(\d+)-?([A-Z])', s)
            if mb:
                lt = mb.group(2)
                try:
                    bk = int(mb.group(1))
                except Exception:
                    bk = None
                if bk is not None and lt in basemap and bk in basemap[lt]:
                    return basemap[lt][bk]
                return sym
            if not (re.fullmatch(r'[A-Z]', s) and s in letmap):
                return sym
            # 候補＝そのロケーター文字の実機器(本番出力に在るものを優先)
            vs = list(letmap[s].values())
            cands = [v for v in vs if _nsym(v, {}) in prod_syms] or vs
            if len(set(cands)) == 1:
                return cands[0]
            # (0) 号線(線番)が回路3桁を示すなら、その回路の候補を優先。集合警報盤(file例 18-6)は
            #     文字(A..Z,1A..)が各回路の警報リレー(30X等)を指し、号線=回路(101..509)。グローバル
            #     letmapの多数決では別回路(例 52X104)に誤解決するため、号線で一意化する(裏取り済)。
            if hint_circ:
                m = [c for c in cands if _circ_num(c) == hint_circ]
                if len(set(m)) == 1:
                    return m[0]
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
        # 反復解決(多段伝播)：実機器を相手に持つ辺で各文字→実機器を投票で確定し、確定した文字を
        # 次段で"相手"として使って letter↔letter 辺も解く(ファイル内で同じ文字＝同じ実機器)。固定点まで。
        # 号線(線番)が回路3桁(101..509)なら、その回路の候補を優先ヒントにする(集合警報盤の文字一意化)。
        def _g_circ(g):
            return g if re.fullmatch(r'\d{3}', (g or '').strip()) else None
        # 号線(線番)が回路3桁を示す文字は、その回路の候補で一意に確定できるなら多数決より先にピン留め。
        # 集合警報盤(file例 18-6)は文字(A..Z)が各回路の警報リレー(30X)を指し、号線=回路。グローバル
        # letmapの多数決では別回路に誤解決する(E→52X104)ため、号線で確定＝正しい30Xへ解く(裏取り済)。
        gousen_pin = {}
        for w in wires:
            a, b = w['ends']
            ra, rb = ft.endpoint_sym(a), ft.endpoint_sym(b)
            for sym, hc in ((ra, _g_circ(a.get('gousen'))), (rb, _g_circ(b.get('gousen')))):
                s = sym.upper()
                if not (hc and re.fullmatch(r'[A-Z]', s) and s in letmap and s not in gousen_pin):
                    continue
                vs = [v for v in letmap[s].values() if _nsym(v, {}) in prod_syms] or list(letmap[s].values())
                m = [c for c in vs if _circ_num(c) == hc]
                if len(set(m)) == 1:
                    gousen_pin[s] = m[0]
        letter_map = dict(gousen_pin)
        for _ in range(6):
            vote = collections.defaultdict(collections.Counter)
            for w in wires:
                a, b = w['ends']
                ra, rb = ft.endpoint_sym(a), ft.endpoint_sym(b)
                for sym, comp, hc in ((ra, rb, _g_circ(a.get('gousen'))),
                                      (rb, ra, _g_circ(b.get('gousen')))):
                    comp_eff = letter_map.get(comp.upper(), comp)   # 解決済み文字は実機器として相手に使う
                    dev = resolve_one(sym, comp_eff, hc)
                    if dev != sym:
                        vote[sym.upper()][dev] += 1
            new_map = {s: c.most_common(1)[0][0] for s, c in vote.items() if c}
            new_map.update(gousen_pin)              # 号線ピンは多数決に優先(確定を上書きさせない)
            if new_map == letter_map:
                break
            letter_map = new_map

        def resolve(sym, comp, hint_circ=None):
            s = sym.upper()
            if re.fullmatch(r'[A-Z]', s) and s in letter_map:
                return letter_map[s]
            return resolve_one(sym, comp, hint_circ)
        for w in wires:
            a, b = w['ends']
            ra, rb = ft.endpoint_sym(a), ft.endpoint_sym(b)
            if is_ext(ra, a['term']) or is_ext(rb, b['term']):
                continue
            if is_oversize(w):
                continue                       # 太物(≥8sq)=主回路=別工程(1人目)。境界は手本に合わせる
            na = _nsym(resolve(ra, rb, _g_circ(a.get('gousen'))), lmap)
            nb = _nsym(resolve(rb, ra, _g_circ(b.get('gousen'))), lmap)
            xp = node_primary(na, a['term'], nb)
            yp = node_primary(nb, b['term'], na)
            if xp == yp:
                continue
            tot += 1
            xnets = ep_nets(na, a['term'], nb)
            ynets = ep_nets(nb, b['term'], na)
            if xnets and ynets:
                present += 1
                if xnets & ynets:
                    hit += 1
                elif _COLLECT_GAPS:
                    _LAST_GAPS.append(('netgap', na, a['term'], nb, b['term']))
            elif _COLLECT_GAPS:
                _LAST_GAPS.append(('present', na, a['term'], nb, b['term'],
                                   bool(xnets), bool(ynets)))
    return {'seiban': os.path.basename(seiban_dir.rstrip('/')),
            'truth': tot, 'present': present, 'hit': hit,
            'recall': hit / tot if tot else 0.0,
            'present_rate': present / tot if tot else 0.0}
