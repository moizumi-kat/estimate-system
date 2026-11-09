# -*- coding: utf-8 -*-
"""端子番号ルールの手本照合(学習)と確定判定。

2人目(端子番号割付)の推論端子番号(review_viewer の tier='inferred')について、
『手本(FromToデータ)から導いたルールで手本どおりの答えを出せるもの』を確定(青)に
格上げするための仕組み。

方針(◎誤答ゼロ):
  - ルールの単位は (機器タイプ × 端子パターン)。例: (RU4S, a接点), (52=遮断器, b接点)。
  - 全手本製番で、推論値と手本の端子番号を突き合わせる。
  - 手本が番号を記載している箇所で、一致のみ・矛盾ゼロ の組合せだけを『確定可(proven)』とする。
    1件でも手本と食い違えば、その組合せは確定にせず要確認のまま残す。
  - 手本に番号が無い組合せ(未確認)も、安全側で要確認のまま。
  - 担当者が全件を確認・修正し、その結果を手本に積み増して再学習すると proven が磨かれる。

proven 集合は学習の成果物(JSON)。本番(手本の無い新製番)では proven 集合を参照して
確定/要確認を判定する(適用時に手本は不要)。
"""
import glob
import json
import os
import re
from collections import defaultdict

from . import contact_pins as _cp

_HERE = os.path.dirname(os.path.abspath(__file__))
PROVEN_PATH = os.path.join(_HERE, 'terminal_rules_proven.json')
# 現場確認の修正学習(可変状態)。環境変数 HARNESS_STATE があればそこへ外部化し、
# 再デプロイ(git pull)でコード木が更新されても運用中の学習が巻き戻らないようにする。
# 未指定ならパッケージ内(ローカル挙動は不変)。base(手本学習)の proven とは別ファイルに分離。
CORRECTIONS_NAME = 'terminal_corrections.json'


def corrections_path():
    """現場修正学習ファイルのパス(HARNESS_STATE優先)。"""
    st = os.environ.get('HARNESS_STATE')
    if st:
        try:
            os.makedirs(st, exist_ok=True)
        except Exception:
            pass
        return os.path.join(st, CORRECTIONS_NAME)
    return os.path.join(_HERE, CORRECTIONS_NAME)


def load_corrections(path=None):
    """現場確認で学習した端子番号の修正(採用済)を読む: {'term': {'<type>|<rank>': num}}。無ければ空。"""
    p = path or corrections_path()
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        return {'term': dict(d.get('term', {}))}
    except Exception:
        return {'term': {}}


def norm(s):
    return re.sub(r'[-\s　]', '', (s or '').upper())


def devtype(dev):
    """機器名 → 機器タイプ(相番・回路番号を除いた識別子)。
    例: 52-101→52, MCCB-105→MCCB, FAN-305→FAN, 30X1A→30X, SPDX1→SPDX,
        27X→27X, BZ1→BZ, OL1A→OL。"""
    d = (dev or '').upper().strip()
    if '-' in d:
        d = d.split('-', 1)[0]            # 'MCCB-105'->'MCCB', '52-101'->'52'
    m = re.match(r'\d*[A-Z]+', d)          # 先頭の(数字+)英字まで。末尾の相番は捨てる
    return m.group(0) if m else (d or '?')


def pattern(term):
    """端子番号 → パターン。ルールの種類を表す。"""
    t = (term or '').strip()
    if t in ('+', '-'):
        return '極性'
    if t.upper() in ('A1', 'A2'):
        return 'コイルA'
    if re.fullmatch(r'[1-4][34]', t):
        return 'a接点'
    if re.fullmatch(r'[1-4][12]', t):
        return 'b接点'
    if re.fullmatch(r'[1-6]', t):
        return '主回路'
    return '他'


def rule_key(dev, term):
    """(機器タイプ, パターン) の確定判定キー。"""
    return (devtype(dev), pattern(term))


def is_selector(dev):
    """選択/切替SW(43/CS/COS/SS)か。接点番号が切替位置依存のため、標準規約(NO/NC)では
    一意に決まらず、自動確定の対象外(要確認)とする。"""
    return bool(re.match(r'(43|CS|COS|SS)', devtype(dev)))


