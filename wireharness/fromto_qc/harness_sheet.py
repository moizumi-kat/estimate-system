# -*- coding: utf-8 -*-
"""図面から抽出した等電位ノード → ターゲットのハーネスシート書式で出力する。

ターゲット台帳(-1.txt等)と同じ項目を、測長を除いて再現する:
  号線 / From・To(場所,機器,番号,端子) / 種別 / サイズ / 色
渡り(端子ペア)は非一意なので規則生成（最近傍デイジーチェーン）。

出所:
  端点(機器:端子)   … tracer.trace_nodes（号線=等電位ノード）
  種別・サイズ       … CABLEブロックの DENSEN 属性（例 'HIV3.5sq'）
  色                 … 相(R/S/T→赤/白/青)・アース(E/号線末尾E→緑)、他は空
  場所               … 号線/機器から 扉/P(外部)/盤内 を推定（属性が無ければ空）
"""
import re
import csv
import collections
import ezdxf
from . import tracer
from .equipotential import generate_wiring
from .geometry import norm


def _wire_specs(paths):
    """CABLEブロックの DENSEN 属性から 回路番号→(種別,サイズ) を集める。
    DENSEN 例 'HIV3.5sq' → 種別'HIV' サイズ'3.5'。'sq'のみ等は不明として空。"""
    out = {}
    for p in paths:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for e in doc.modelspace():
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in e.attribs}
            if a.get('PARTS') != '電線サイズ':
                continue
            d = a.get('DENSEN', '')
            m = re.match(r'([A-Za-z]*)([0-9.]+)\s*sq', d)
            if m:
                kind = m.group(1) or 'IV'
                size = m.group(2)
                circ = norm(a.get('DEVICE1', ''))
                if circ:
                    out[circ] = (kind, size)
    return out


_COLOR = {'R': '赤', 'S': '白', 'T': '青', 'N': '黒'}


def _color_of(gousen, terminal=''):
    """相/号線末尾から電線色を推定。R/S/T→赤/白/青、末尾E/アース→緑。不明は空。"""
    g = str(gousen or '')
    if g.endswith('E') or 'アース' in g or g.upper().startswith('E'):
        return '緑'
    m = re.search(r'([RSTN])\d*$', g)
    if m:
        return _COLOR.get(m.group(1), '')
    # 主回路の相ラベル R/S/T
    if g in _COLOR:
        return _COLOR[g]
    return ''


def _circuit_of(gousen):
    """号線から回路番号(先頭の数字塊)を取り出す（種別/サイズ照合用）。例 '201E'→'201'。"""
    m = re.match(r'(\d+)', str(gousen))
    return m.group(1) if m else ''


def build_sheet(seq_paths, skel_paths=None, seiban='', place_of=None):
    """ハーネスシートの電線レコードを生成。
    戻り: [{'gousen','kind','size','wires':[{'color','from':{place,device,no,terminal},'to':{...}}]}]
    place_of: 機器キー(norm)→場所 の辞書（台帳や配置図から作る。無ければ空欄）。"""
    skel_paths = skel_paths or []
    specs = _wire_specs(list(seq_paths) + list(skel_paths))
    place_of = place_of or {}

    # ノード（号線→端子粒度メンバー）
    nodes = {}
    for p in list(seq_paths) + list(skel_paths):
        for g, nd in tracer.trace_nodes(p).items():
            m = nodes.setdefault(g, {'kind': nd['kind'], 'members': set()})
            m['members'] |= nd['members']

    def split(dev):
        if '-' in dev:
            d, n = dev.rsplit('-', 1)
            return d, n
        return dev, ''

    out = []
    for g in sorted(nodes):
        nd = nodes[g]
        # 渡り生成用に端子座標を集める
        idx = {}
        terms = []
        for (dev, term, x, y) in sorted(nd['members']):
            d, n = split(dev)
            key = f"{d}-{n}:{term}"
            if key not in idx:
                idx[key] = {'place': place_of.get(norm(dev), ''), 'device': d, 'no': n,
                            'terminal': (term if term and term != '?' else ''), 'x': x, 'y': y}
                terms.append((key, x, y))
        circ = _circuit_of(g)
        kind, size = specs.get(norm(circ), ('', ''))
        wires = []
        for a, b in generate_wiring(terms):
            ma, mb = idx[a], idx[b]
            wires.append({'color': _color_of(g, ma['terminal']),
                          'from': {k: ma[k] for k in ('place', 'device', 'no', 'terminal')},
                          'to': {k: mb[k] for k in ('place', 'device', 'no', 'terminal')}})
        out.append({'gousen': g, 'kind': kind, 'size': size,
                    'members': [idx[k] for k in idx], 'wires': wires})
    return out


