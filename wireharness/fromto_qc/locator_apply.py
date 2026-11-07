# -*- coding: utf-8 -*-
"""ロケーター仮名称を設計図面(内部配置図)へ付加する。

工程の起点: 設計から供給される内部配置図(実機器名＋位置)に対し、我々が決定論的に
ロケーター(A,B,C… / 基-文字)を採番し(locator_assign)、その情報を図面へ書き戻す。
  - write_dxf(): 元図面に TEXT でロケーター文字を重ね書きした DXF を保存(機器相番号DCT相当)。
  - locator_rows(): (locator, 実機器sym, x, y, type) の一覧(確認UI・後続工程の入力)。

基(盤)の扱い:
  basis_of を渡せば「基は設計から・文字は我々が生成」(assign_by_basis)。
  渡さなければ位置ベースの基推定(assign)。本番では設計の盤番号を basis_of で渡すのが正確。
"""
import os
import ezdxf

from . import locator_assign as LA
from . import locator_map as LM
from .geometry import norm


def _basis_key(loc):
    """ロケーター '2-E' を整列キー (基, 文字) に。基は数値優先。"""
    b, lt = LM._split_locator(loc)
    bk = (0, int(b)) if str(b).isdigit() else (1, 0, str(b))
    return (bk, lt)


def locator_rows(layout_paths, basis_of=None, dct_paths=None):
    """ロケーター採番の一覧を返す: [(locator, sym, x, y, type), ...]。
    x,y は配置図で対応機器が見つかった場合の位置(見つからなければ None)。

    ◎手本準拠が最優先: 設計DCT(機器相番号)の『盤下ロケーター対応表』(locator_map)は
    基-文字↔実機器名を機器名で定義する権威データ。これをそのまま採用する(=手本通り)。
    対応表は、相番号シンボルの単文字印字より完全・曖昧なし・座標ずれに強く、
    From-To手本と同一表記。DCT対応表が無い新規図面のときだけ、基割当→幾何assignで代替。"""
    # ① 手本(盤下ロケーター対応表)をそのまま採用
    if dct_paths:
        seen = set()
        pairs = []
        for p in dct_paths:
            for loc, act in LM.extract_pairs(p):
                if (loc, act) not in seen:
                    seen.add((loc, act))
                    pairs.append((loc, act))
        if pairs:
            parts = {p['sym']: p for p in LA._all_device_parts(layout_paths)}
            partsN = {norm(k): v for k, v in parts.items()}
            rows = []
            for loc, act in pairs:
                p = parts.get(act) or partsN.get(norm(act))
                if p:
                    rows.append((loc, act, p['x'], p['y'], p.get('type', '')))
                else:
                    rows.append((loc, act, None, None, ''))   # 対応は保持(配置図に未配置)
            rows.sort(key=lambda r: _basis_key(r[0]))
            return rows
    # ② 代替: 基割当 → 幾何
    if basis_of:
        res = LA.assign_by_basis(layout_paths, basis_of)
    else:
        res = LA.assign(layout_paths, basis_prefix='auto')
    rows = []
    for panel in res['panels']:
        for loc, pr in panel:
            rows.append((loc, pr['sym'], pr['x'], pr['y'], pr.get('type', '')))
    return rows


def write_table_csv(layout_paths, out_csv, basis_of=None, dct_paths=None):
    """盤下ロケーター対応表(ロケーター↔実機器名)をCSVに出力する。
    列: ロケーター, 実機器名, X, Y, 型式。位置は配置図で見つかった場合のみ。戻り: 行数。"""
    import csv
    import os
    rows = locator_rows(layout_paths, basis_of, dct_paths)
    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    with open(out_csv, 'w', encoding='cp932', errors='replace', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['ロケーター', '実機器名', 'X', 'Y', '型式'])
        for loc, sym, x, y, t in rows:
            wr.writerow([loc, sym, '' if x is None else x, '' if y is None else y, t])
    return len(rows)