def func(term, base=''):
    """端子番号 → 機能。照合は『同機能=電気的に等価=許容』で行う(現場ルール 2026-10-16:
    リレー/接触器の同種接点は等価、割付は空き端子を小さい順でよい)。
    返り値: polarity(+/-)/earth(E)/alarm(AL*)/coil/pole_in/pole_out/NO/NC/selector/
            meter(CA,CB,CL,CM,1L.. 等の計器端子)/None。"""
    t = re.sub(r'[ 　]+', '', str(term or ''))
    t = re.sub(r'[上下左右]$', '', t)              # 位置接尾(上/下/左/右)を外す
    if t in ('+', '-'):
        return 'polarity'
    u = t.upper()
    if u == 'E':
        return 'earth'
    if re.match(r'AL', u):                          # ALa/ALc/ALm 警報接点
        return 'alarm'
    f = _cp.function(t, base)
    if f:
        return f
    if re.match(r'[A-Z]', u):                       # 計器等の英字端子(CA/CB/CL/CM/1L/1S..)
        return 'meter'
    return None


def truth_terms(seiban, harness_dir):
    """手本(製番) → {正規化機器名: set(正規化端子)}。空欄端子も '' として含む。"""
    from . import fromto_truth as ft
    tr = defaultdict(set)
    for f in sorted(glob.glob(os.path.join(harness_dir, f'{seiban}-[0-9].txt'))):
        for w in ft.parse(f):
            for e in w['ends']:
                tr[norm(ft.endpoint_sym(e))].add(norm((e['term'] or '').strip()))
    return tr


def truth_funcs(seiban, harness_dir):
    """手本(製番) → {正規化機器名: set(機能)}。照合は機能(同機能=等価)で行う。"""
    from . import fromto_truth as ft
    trf = defaultdict(set)
    for f in sorted(glob.glob(os.path.join(harness_dir, f'{seiban}-[0-9].txt'))):
        for w in ft.parse(f):
            for e in w['ends']:
                term = (e['term'] or '').strip()
                if term:
                    trf[norm(ft.endpoint_sym(e))].add(
                        func(term, devtype(ft.endpoint_sym(e))))
    return trf


def type_funcs(harness_dir, seibans):
    """全手本から 機器タイプ → set(機能) を集める(機器タイプが正しく持つ端子機能の語彙)。
    手本は配線された端子しか載らず個体ごとに欠落するため、照合はタイプ単位で行う
    (例: MCCBの警報接点ALa/ALcは個体によっては別名『104.105』や未配線で欠けるが、
    タイプとしては正当な端子)。"""
    from . import fromto_truth as ft
    tf = defaultdict(set)
    for sb in seibans:
        for f in sorted(glob.glob(os.path.join(harness_dir, f'{sb}-[0-9].txt'))):
            for w in ft.parse(f):
                for e in w['ends']:
                    term = (e['term'] or '').strip()
                    if term:
                        tf[devtype(ft.endpoint_sym(e))].add(
                            func(term, devtype(ft.endpoint_sym(e))))
    return {t: (s - {None}) for t, s in tf.items()}


def type_exact(harness_dir, seibans):
    """全手本から 機器タイプ → set(正規化端子番号)。選択SW等、機能照合では
    区別できない機器を実番号で照合するために使う。"""
    from . import fromto_truth as ft
    te = defaultdict(set)
    for sb in seibans:
        for f in sorted(glob.glob(os.path.join(harness_dir, f'{sb}-[0-9].txt'))):
            for w in ft.parse(f):
                for e in w['ends']:
                    term = (e['term'] or '').strip()
                    if term:
                        te[devtype(ft.endpoint_sym(e))].add(
                            re.sub(r'[上下左右]$', '', norm(term)))
    return te