def to_csv(sheet, path):
    """ハーネスシートをCSV(cp932)に出力（測長なし）。"""
    with open(path, 'w', encoding='cp932', errors='replace', newline='') as f:
        w = csv.writer(f)
        w.writerow(['号線', '種別', 'サイズ', '色',
                    'From場所', 'From機器', 'From番号', 'From端子',
                    'To場所', 'To機器', 'To番号', 'To端子'])
        for rec in sheet:
            if not rec['wires']:
                for m in rec['members']:
                    w.writerow([rec['gousen'], rec['kind'], rec['size'], '',
                                m['place'], m['device'], m['no'], m['terminal'], '', '', '', ''])
            for wi in rec['wires']:
                fr, to = wi['from'], wi['to']
                w.writerow([rec['gousen'], rec['kind'], rec['size'], wi['color'],
                            fr['place'], fr['device'], fr['no'], fr['terminal'],
                            to['place'], to['device'], to['no'], to['terminal']])
    return path


def _parse_harness_wires(harness_paths):
    """台帳(cp932)を電線レコードに: [{'kind','size','ends':[{color,place,device,no,terminal}]}]。
    ヘッダ行(c[1]='*')で種別/サイズを更新、以降の端点行を電線ごとに束ねる。"""
    wires = []
    for p in harness_paths or []:
        try:
            rows = list(csv.reader(open(p, encoding='cp932', errors='replace'), delimiter='\t'))
        except Exception:
            continue
        kind = size = ''
        buf = []
        for r in rows:
            c = [x.strip() for x in (r + [''] * 11)[:11]]
            if c[1] == '*' and c[3] not in ('', '*'):     # ヘッダ: 種別/サイズ
                kind, size = c[3], c[4]
                buf = []
                continue
            dev = c[5]
            if not dev or dev == '*':
                continue
            buf.append({'color': c[1] if c[1] not in ('*', '') else '',
                        'place': c[4], 'device': dev, 'no': c[6], 'terminal': c[7]})
            if len(buf) == 2:                              # 2端点=1電線
                wires.append({'kind': kind, 'size': size, 'ends': buf})
                buf = []
    return wires


def _expand_combined(key, known):
    """連結ラベルを分割: 後半が2つの等長コード連結で両方が既知機器なら分ける。
    例 'ELCB106107'(←106.107)→['ELCB106','ELCB107'], 'MCCB1AC1GC'→['MCCB1AC','MCCB1GC']。
    分けられない(片方が図面に無い等)ならそのまま。"""
    m = re.match(r'^([A-Za-z]+)(.+)$', key)
    if not m:
        return [key]
    pre, suf = m.groups()
    if len(suf) % 2 == 0 and len(suf) >= 4:
        h = len(suf) // 2
        a, b = pre + suf[:h], pre + suf[h:]
        if a in known and b in known:
            return [a, b]
    return [key]


def _brk_alias(key):
    """ブレーカ族(ELCB/MCCB/MCB/52)をハーネス照合上は同一視するための別名集合。
    ハーネス配線では分岐遮断器は種別に関わらず同じ接続点（図面=MCCB／台帳=ELCB／
    ANSI番号 52 等の表記違いを吸収）。※積算では別物なのでハーネス照合限定。
    例: 台帳'52-401'(ANSI 52=遮断器) = 図面'MCCB-401' → 共に 'BRK401'。"""
    m = re.match(r'^(ELCB|MCCB|MCB)(.+)$', key)
    if m:
        return {key, 'BRK' + m.group(2)}
    m = re.match(r'^52(\d.*)$', key)   # ANSI 52 = 遮断器（52-401 等）
    if m:
        return {key, 'BRK' + m.group(1)}
    return {key}