def _dct_table_cells(dct_paths):
    """手本DCT(機器相番号)の盤下ロケーター対応表のセルを、元の位置そのままで取得する。
    戻り: [(text, x, y, height), ...]（loc / ':' / actual を各セルの原位置で）。
    手本の列構成・位置を忠実に再現するため、ロケーター付図面へ同座標で描き写す用途。"""
    import re as _re
    cells = []
    for p in (dct_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        A = []
        for ins in doc.modelspace().query('INSERT'):
            for at in (ins.attribs or []):
                s = (at.dxf.text or '').strip()
                if not s:
                    continue
                try:
                    x, y, h = at.dxf.insert.x, at.dxf.insert.y, at.dxf.height
                except Exception:
                    continue
                if at.dxf.tag in ('DEVICE', 'DEVICE1', 'DEVICE2'):
                    A.append((x, y, at.dxf.tag, s, h))
        anchors = [(x, y, h) for x, y, t, s, h in A if t == 'DEVICE2' and s == ':']
        anchor_xs = sorted(set(round(x) for x, y, h in anchors))
        for (ax, ay, ah) in anchors:
            right = min([bx for bx in anchor_xs if bx > ax + 1], default=ax + 1e9)
            loc = act = None
            locp = actp = None
            loch = acth = ah
            for x, y, t, s, h in A:
                if abs(y - ay) > 4:
                    continue
                if t == 'DEVICE1' and x < ax and (locp is None or x > locp[0]):
                    loc, locp, loch = s, (x, y), h
                if t == 'DEVICE' and ax < x < right and (actp is None or x < actp[0]):
                    act, actp, acth = s, (x, y), h
            if loc and act and _re.search(r'[A-Za-z]', loc) and _re.search(r'\d', act):
                cells.append((loc, locp[0], locp[1], loch))
                cells.append((':', ax, ay, ah))
                cells.append((act, actp[0], actp[1], acth))
    return cells


def _table_style_from_dct(dct_paths):
    """手本DCTの対応表から、縦位置の基準(y_top)・文字高(th)を得る。無ければ(None,None)。
    手本と同じ『盤の下の空き帯・文字サイズ』に合わせるため。行間は文字高から決める
    (手本のy値は列が入り組み最小差分が過小になるため、行間推定には使わない)。"""
    cells = _dct_table_cells(dct_paths) if dct_paths else []
    ys = [round(y) for tx, x, y, h in cells if tx == ':']
    if ys:
        th = next((h for tx, x, y, h in cells if tx == ':' and h), 25.0)
        return max(ys), th
    return None, None


def _draw_table_per_basis(msp, rows, layer, color, dct_paths=None):
    """各基の対応表(ロケーター：実機器名)を、その盤の中心の真下に縦並びで描画する。
    『盤の下に基ごと』の記載。縦位置・行間・文字高は手本DCTに合わせる(無ければ既定)。
    rows は (loc, sym, x, y, type)。配置済(x,y)の機器から各基の盤中心xを決める。"""
    import collections
    byb = collections.OrderedDict()
    for loc, sym, x, y, t in rows:
        b, _lt = LM._split_locator(loc)
        byb.setdefault(b, []).append((loc, sym, x, y))
    if not byb:
        return
    _dcty, th = _table_style_from_dct(dct_paths)
    if th is None:
        th = 25.0
    # 盤に近い位置(盤の直下)に表示: 図面上の全機器の最下yの少し下に置く。
    # (手本DCTの表位置は盤から離れて下方にあるため、盤寄りへ引き上げる)
    dev_ys = []
    for ins in msp.query('INSERT'):
        if not ins.attribs:
            continue
        a = {k.dxf.tag: (k.dxf.text or '').strip() for k in ins.attribs}
        if a.get('DEVICE') and a.get('DEVICE2') != ':':
            try:
                dev_ys.append(ins.dxf.insert.y)
            except Exception:
                pass
    if dev_ys:
        y_top = min(dev_ys) - 500.0         # 盤最下端機器の下・寸法線帯の直下(盤に近い位置)
    else:
        ys = [y for loc, sym, x, y, t in rows if y is not None]
        y_top = (min(ys) - 500.0) if ys else (_dcty or 0.0)
    import math
    row_h = th * 1.5                         # 行間は文字高基準(潰れ防止)
    y_top -= row_h                           # 寸法線と表の間に1行分の余白を空ける
    char_w = th * 0.62                       # 概算字幅(列センタリング用)
    max_rows = 7                             # 1列の最大行数(これを超えたら横に折り返し、下枠に掛けない)
    for b, items in byb.items():
        items.sort(key=lambda r: _basis_key(r[0]))
        xs = [x for loc, sym, x, y in items if x is not None]
        if not xs:
            continue
        cx = sum(xs) / len(xs)               # 盤(基)の中心x
        n = len(items)
        ncol = max(1, math.ceil(n / max_rows))
        rows_per = math.ceil(n / ncol)       # 列をできるだけ均等に
        maxlen = max(len(f'{loc} : {sym}') for loc, sym, x, y in items)
        col_w = maxlen * char_w + 3 * char_w  # 列幅(文字列＋列間)
        x_start = cx - (ncol * col_w - 3 * char_w) / 2.0   # 盤中心に全体を揃える
        for i, (loc, sym, x, y) in enumerate(items):
            col = i // rows_per
            row = i % rows_per
            tx = x_start + col * col_w
            ty = y_top - row * row_h
            msp.add_text(f'{loc} : {sym}',
                         dxfattribs={'layer': layer, 'height': th, 'color': color,
                                     'insert': (tx, ty)})


def write_dxf(layout_paths, out_path, basis_of=None, dct_paths=None, layer='LOCATOR',
              height=28.0, color=1, dx=0.0, dy=40.0, draw_table=True):
    """元の内部配置図にロケーター文字を TEXT で重ね書きして保存する(機器相番号DCT相当)。
    先頭の layout を基図として読み、各機器位置の近くへ『手本(盤下対応表)通りの文字』を書き、
    さらに盤下へロケーター対応表(ロケーター↔実機器名)を記載する。
    dct_paths があれば盤下対応表の表記をそのまま使う(再生成しない)。
    color: ACI色(1=赤)。dx,dy: 機器位置からの文字オフセット。draw_table: 対応表を描くか。
    戻り: 機器に書いた件数。"""
    base = layout_paths[0]
    doc = ezdxf.readfile(base)
    msp = doc.modelspace()
    if layer not in doc.layers:
        doc.layers.add(layer, color=color)
    rows = locator_rows(layout_paths, basis_of, dct_paths)
    n = 0
    for loc, sym, x, y, _t in rows:
        if x is None or y is None:          # 対応表にあるが配置図に未配置 → 機器描画はスキップ
            continue
        msp.add_text(
            loc,
            dxfattribs={'layer': layer, 'height': height, 'color': color,
                        'insert': (x + dx, y + dy)},
        )
        n += 1
    if draw_table:
        # 各基の対応表を、その盤の中心の真下に基ごとに描画(縦位置・行間・文字高は手本に合わせる)
        _draw_table_per_basis(msp, rows, layer, color, dct_paths=dct_paths)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.saveas(out_path)
    return n
