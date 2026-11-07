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


def write_dxf(layout_paths, out_path, basis_of=None, dct_paths=None, layer='LOCATOR',
              height=28.0, color=1, dx=0.0, dy=40.0):
    """元の内部配置図にロケーター文字を TEXT で重ね書きして保存する(機器相番号DCT相当)。
    先頭の layout を基図として読み、各機器位置の近くへ『手本(DCT印字)通りの文字』を書く。
    dct_paths があれば手本の印字文字をそのまま使う(再生成しない)。
    color: ACI色(1=赤)。dx,dy: 機器位置からの文字オフセット。
    戻り: 書いた件数。"""
    base = layout_paths[0]
    doc = ezdxf.readfile(base)
    msp = doc.modelspace()
    if layer not in doc.layers:
        doc.layers.add(layer, color=color)
    n = 0
    for loc, sym, x, y, _t in locator_rows(layout_paths, basis_of, dct_paths):
        if x is None or y is None:          # 対応表にあるが配置図に未配置 → 描画はスキップ
            continue
        msp.add_text(
            loc,
            dxfattribs={'layer': layer, 'height': height, 'color': color,
                        'insert': (x + dx, y + dy)},
        )
        n += 1
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.saveas(out_path)
    return n
