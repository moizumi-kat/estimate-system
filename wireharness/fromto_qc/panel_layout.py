# -*- coding: utf-8 -*-
"""内部配置図＋扉面図から『盤別機器リスト(device→面)』を作る。

茂泉様確定の読み方:
  - 盤は内部配置図/扉面で数えられ、左から 1,2,…,N と順に番号を振る。
  - 1系統(回路番号)の機器が複数盤にまたがることがあり、どの盤かは『位置』でしか分からない
    (回路番号では盤は決まらない)。共通リレー(30X等)は特定盤に集約されることがある。
  - ハーネスの From-To は『一面(=盤＋その扉)』ごとに作られる。

本モジュール: D図面(配置図)から各機器を x位置で左から N 盤へ割り当て、
  {(DEVICE正規化, DEVICE1正規化): 盤番号文字列} を返す。DCT(相番号図)は別途『改名』用途。
"""
import re
import collections

import ezdxf
from ezdxf import recover

# 盤に概ね1つ出る目印(盤数・盤中心の推定に使う)。扉面に出る計器類。
_PANEL_MARKERS = ('VM', 'VS')
# device→面 の対象外(金物・付属)。
_SKIP_DEVICES = {'吊りﾎﾞﾙﾄ', 'ﾊﾝﾄﾞﾙ', 'TB取付金具', 'CABLE', '低圧ｸﾘｰﾄ', '端子ｶﾊﾞｰ',
                 '扉蝶番', 'LugTB'}
# シーケンス機器名 → 内部配置図の機器名/同一視(名寄せ)。AM=電流計A、51/86X=遮断器52に付随。
_DEVICE_ALIAS = {'AM': 'A'}
_INTEGRATED_TO_52 = {'51', '86X'}   # 同一回路の52の盤を継承(電子式で遮断器に統合/付随)
# サーマル等スターター付随機器: 回路の遮断器(52/MCCB)の盤を継承。内部配置図に描かれない
# ことがある(例 5-12043 の OL-301/302/303 は未描画)ため、回路番号で盤を補う。
_INTEGRATED_TO_BREAKER = {'OL'}
# 盤枠矩形が載る層(内部配置/扉面の正確な盤x境界)。製番により無い(k-means代替)。
_FRAME_LAYER = 'Dr_BOX'
# 系統(DEVICE1)→盤 の投票に使う主機器・扉機器(位置が盤を素直に表す型)。
_VOTE_DEVICES = {'52', 'MCCB', 'MCB', 'ELB', '3', 'SL', '43', 'WL', 'CT'}
# 内部機器(内部/扉の領域分離に使う。枠なし製番の盤x境界導出用)。
_INTERNAL_CORE = {'MCCB', '52', 'MCB', 'ELB', 'ELCB', '51', '86X', '30X', '30Y',
                  'CT', '52X', '28X', '42', '6', '2T', 'FX', '2'}


def _norm(s):
    return re.sub(r'\s', '', str(s or '')).upper()


def _read(path):
    try:
        return ezdxf.readfile(path)
    except Exception:
        try:
            return recover.readfile(path)[0]
        except Exception:
            return None


def _layout_paths(dct_paths):
    """dct_paths から配置図(D図面・-D\\d、DCTを除く)だけを返す。"""
    out = []
    for p in (dct_paths or []):
        b = str(p).upper()
        if 'DCT' in b:
            continue
        if re.search(r'-D\d', b) or '配置' in str(p) or '扉面' in str(p):
            out.append(p)
    return out


def _kmeans1d(xs, n, it=80):
    """1次元 k-means。昇順の中心 n 個を返す。"""
    xs = sorted(xs)
    uniq = sorted(set(xs))
    if len(uniq) <= n:
        return uniq
    cs = [xs[int(i * (len(xs) - 1) / (n - 1))] for i in range(n)]
    for _ in range(it):
        g = [[] for _ in cs]
        for x in xs:
            g[min(range(len(cs)), key=lambda i: abs(cs[i] - x))].append(x)
        nc = [sum(q) / len(q) if q else cs[i] for i, q in enumerate(g)]
        if nc == cs:
            break
        cs = nc
    return sorted(cs)