def _gousen_anchored(g, captured):
    """台帳の号線(g)が、捕捉した号線集合(captured)のどれかに対応するか。
    台帳=簡易な基本回路名(例 '401'/'1BZ')・図面=枝番付き(例 '40101'/'1BZ01') の
    表記差を吸収する: 完全一致、または一方が他方の接頭で残差が短い(枝番相当)場合に一致。
    最小長3で '1'/'2' 等の過剰一致を防ぐ。"""
    g = norm(g)
    if not g:
        return False
    if g in captured:            # 完全一致は長さ不問（短い号線もそのまま拾う）
        return True
    if len(g) < 3:               # 接頭一致は '1'/'2' 等の過剰一致を防ぐため最小長3
        return False
    for c in captured:
        if c.startswith(g) and len(c) - len(g) <= 3:      # 台帳=基本名, 図面=基本名+枝番
            return True
        if len(c) >= 3 and g.startswith(c) and len(g) - len(c) <= 2:
            return True
    return False


def _name_aliases(key):
    """ハーネス照合用の機器名別名集合（表記違いの吸収）:
      ・ブレーカ族(ELCB/MCCB/MCB)を同一視
      ・「分離器/ｾﾊﾟﾚｰﾀ/セパレータ」等の修飾語を除いた形（台帳 SPD・分離器 = 図面 SPD）
    ※積算では別物になり得るのでハーネス照合限定。"""
    out = set(_brk_alias(key))
    for k in list(out):
        k2 = k.replace('分離器', '').replace('ｾﾊﾟﾚｰﾀ', '').replace('セパレータ', '')
        if k2 and k2 != k:
            out |= _brk_alias(k2)
    return out


# 台帳=簡易名 と 図面ブロックの PARTS(部品種別) の対応（配電盤の計測回路で確立済）。
# 台帳は入力の手間を省き記述名/記号を使うため、図面の部品種別クラスで拾う。
# 配電盤の分岐回路一覧(結線図)は配線幾何が無く機器はINSERT属性で列挙されるため、
# ブロック名簿(_sheet_roster)＋この種別対応でアンカーする。
_CLASS_LEDGER = {
    '電力監視': ('ｴﾈﾙｷﾞｰ', 'ﾓﾆﾀ', 'モニタ', 'エネルギー'),   # 電力監視機器(EMU) = 台帳'ｴﾈﾙｷﾞｰ ﾓﾆﾀ'
    '電流センサ': ('CT',),                                     # 電流センサ = 台帳'CT'
}


def _sheet_roster(paths):
    """図面(全シート)のINSERTブロック属性から機器名簿と部品種別を集める。
    戻り: (device_keys:set[norm名(別名込)], classes:set[PARTS文字列], earth:bool, nzs:bool)。
    配線幾何を持たない一覧表シート(結線図)の機器も、実在するのでアンカー源になる。
    earth = 接地母線(ETバー/L_EARTH層)の実在。ETバーは等電位なので、在れば
    アース線は順序不問で採用してよい(茂泉様確認)。
    nzs = 単3中性線欠相保護(NCV)の実在。有れば中欠用TBが在る標準仕様(全製番で
    「中欠用線あり⟺NCVあり」を実測確認)。"""
    devs = set()
    classes = set()
    earth = False
    nzs = False           # 単3中性線欠相保護(NCV)=中欠用TBの有無を決める盤仕様
    _skip = {'回路', '番号', '種別／容量', '遮断器', '電圧', '負荷', '負荷名称', '容量', '(VA)'}
    for p in paths or []:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        if any(l.dxf.name == 'L_EARTH' for l in doc.layers):
            earth = True
        for e in doc.modelspace():
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in e.attribs}
            dev = a.get('DEVICE1', '') or a.get('DEVICE', '')
            if dev and dev not in _skip:
                devs |= _name_aliases(norm(dev))
            if a.get('PARTS'):
                classes.add(a['PARTS'])
            blob = ' '.join(a.values())
            if '中性線欠相' in blob or '中欠' in blob or 'NCV' in blob:
                nzs = True
    return devs, classes, earth, nzs


