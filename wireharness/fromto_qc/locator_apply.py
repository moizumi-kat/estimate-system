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


def locator_rows(layout_paths, basis_of=None, dct_paths=None):
    """ロケーター採番の一覧を返す: [(locator, sym, x, y, type), ...]。

    ◎手本準拠が最優先: 設計DCT(機器相番号)にロケーター文字が印字されていれば、それを
    位置一致で物理機器へ対応づけて『そのまま』出力する(=手本通り)。再生成(assign)は使わない。
    DCTが無い/印字が無い新規図面のときだけ、基割当(basis_of)→幾何assign で代替する。"""
    # ① 手本(DCT印字)をそのまま採用。
    #   ただしDCTと配置図の座標系がずれて位置照合が大きく欠落する図面(例:2基1式)では、
    #   印字のみ採用だと取りこぼすため、物理ロケーター対象の過半を照合できた時だけ採用し、
    #   そうでなければ②へフォールバックする(◎誤答ゼロ/取りこぼし防止)。
    if dct_paths:
        printed = LA.read_printed(dct_paths, layout_paths)   # {sym: 文字}
        if printed:
            parts = {p['sym']: p for p in LA._all_device_parts(layout_paths)}
            rows = []
            for sym, lt in printed.items():
                p = parts.get(sym)
                if p:
                    rows.append((lt, sym, p['x'], p['y'], p.get('type', '')))
            n_loc_parts = len(LA.actual_parts(layout_paths))  # 物理ロケーター対象数
            if rows and (n_loc_parts == 0 or len(rows) >= 0.5 * n_loc_parts):
                rows.sort(key=lambda r: (-r[3], r[2]))        # 読み順(上→下, 左→右)
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
        msp.add_text(
            loc,
            dxfattribs={'layer': layer, 'height': height, 'color': color,
                        'insert': (x + dx, y + dy)},
        )
        n += 1
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.saveas(out_path)
    return n
