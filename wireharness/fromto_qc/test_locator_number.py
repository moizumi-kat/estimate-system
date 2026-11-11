# -*- coding: utf-8 -*-
"""locator_number の相番号付与ロジックの固定テスト(DXF非依存・合成データ)。

実機の手本DCT(社内資産)は同梱しないため、ここでは assign_letters の採番規則
(盤まとめ=同一レール/レール重なり/同一行近接、採番=上→下・左→右)を合成座標で検証する。
手本12製番=116/116 の実データ照合は社内で別途実施済み。
"""
from . import locator_number as LN


def _assign(devs, rails):
    """devs=[(x,y)] を (x,y,None) に整えて相番号 {index:letter} を返す。"""
    return LN.assign_letters([(x, y, None) for (x, y) in devs], rails)


def test_single_rail_left_to_right():
    # 1本のレール上に左→右。採番 A,B,C。
    rail = (0.0, 100.0, 300.0)            # 幅=番号×8 相当の span(x0,y,x1)
    devs = [(50.0, 120.0), (150.0, 120.0), (250.0, 120.0)]
    L = _assign(devs, [rail])
    assert [L[i] for i in range(3)] == ['A', 'B', 'C'], L


def test_rows_top_to_bottom():
    # 同一盤内で上段(y=200)→下段(y=120)。各行は左→右。
    rail = (0.0, 100.0, 300.0)
    devs = [(50.0, 120.0),   # 下段左
            (150.0, 120.0),  # 下段右
            (50.0, 200.0),   # 上段左
            (150.0, 200.0)]  # 上段右
    L = _assign(devs, [rail])
    # 上段(idx2,3)=A,B / 下段(idx0,1)=C,D
    assert L[2] == 'A' and L[3] == 'B', L
    assert L[0] == 'C' and L[1] == 'D', L


def test_overlapping_rails_same_panel():
    # x範囲が重なる2本のレール(縦積み)=同一盤。採番は盤全体で連番。
    r1 = (0.0, 200.0, 200.0)
    r2 = (0.0, 100.0, 200.0)             # 真下に重なる
    devs = [(50.0, 220.0), (50.0, 120.0)]
    L = _assign(devs, [r1, r2])
    assert L[0] == 'A' and L[1] == 'B', L     # 上(A)→下(B) で同一盤連番


def test_far_apart_separate_panels():
    # 横に大きく離れた(ROW_XGAP超)別レール=別盤。各盤が独立に A から。
    r1 = (0.0, 100.0, 100.0)
    r2 = (2000.0, 100.0, 2100.0)        # 遠い別盤
    devs = [(50.0, 120.0), (2050.0, 120.0)]
    L = _assign(devs, [r1, r2])
    assert L[0] == 'A' and L[1] == 'A', L     # それぞれ別盤の先頭


def test_same_row_near_merges_without_rail():
    # レール無しでも同一行で近接(<=450)なら同一盤。
    devs = [(0.0, 100.0), (400.0, 100.0)]
    L = _assign(devs, [])
    assert sorted(L.values()) == ['A', 'B'], L


def test_rail_span_from_name():
    # X_RAIL_<n>W の幅 ≒ n×8。
    assert LN._rail_span('X_RAIL_25W', 1) == 25 * 8.0
    assert LN._rail_span('X_RAIL_10W', 2) == 10 * 8.0 * 2