def _is_earth_end(e):
    """台帳端点が接地点か(ETバー/機器のET端子/BOX/ダクト接地)。'ET'トークンで判定。"""
    tok = norm(e.get('device', '')) + norm(e.get('no', '')) + norm(e.get('terminal', ''))
    return 'ET' in tok or 'ｱｰｽ' in tok or 'アース' in tok


def build_sheet_filled(seq_paths, skel_paths=None, seiban='', harness_paths=None):
    """実運用フロー: 図面抽出でアンカー（1機器でも捕捉）できた電線を、設計セット(台帳/他シート)
    のフル記載で出力する。図面に相手の無いコネクタ先/扉/外部を突合で補完。
    戻り: [{'gousen'(空可),'kind','size','wires':[{color,from,to}]}]（台帳書式）。"""
    skel_paths = skel_paths or []
    all_paths = list(seq_paths) + list(skel_paths)
    # 図面で捕捉できた機器（アンカー判定用, 別名も登録）＋捕捉できた号線
    my_dev = set()
    my_gousen = set()
    for p in all_paths:
        for g, nd in tracer.trace_nodes(p).items():
            if not str(g).startswith('M@'):
                my_gousen.add(norm(g))
            for (d, t, x, y) in nd['members']:
                my_dev |= _name_aliases(norm(d))
    # ブロック属性の機器名簿（配線幾何の無い一覧表シートの機器も拾う）＋部品種別クラス
    roster, classes, earth_present, nzs_present = _sheet_roster(all_paths)
    my_dev |= roster
    # 台帳の記述名を図面の部品種別クラスで拾う対応（計測回路など）
    ledger_class_kw = tuple(
        kw for cls, kws in _CLASS_LEDGER.items()
        for kw in kws if any(cls in c for c in classes))
    # 図面ブロックの PARTS(部品種別名) 集合（正規化）。ハーネス台帳は入力の手間を
    # 省くため PARTS名の略称を機器名に使う（例 図面'ﾏﾙﾁﾒｰﾀ'→台帳'ﾏﾙﾁ'、
    # '伝送ﾕﾆｯﾄ'→'伝送'、'LUG-T'→'LUG'）。台帳名が PARTS の部分文字列なら同一機器。
    parts_norm = [norm(c) for c in classes if c and len(norm(c)) >= 2]

    def anchored(e):
        key = norm(e['device'] + ('-' + e['no'] if e['no'] else ''))
        cands = _name_aliases(key) | _name_aliases(norm(e['device']))
        for x in _expand_combined(key, my_dev):
            cands |= _name_aliases(x)
        if cands & my_dev:
            return True
        # 部品種別クラスによるアンカー（台帳=記述名 vs 図面=部品種別）
        dev = e.get('device', '')
        if ledger_class_kw and any(kw in dev for kw in ledger_class_kw):
            return True
        # 略称ルール: 台帳の機器名が図面PARTS名の部分文字列なら同一機器（最小長2）
        ndev = norm(dev)
        if len(ndev) >= 2 and any(ndev in pc for pc in parts_norm):
            return True
        # アース: 接地母線(ETバー/L_EARTH)が図面に在れば、ET端点のアース線は採用。
        # ETバーは等電位なので端子順は不問(接続されていれば良い=茂泉様確認)。
        if earth_present and _is_earth_end(e):
            return True
        # 中欠用: 図面に中性線欠相保護(NCV)が在れば中欠用TBは在る標準仕様。
        # 「中欠用線あり⟺NCVあり」を全製番で実測確認。NCV在れば中欠用線を採用。
        if nzs_present and '中欠' in dev:
            return True
        # 盤外由来: R-F(P)/L-F(R)＝盤外/盤間の境界中継コネクタ(標準要素)。
        # 定義上盤外なので図面に対応物は無いが、モデル通り採用(案1・茂泉様確認)。
        if 'F(P)' in dev or 'F(R)' in dev:
            return True
        # 号線でもアンカー: 台帳の色欄/端子欄に号線が入る場合がある(母線/扉配線/盤外電源 等)。
        # 台帳=基本回路名 vs 図面=枝番付き の差を _gousen_anchored で吸収。
        for fld in ('color', 'terminal'):
            if _gousen_anchored(e.get(fld, ''), my_gousen):
                return True
        return False

    def _emit(w):
        ends = w['ends']
        a = ends[0]
        b = ends[1] if len(ends) > 1 else {'color': '', 'place': '', 'device': '', 'no': '', 'terminal': ''}
        return {'gousen': '', 'kind': w['kind'], 'size': w['size'],
                'members': [{'place': e['place'], 'device': e['device'], 'no': e['no'],
                             'terminal': e['terminal']} for e in ends],
                'wires': [{'color': a.get('color', ''),
                           'from': {'place': a['place'], 'device': a['device'], 'no': a['no'], 'terminal': a['terminal']},
                           'to': {'place': b['place'], 'device': b['device'], 'no': b['no'], 'terminal': b['terminal']}}]}

    wires = _parse_harness_wires(harness_paths)
    out = []
    rest = []
    confirmed = set()   # 出力済み(実在確定)機器キー device-no
    for w in wires:
        if any(anchored(e) for e in w['ends']):
            out.append(_emit(w))
            for e in w['ends']:
                confirmed.add(norm(e['device'] + ('-' + e['no'] if e['no'] else '')))
        else:
            rest.append(w)
    # 確定機器の伝播(1パス): 出力済みで実在確定した機器に繋がる残りの台帳線も採用。
    # 例: GL-401 は 52-401 経由の線で出力済み→実在確定。同じ GL-401 の扉配線も採る。
    for w in rest:
        if any(norm(e['device'] + ('-' + e['no'] if e['no'] else '')) in confirmed for e in w['ends']):
            out.append(_emit(w))
    return out


