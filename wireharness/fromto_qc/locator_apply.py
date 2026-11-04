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


def locator_rows(layout_paths, basis_of=None):
    """ロケーター採番の一覧を返す: [(locator, sym, x, y, type), ...]（基・読み順）。"""
    if basis_of:
        res = LA.assign_by_basis(layout_paths, basis_of)
    else:
        res = LA.assign(layout_paths, basis_prefix='auto')
    rows = []
    for panel in res['panels']:
        for loc, pr in panel:
            rows.append((loc, pr['sym'], pr['x'], pr['y'], pr.get('type', '')))
    return rows


def write_dxf(layout_paths, out_path, basis_of=None, layer='LOCATOR',
              height=28.0, color=1, dx=0.0, dy=40.0):
    """元の内部配置図にロケーター文字を TEXT で重ね書きして保存する(機器相番号DCT相当)。
    先頭の layout を基図として読み、各機器位置の近くへ『基-文字』を書く。
    color: ACI色(1=赤)。dx,dy: 機器中心からの文字オフセット。
    戻り: 書いた件数。"""
    base = layout_paths[0]
    doc = ezdxf.readfile(base)
    msp = doc.modelspace()
    if layer not in doc.layers:
        doc.layers.add(layer, color=color)
    n = 0
    for loc, sym, x, y, _t in locator_rows(layout_paths, basis_of):
        msp.add_text(
            loc,
            dxfattribs={'layer': layer, 'height': height, 'color': color,
                        'insert': (x + dx, y + dy)},
        )
        n += 1
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.saveas(out_path)
    return n