def _frame_bounds(doc):
    """配置図の盤枠矩形(_FRAME_LAYER の大矩形)の内側x境界(昇順 N-1 本)を返す。
    茂泉様確定: 内部配置図/扉面には盤が矩形で描かれ、その境界が正確な盤x境界。
    枠が2つ未満なら None(=この製番/シートは枠なし→k-means代替)。"""
    rs = []
    for e in doc.modelspace().query('LWPOLYLINE'):
        if e.dxf.layer != _FRAME_LAYER:
            continue
        try:
            pts = [(p[0], p[1]) for p in e.get_points()]
        except Exception:
            continue
        if len(pts) < 4:
            continue
        xs = [a for a, _ in pts]; ys = [b for _, b in pts]
        if max(xs) - min(xs) > 300 and max(ys) - min(ys) > 300:
            rs.append((min(xs), max(xs)))
    rs.sort()
    return [b for _, b in rs[:-1]] if len(rs) >= 2 else None


def _sheet_items(doc):
    """配置図1枚の機器を (DEVICE正規化, DEVICE1正規化, x) で返す(対象外機器は除く)。"""
    out = []
    for ins in doc.modelspace().query('INSERT'):
        if not ins.attribs:
            continue
        a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
        d = a.get('DEVICE', '')
        if not d or d in _SKIP_DEVICES:
            continue
        out.append((_norm(d), _norm(a.get('DEVICE1', '')), float(ins.dxf.insert[0])))
    return out


def panel_count(dct_paths):
    """盤数 N を推定。盤枠(_FRAME_LAYER の矩形)が最優先、次に目印(VM/VS)、
    最後に DCT の盤ラベル(N-M)の個数。どれも無ければ 0(=不明)。"""
    best = 0
    # 設計盤ブロック(InnerBox/DoorBox)が最も確実: 面数 = 最大ブロック番号
    for p in _layout_paths(dct_paths):
        doc = _read(p)
        if not doc:
            continue
        b = _design_boxes(doc)
        if b:
            nums = list(b.get('inner', {})) + list(b.get('door', {}))
            if nums:
                best = max(best, max(nums))
    if best >= 1:
        return best
    # 盤枠: 枠の数 = 盤数
    for p in _layout_paths(dct_paths):
        doc = _read(p)
        if not doc:
            continue
        fb = _frame_bounds(doc)
        if fb:
            best = max(best, len(fb) + 1)
    if best >= 2:
        return best
    for p in _layout_paths(dct_paths):
        doc = _read(p)
        if not doc:
            continue
        for mk in _PANEL_MARKERS:
            c = sum(1 for ins in doc.modelspace().query('INSERT')
                    if ins.attribs and any(a.dxf.tag == 'DEVICE' and (a.dxf.text or '').strip() == mk
                                           for a in ins.attribs))
            best = max(best, c)
    if best >= 2:
        return best
    # DCT の盤ラベル
    for p in (dct_paths or []):
        if 'DCT' not in str(p).upper():
            continue
        doc = _read(p)
        if not doc:
            continue
        nums = set()
        for e in doc.modelspace().query('TEXT MTEXT'):
            t = (e.plain_text() if e.dxftype() == 'MTEXT' else e.dxf.text).strip()
            m = re.match(r'^(\d+)-(\d+)$', t)
            if m:
                nums.add(int(m.group(2)))
        best = max(best, len(nums))
    return best


def _pane_fn(bounds):
    """内側境界 bounds(昇順)から x→盤番号(1..N) の関数を返す。"""
    def pane(x):
        for i, bd in enumerate(bounds):
            if x < bd:
                return i + 1
        return len(bounds) + 1
    return pane