def learn(harness_dir, seibans):
    """手本のある製番群から (機器タイプ×パターン) ごとの機能一致/不一致を集計し、
    proven(確定可)集合を学習する。照合は『同機能=電気的に等価=許容』(現場ルール)で、
    機器タイプ単位の機能語彙に対して行う(個体ごとの手本欠落に左右されない)。
    戻り: {'stats':..., 'proven':[[type,pat],...], 'contradicted_types':[...]}。"""
    from . import review_viewer as rv
    tf = type_funcs(harness_dir, seibans)
    te = type_exact(harness_dir, seibans)
    stats = defaultdict(lambda: {'match': 0, 'mismatch': 0, 'unseen': 0,
                                 'mism_ex': []})
    for sb in seibans:
        dxfs = sorted(glob.glob(os.path.join(harness_dir, f'{sb}-F*.DXF')) +
                      glob.glob(os.path.join(harness_dir, f'{sb}-F*.dxf')))
        for dxf in dxfs:
            try:
                data = rv.extract(dxf)
            except Exception:
                continue
            for dev, term, x, y, tier in data['terminals']:
                if tier != 'inferred':
                    continue
                k = rule_key(dev, term)
                t = devtype(dev)
                f = func(term, t)
                st = stats[k]
                # 選択SW等(位置接点)は機能が一律'selector'で区別できないため実番号で照合。
                if f == 'selector':
                    exact = te.get(t, set())
                    if not exact:
                        st['unseen'] += 1
                    elif re.sub(r'[上下左右]$', '', norm(term)) in exact:
                        st['match'] += 1
                    else:
                        st['mismatch'] += 1
                        if len(st['mism_ex']) < 5:
                            st['mism_ex'].append([sb, dev, term, sorted(exact)[:8]])
                    continue
                vocab = tf.get(t, set())
                if vocab:
                    if f in vocab:
                        st['match'] += 1       # タイプが持つ機能=許容
                    else:
                        st['mismatch'] += 1
                        if len(st['mism_ex']) < 5:
                            st['mism_ex'].append([sb, dev, term,
                                                  sorted(str(s) for s in vocab)[:6]])
                else:
                    st['unseen'] += 1
    proven = [list(k) for k, st in stats.items()
              if st['match'] > 0 and st['mismatch'] == 0]
    # 手本と1件でも矛盾した機器タイプ(=②: 推論が誤っている)。安全側で
    # このタイプの推論端子はリレー規約では確定にしない。
    contradicted = sorted({k[0] for k, st in stats.items() if st['mismatch'] > 0})
    # ②(非リレー機器: FAN/SPD/TH/WHM 等)の固有端子テンプレートを手本から学習。
    templates = learn_device_templates(harness_dir, seibans, set(contradicted))
    # 番号を持たない機器タイプ(手本で端子番号が空欄=番号なしが支配的)。例: FAN(交流送風機)。
    # これらは接点番号を付けず『番号なし(確定)』とする(推論の+/-等の誤番号を出さない)。
    blank_types = sorted(learn_blank_types(harness_dir, seibans, set(contradicted)))
    # 切替SW(LS_CHROB等, 記号で機能判定できない)の位置テンプレートを図面記載から学習。
    selector_templates = learn_selector_templates(harness_dir, seibans)
    # 選択SW(位置接点)の実在端子『在庫』を手本から学習(小さい順)。手本は同機能の端子から
    # 一番小さい番号を選ぶため、在庫を小さい順に並べ、検出接点へ小さい順で割り付ける。
    selector_inv = {t: sorted(terms, key=_term_sort_key)
                    for t, terms in te.items() if re.match(r'(43|CS|COS|SS)', t)}
    out = {'proven': sorted(proven),
           'contradicted_types': contradicted,
           'device_templates': templates,
           'blank_types': blank_types,
           'selector_templates': selector_templates,
           'selector_inventory': selector_inv,
           'stats': {f'{k[0]}|{k[1]}': {kk: vv for kk, vv in st.items()}
                     for k, st in stats.items()}}
    return out


def _term_sort_key(t):
    """端子番号の小さい順ソートキー。数字は数値順、他は文字列順。"""
    t = str(t)
    return (0, int(t)) if t.isdigit() else (1, t)


def learn_device_templates(harness_dir, seibans, target_types, min_ratio=0.6):
    """②非リレー機器タイプの固有端子を手本から学習する。
    各機器の端子を上(y大)から順位付けし、接続号線で手本の実端子名に対応させて、
    『タイプ×順位 → 端子名』の多数決テンプレートを作る。戻り: {type: {str(rank): name}}。
    本番(手本なし)では order だけで端子名を割付できる。"""
    from . import stage_interactive as SI
    from . import fromto_truth as ft
    votes = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for sb in seibans:
        seq = sorted(glob.glob(os.path.join(harness_dir, f'{sb}-F*.DXF')) +
                     glob.glob(os.path.join(harness_dir, f'{sb}-F*.dxf')))
        skel = sorted(glob.glob(os.path.join(harness_dir, f'{sb}-E*.DXF')) +
                      glob.glob(os.path.join(harness_dir, f'{sb}-E*.dxf')))
        g2t = defaultdict(list)                      # 号線 -> [(type, term)]
        for f in sorted(glob.glob(os.path.join(harness_dir, f'{sb}-[0-9].txt'))):
            for w in ft.parse(f):
                for e in w['ends']:
                    g = (e['gousen'] or '').strip()
                    if g:
                        g2t[g].append((devtype(ft.endpoint_sym(e)),
                                       (e['term'] or '').strip()))
        byD = defaultdict(list)
        try:
            sheets = SI._all_terminals_by_sheet(seq, skel)
        except Exception:
            continue
        for p, items in sheets:
            for it in items:
                # 図面記載(drawn)は番号が既にあり、テンプレートの適用対象外(post-passで尊重)。
                # 学習も適用に合わせ drawn を除外する。例: WHMは電力計本体(E面, 全端子drawn)と
                # パルス接点(F面, CA/CB=inferred)が同タイプで、本体を混ぜると順位投票が濁る。
                if it.get('tier') == 'drawn':
                    continue
                if devtype(it['dev']) in target_types:
                    byD[(p, it['dev'])].append(it)
        for (p, dev), lst in byD.items():
            t = devtype(dev)
            lst.sort(key=lambda z: -z['y'])          # 上(y大)から
            for rank, it in enumerate(lst):
                names = set()
                for g in it.get('gousen', []):
                    for (dt, term) in g2t.get(g, []):
                        if dt == t and term:
                            names.add(term)
                for nm in names:
                    votes[t][rank][nm] += 1
    templates = {}
    for t, ranks in votes.items():
        rt = {}
        for rank, nmc in ranks.items():
            total = sum(nmc.values())
            top, cnt = max(nmc.items(), key=lambda x: x[1])
            if total and cnt >= min_ratio * total:   # 明確な多数(競合が少ない)順位のみ採用
                rt[str(rank)] = top
        if rt:
            templates[t] = rt
    return templates