def rule_errors(seq_paths, skel_paths=None, harness_paths=None):
    """ハーネス生成ルールに反する箇所をエラーとして返す（黙って落とさない）。
    現行ルール:
      ・中欠用: 台帳に「中欠用」線が在るなら図面に中性線欠相保護(NCV)が在るはず。
        NCVが無いのに中欠用が在れば矛盾 → エラー。
    戻り: [{'rule','reason','wire'}]。空なら全てルール通り。"""
    skel_paths = skel_paths or []
    _, _, _earth, nzs = _sheet_roster(list(seq_paths) + list(skel_paths))
    errs = []
    for w in _parse_harness_wires(harness_paths):
        devs = [e.get('device', '') for e in w['ends']]
        if any('中欠' in d for d in devs) and not nzs:
            e0 = w['ends'][0]
            e1 = w['ends'][1] if len(w['ends']) > 1 else {}
            errs.append({'rule': '中欠用', 'reason': '台帳に中欠用が在るが図面に中性線欠相保護(NCV)が無い',
                         'wire': f"{e0.get('device')}-{e0.get('no')}:{e0.get('terminal')} <-> "
                                 f"{e1.get('device','')}-{e1.get('no','')}:{e1.get('terminal','')}"})
    return errs


def place_map_from_harness(harness_paths):
    """台帳の場所列(扉/P等)から 機器キー(norm)→場所 を作る（検証時の場所付与に利用）。"""
    pm = {}
    for p in harness_paths or []:
        try:
            rows = list(csv.reader(open(p, encoding='cp932', errors='replace'), delimiter='\t'))
        except Exception:
            continue
        for r in rows:
            c = [x.strip() for x in (r + [''] * 11)[:11]]
            dev, num, place = c[5], c[6], c[4]
            if dev and dev != '*' and place:
                pm[norm(dev + ('-' + num if num else ''))] = place
    return pm