# 設計の内部配置図に置かれる盤ブロック。盤の物理境界の最も確実な根拠(茂泉様指示:
# 「DCTでなく設計からの内部配置図を参照」)。例 MCILU_JKN_InnerBox2_..., DoorBox3_...。
# InnerBox{N}=N面の内部盤、DoorBox{N}=N面の扉。各ブロックの挿入x＋幅で面のx域が決まる。
_BOX_RE = re.compile(r'(Inner|Door)Box(\d+)', re.I)
# 板金の箱外形(盤の筐体)が載る画層。茂泉様指示「内部配置図の板金の箱帯で盤境界を把握」。
# 内部盤(InnerBox)は B_BOX 層、扉盤(DoorBox)は Dr_BOX 層に各盤の実際の板金外形がある
# ＝正確なx境界(盤ごとに幅が違ってよい)。両層を対象にする。
_METAL_BOX_LAYERS = {'B_BOX', 'DR_BOX'}


def _box_metal_xrange(ins):
    """盤ブロックINSERTの板金外形(B_BOX層ポリライン)の world x範囲を返す。無ければ None。"""
    xs = []
    try:
        for e in ins.virtual_entities():
            if (e.dxftype() in ('LWPOLYLINE', 'POLYLINE')
                    and str(e.dxf.layer).upper() in _METAL_BOX_LAYERS):
                xs += [p[0] for p in e.get_points()]
    except Exception:
        return None
    return (min(xs), max(xs)) if xs else None


def _design_boxes(doc):
    """設計内部配置図の盤ブロック(InnerBox{N}/DoorBox{N})から面の x域を返す。
    戻り: {'inner': {N:(x0,x1)}, 'door': {N:(x0,x1)}}。ブロックが無ければ None。
    各盤の境界は**板金の箱外形(B_BOX層)**の実x範囲で取る(盤ごとに幅が違っても正確)。
    B_BOX が読めない場合のみ 挿入x＋(隣接間隔=幅) で代替。"""
    inner_x, door_x, inner_r, door_r = {}, {}, {}, {}
    for ins in doc.modelspace().query('INSERT'):
        m = _BOX_RE.search(str(ins.dxf.name))
        if not m:
            continue
        N = int(m.group(2))
        xr = _box_metal_xrange(ins)
        x = float(ins.dxf.insert[0])
        if m.group(1).lower() == 'inner':
            inner_x[N] = x
            if xr:
                inner_r[N] = xr
        else:
            door_x[N] = x
            if xr:
                door_r[N] = xr
    if not inner_x and not door_x:
        return None

    def _fallback(xmap, rmap):
        # B_BOX が全く読めない盤だけ 挿入x＋等幅 で補う(通常は不要)
        if not xmap:
            return {}
        out = dict(rmap)
        missing = [N for N in xmap if N not in out]
        if missing:
            xs = sorted(xmap.values())
            difs = [b - a for a, b in zip(xs, xs[1:]) if b - a > 1]
            w = min(difs) if difs else 600.0
            for N in missing:
                out[N] = (xmap[N], xmap[N] + w)
        return out
    return {'inner': _fallback(inner_x, inner_r), 'door': _fallback(door_x, door_r)}