def learn_blank_types(harness_dir, seibans, target_types, min_ratio=0.9, min_n=3):
    """手本で端子番号が空欄(=番号なし)が支配的な機器タイプを学習する(接地Eは除外)。
    例: FAN(交流送風機)は電源リードに端子番号を付けない(手本は空欄)。推論の+/-は誤りで、
    正解は『番号なし』。これらは接点番号を付けず番号なし(確定)として扱う。戻り: set(type)。"""
    from . import fromto_truth as ft
    cnt = defaultdict(lambda: {'blank': 0, 'num': 0})
    for sb in seibans:
        for f in sorted(glob.glob(os.path.join(harness_dir, f'{sb}-[0-9].txt')) +
                        glob.glob(os.path.join(harness_dir, f'{sb}.txt'))):
            for w in ft.parse(f):
                for e in w['ends']:
                    t = devtype(ft.endpoint_sym(e))
                    if t not in target_types:
                        continue
                    term = (e['term'] or '').strip()
                    base = re.sub(r'[上下左右]$', '', term).strip().upper()
                    if base in ('E', 'ｱｰｽ', 'アース', 'PE'):
                        continue                      # 接地は番号なし判定から除外
                    cnt[t]['num' if term else 'blank'] += 1
    out = set()
    for t, c in cnt.items():
        tot = c['blank'] + c['num']
        if tot >= min_n and c['blank'] >= min_ratio * tot:
            out.add(t)
    return out


def learn_selector_templates(harness_dir, seibans, min_ratio=0.8, min_n=3):
    """選択SW(切替含む)の位置テンプレートを図面記載(drawn)の端子から学習する。
    記号(LS_A/LS_B)で機能判定できない切替SW(LS_CHROB等, 富士AR22PR-311B)の端子番号割付用。
    機器ごとに端子を (y降順, x昇順) で順位付けし、タイプ×順位→端子番号 を多数決。
    図面に番号が書かれた同型(同タイプ)の切替SWから学習し、未記入の同型に適用する。
    戻り: {type: {str(rank): num}}。"""
    from . import review_viewer as rv
    votes = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for sb in seibans:
        for dxf in sorted(glob.glob(os.path.join(harness_dir, f'{sb}-F*.DXF')) +
                          glob.glob(os.path.join(harness_dir, f'{sb}-F*.dxf'))):
            try:
                data = rv.extract(dxf)
            except Exception:
                continue
            byd = defaultdict(list)
            for dev, term, x, y, tier in data['terminals']:
                if is_selector(dev) and tier == 'drawn' and str(term) not in ('', '?'):
                    byd[dev].append((str(term), x, y))
            for dev, lst in byd.items():
                lst.sort(key=lambda z: (-z[2], z[1]))     # 上(y大)→左(x小)の順
                for rank, (term, x, y) in enumerate(lst):
                    votes[devtype(dev)][rank][term] += 1
    templates = {}
    for t, ranks in votes.items():
        rt = {}
        for rank, nmc in ranks.items():
            total = sum(nmc.values())
            top, cnt = max(nmc.items(), key=lambda x: x[1])
            if total >= min_n and cnt >= min_ratio * total:
                rt[str(rank)] = top
        if rt:
            templates[t] = rt
    return templates