def _face_from_boxes(docs):
    """設計盤ブロック(InnerBox/DoorBox)で device→面 を作る(最優先・最確実)。
    各機器を x位置で内部盤/扉盤の域に入れ、面番号=ブロック番号。系統(DEVICE1)は
    主機器/扉機器の多数決で面を確定し系統機器へ継承(境界跨ぎ吸収)。盤ブロックが
    1つも無ければ None。戻り: {(DEVICE正規化, DEVICE1正規化): 面番号文字列}。"""
    allboxes = [(_design_boxes(d), d) for _, d in docs]
    allboxes = [(b, d) for b, d in allboxes if b]
    if not allboxes:
        return None

    def _panel_of(boxes, x):
        for N, (x0, x1) in boxes['inner'].items():
            if x0 <= x <= x1:
                return N
        for N, (x0, x1) in boxes['door'].items():
            if x0 <= x <= x1:
                return N
        # 箱に入らない機器(例 DoorBox 無し製番の扉機器)は最近傍の箱へ寄せる(機器を落とさない)
        cand = []
        for grp in ('inner', 'door'):
            for N, (x0, x1) in boxes[grp].items():
                cand.append((min(abs(x - x0), abs(x - x1)), N))
        return min(cand)[1] if cand else None

    rows = []                       # (DEVICE, 系統, 面)
    for boxes, doc in allboxes:
        for d, k, x in _sheet_items(doc):
            pn = _panel_of(boxes, x)
            if pn is not None:
                rows.append((d, k, pn))
    if not rows:
        return None
    votes = collections.defaultdict(list)
    for d, k, pn in rows:
        if d in _VOTE_DEVICES and k:
            votes[k].append(pn)
    keito = {k: collections.Counter(v).most_common(1)[0][0] for k, v in votes.items()}
    bytype = collections.defaultdict(list)
    for d, k, pn in rows:
        bytype[d].append((k, pn))
    common = set()
    for d, lst in bytype.items():
        if (len(lst) >= 4 and len({p for _, p in lst}) == 1
                and len({keito[k] for k, _ in lst if k in keito}) >= 3):
            common.add(d)
    df = {}
    for d, k, pn in rows:
        df[(d, k)] = str(keito[k]) if (k in keito and d not in common) else str(pn)
    return df


def build_device_place(dct_paths):
    """各機器が『どの面の内部盤か扉か』を設計の盤箱(InnerBox/DoorBox の板金外形)で把握する。
    茂泉様指示「それぞれの箱と扉に設置される機器の把握」。device→面 は番号のみだが、本関数は
    内部(箱)/扉の区別と面番号を返す(扉フラグ・扉側配線の根拠に使える)。
    戻り: {(DEVICE正規化, DEVICE1正規化): ('内部'|'扉', 面番号文字列)}。盤箱が無ければ空。"""
    layouts = _layout_paths(dct_paths)
    docs = [(p, _read(p)) for p in layouts]
    docs = [(p, d) for p, d in docs if d]
    allboxes = [(_design_boxes(d), d) for _, d in docs]
    allboxes = [(b, d) for b, d in allboxes if b]
    if not allboxes:
        return {}
    out = {}
    for boxes, doc in allboxes:
        for d, k, x in _sheet_items(doc):
            place = None
            for N, (x0, x1) in boxes['inner'].items():
                if x0 <= x <= x1:
                    place = ('内部', str(N)); break
            if place is None:
                for N, (x0, x1) in boxes['door'].items():
                    if x0 <= x <= x1:
                        place = ('扉', str(N)); break
            if place is not None:
                out[(d, k)] = place
    return out


def build_device_face(dct_paths, n=None):
    """配置図(内部＋扉面)から device→面 を作る。茂泉様の読み方に沿う:
      - 盤枠(_FRAME_LAYER)があれば正確な盤x境界。無いシートは k-means(N)中点。
      - 盤枠のある製番では『系統(DEVICE1)→盤』を主機器/扉機器の多数決で確定し、
        系統機器はその盤を継承(境界跨ぎの誤割当を吸収)。扉(D001)と内部(D002)は
        同一座標で系統ごとの盤が一致する実証(5-12043=25/25)に基づく。
      - 共通リレー(全系統が1盤に集中する型=例30X)と無系統機器(端子台TB等)は自位置。
      - 盤枠が全く無い製番は『内部/扉』を内部機器のx域で領域分離し各域を k-means(N)
        (系統伝播はせず自位置)。グリッドが不確実なため伝播で誤差を増幅させない。
    別名(AM→A)と 51/86X→同回路52 の盤継承を補う。
    戻り: {(DEVICE正規化, DEVICE1正規化): 盤番号文字列}。N 不明や配置図無しなら空。"""
    layouts = _layout_paths(dct_paths)
    if not layouts:
        return {}
    docs = [(p, _read(p)) for p in layouts]
    docs = [(p, d) for p, d in docs if d]
    # 最優先: 設計内部配置図の盤ブロック(InnerBox/DoorBox)で面を割当て(最確実)。
    # 茂泉様指示「DCTでなく設計からの内部配置図で盤境界を把握」。盤枠/k-means より優先。
    box_df = _face_from_boxes(docs)
    if box_df:
        return _apply_inheritance(box_df)
    if n is None:
        n = panel_count(dct_paths)
    if n < 2:
        return {}
    has_frames = any(_frame_bounds(d) for _, d in docs)
    rows = []                       # (DEVICE, 系統, sheet_idx, 盤)
    for si, (p, doc) in enumerate(docs):
        items = _sheet_items(doc)
        if not items:
            continue
        fb = _frame_bounds(doc)
        if fb and len(fb) >= n - 1:                       # このシートに枠あり=正確
            pane = _pane_fn(fb)
            rows += [(d, k, si, pane(x)) for d, k, x in items]
        elif has_frames:                                  # 他シートに枠=k-means中点で代替
            if len(items) < n:
                continue
            cs = _kmeans1d([x for _, _, x in items], n)
            pane = _pane_fn([(cs[i] + cs[i + 1]) / 2 for i in range(len(cs) - 1)])
            rows += [(d, k, si, pane(x)) for d, k, x in items]
        else:                                             # 枠なし製番: 内部/扉を領域分離
            intx = [x for d, _, x in items if d in _INTERNAL_CORE]
            xmax = max(intx) + (max(intx) - min(intx)) * 0.08 if intx else None
            regions = [[i for i in items if (xmax is None or i[2] <= xmax)]]
            if xmax is not None:
                door = [i for i in items if i[2] > xmax]
                if door:
                    regions.append(door)
            for reg in regions:
                if not reg:
                    continue
                cs = _kmeans1d([x for _, _, x in reg], n)
                rows += [(d, k, si, min(range(len(cs)), key=lambda i: abs(cs[i] - x)) + 1)
                         for d, k, x in reg]
    if not rows:
        return {}
    if has_frames:
        # 系統→盤(投票) と 共通リレー型を判定し、系統機器は系統盤を継承
        votes = collections.defaultdict(list)
        for d, k, _si, pn in rows:
            if d in _VOTE_DEVICES and k:
                votes[k].append(pn)
        keito = {k: collections.Counter(v).most_common(1)[0][0] for k, v in votes.items()}
        bytype = collections.defaultdict(list)
        for d, k, _si, pn in rows:
            bytype[d].append((k, pn))
        common = set()
        for d, lst in bytype.items():
            if (len(lst) >= 4 and len({p for _, p in lst}) == 1
                    and len({keito[k] for k, _ in lst if k in keito}) >= 3):
                common.add(d)                 # 全部1盤・系統は多盤に散る = 共通リレー
        df = {}
        for d, k, _si, pn in rows:
            df[(d, k)] = str(keito[k]) if (k in keito and d not in common) else str(pn)
    else:
        df = {(d, k): str(pn) for d, k, _si, pn in rows}
    return _apply_inheritance(df)


def _apply_inheritance(df):
    """別名(AM→A)・51/86X→同回路52・OL→同回路遮断器 の盤継承を補う(共通後処理)。"""
    for (d, k), fc in list(df.items()):
        for alias, real in _DEVICE_ALIAS.items():
            if d == _norm(real):
                df.setdefault((_norm(alias), k), fc)
    by52 = {k: fc for (d, k), fc in df.items() if d == '52'}
    for dev in _INTEGRATED_TO_52:
        for k, fc in by52.items():
            df.setdefault((dev, k), fc)
    by_brk = {k: fc for (d, k), fc in df.items() if d in ('52', 'MCCB')}
    for dev in _INTEGRATED_TO_BREAKER:
        for k, fc in by_brk.items():
            df.setdefault((dev, k), fc)
    return df