def save_proven(learned, path=PROVEN_PATH):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(learned, f, ensure_ascii=False, indent=1)
    return path


# 標準の接点/コイル/極性/主回路パターン。機器タイプが手本と一度も矛盾しなければ、
# これらは JIS/IEC 規約で一意に決まるため確定(方針B)。
STANDARD_PATTERNS = {'a接点', 'b接点', 'コイルA', '極性', '主回路'}


def load_proven(path=PROVEN_PATH):
    """proven 集合を読み込む。無ければ空集合。"""
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return {tuple(x) for x in d.get('proven', [])}
    except Exception:
        return set()


def load_model(path=PROVEN_PATH):
    """確定判定モデルを読み込む: {'proven':set, 'contradicted':set, 'templates':dict}。
    無ければ空(=全推論を要確認)。"""
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return {'proven': {tuple(x) for x in d.get('proven', [])},
                'contradicted': set(d.get('contradicted_types', [])),
                'templates': d.get('device_templates', {}),
                'blank_types': set(d.get('blank_types', [])),
                'selector_templates': d.get('selector_templates', {}),
                'selector_inv': d.get('selector_inventory', {}),
                'corrections': load_corrections()}
    except Exception:
        return {'proven': set(), 'contradicted': set(), 'templates': {},
                'blank_types': set(), 'selector_templates': {}, 'selector_inv': {},
                'corrections': load_corrections()}


def selector_template(dev, model=None):
    """切替SW(記号で機能判定できないもの)の位置テンプレート {str(rank): num} を返す。無ければ None。"""
    if model is None:
        model = load_model()
    return model.get('selector_templates', {}).get(devtype(dev))


def term_correction(dev, rank, model=None):
    """現場確認で学習した端子番号の修正(採用済)を返す。タイプ×順位で引く。
    無ければ None。空文字('')は『番号なし(確定)』の修正。"""
    if model is None:
        model = load_model()
    return model.get('corrections', {}).get('term', {}).get(f'{devtype(dev)}|{rank}')


def is_blank_type(dev, model=None):
    """手本で端子番号が付かない機器タイプ(例: FAN)か。True なら端子は番号なし(確定)。"""
    if model is None:
        model = load_model()
    return devtype(dev) in model.get('blank_types', set())


def selector_inventory(dev, model=None):
    """選択SWの実在端子在庫(小さい順)を返す。無ければ空リスト。"""
    if model is None:
        model = load_model()
    return list(model.get('selector_inv', {}).get(devtype(dev), []))


def selector_func_pairs(dev, model=None):
    """選択SWの在庫を機能別のペア列に分ける。
    a接点(NO=末尾3/4)と b接点(NC=末尾1/2)。戻り: {'a':[(x3,x4)..], 'b':[(x1,x2)..]}。
    富士AR22の体系: 接点ユニット位置①②③④=十の位, a=末尾3/4・b=末尾1/2。"""
    inv = selector_inventory(dev, model)
    a = sorted((t for t in inv if t[-1:] in ('3', '4')), key=_term_sort_key)
    b = sorted((t for t in inv if t[-1:] in ('1', '2')), key=_term_sort_key)
    pairs = {'a': [tuple(a[i:i + 2]) for i in range(0, len(a) - 1, 2)],
             'b': [tuple(b[i:i + 2]) for i in range(0, len(b) - 1, 2)]}
    return pairs


def template_name(dev, rank, model=None):
    """②非リレー機器の固有端子名を、タイプ×順位(上から)のテンプレートで引く。
    テンプレートがあれば端子名、無ければ None。"""
    if model is None:
        model = load_model()
    tp = model.get('templates', {}).get(devtype(dev))
    if not tp:
        return None
    return tp.get(str(rank))


def is_confirmed(dev, term, model=None):
    """推論端子 (dev, term) を確定にできるか(方針B)。
      - 手本と矛盾した機器タイプ(②)は常に要確認(False)。
      - 手本で実証済(proven)なら確定。
      - 標準規約パターンで、機器タイプが無矛盾なら確定。
      - それ以外(不明パターン等)は要確認。"""
    if model is None:
        model = load_model()
    if isinstance(model, set):               # 旧API(proven集合)互換
        model = {'proven': model, 'contradicted': set()}
    t = devtype(dev)
    k = rule_key(dev, term)
    if t in model.get('contradicted', set()):
        return False
    if k in model.get('proven', set()):
        return True
    if is_selector(dev):                 # 選択SWは標準規約で確定しない(位置接点)
        return False
    return pattern(term) in STANDARD_PATTERNS
