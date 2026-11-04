# -*- coding: utf-8 -*-
"""locator_assign の付番ロジック固定テスト(DXF非依存)。"""
from . import locator_assign as la


def test_letters():
    assert la._letters(3) == ['A', 'B', 'C']
    assert la._letters(28)[26:28] == ['AA', 'AB']


def test_cluster_panels():
    parts = [{'sym': 'a', 'x': 100, 'y': 0}, {'sym': 'b', 'x': 120, 'y': 0},
             {'sym': 'c', 'x': 900, 'y': 0}]           # 900は>300離れ別盤
    panels = la._cluster_panels(parts)
    assert len(panels) == 2
    assert [p['sym'] for p in panels[0]] == ['a', 'b']


def test_assign_reading_order(monkeypatch=None):
    # 読み順(上→下,左→右)で付番。単盤なら基前置なし。
    parts = [
        {'sym': 'FX1', 'base': 'FX', 'num': '1', 'x': 1876, 'y': 2140, 'type': 'RU4S'},
        {'sym': 'ALX1', 'base': 'ALX', 'num': '1', 'x': 1905, 'y': 2140, 'type': 'RU4S'},
        {'sym': 'CX101', 'base': 'CX', 'num': '101', 'x': 1934, 'y': 2140, 'type': 'RU2S'},
        {'sym': '86X102', 'base': '86X', 'num': '102', 'x': 1978, 'y': 1790, 'type': 'RU2S'},
    ]
    orig = la.actual_parts
    la.actual_parts = lambda paths: parts
    try:
        a = la.assign(['x'])
        # 同じy(2140)は左→右: FX1,ALX1,CX101 = A,B,C ; 下段(1790)の86X102 = D
        assert a['by_actual']['FX1'] == 'A'
        assert a['by_actual']['ALX1'] == 'B'
        assert a['by_actual']['CX101'] == 'C'
        assert a['by_actual']['86X102'] == 'D'
    finally:
        la.actual_parts = orig


def test_reading_order_row_tolerance():
    # 同一行でも作図上 y が微小にズレる(0.25等)。行にまとめて左→右に並べる。
    # (117盤2の H/I/J 実例: y=2370.75/2371.00 混在で厳密ソートだと順序が崩れる)
    panel = [
        {'sym': 'J', 'x': 1272, 'y': 2371.00},
        {'sym': 'H', 'x': 1193, 'y': 2370.75},
        {'sym': 'I', 'x': 1222, 'y': 2370.75},
    ]
    order = [p['sym'] for p in la._reading_order(panel, yrow_tol=40)]
    assert order == ['H', 'I', 'J'], order       # x昇順(左→右)
    # 行が離れていれば(>yrow_tol)別行として上→下
    panel2 = [
        {'sym': 'top', 'x': 100, 'y': 2000.0},
        {'sym': 'bot', 'x': 90, 'y': 1700.0},
    ]
    assert [p['sym'] for p in la._reading_order(panel2, yrow_tol=40)] == ['top', 'bot']


def test_assign_by_basis_grouping():
    # 設計の基割当(basis_of)で基ごとにグループ化し、各基内を読み順で A,B,C…。
    # 幾何クラスタを使わない: 離れていても同一基ならまとめる / 近くても別基なら分ける。
    parts = [
        {'sym': 'P1', 'x': 100, 'y': 2000.0, 'type': ''},   # 基2・上段
        {'sym': 'P2', 'x': 900, 'y': 2000.0, 'type': ''},   # 基2・上段(離れていても同一基)
        {'sym': 'P3', 'x': 120, 'y': 1700.0, 'type': ''},   # 基2・下段
        {'sym': 'Q1', 'x': 130, 'y': 2000.0, 'type': ''},   # 基3(近くても別基)
    ]
    orig = la._all_device_parts
    la._all_device_parts = lambda paths: parts
    try:
        r = la.assign_by_basis(['x'], {'P1': '2', 'P2': '2', 'P3': '2', 'Q1': '3'})
        a = r['by_actual']
        assert a['P1'] == '2-A' and a['P2'] == '2-B' and a['P3'] == '2-C', a   # 上段左→右, 次段
        assert a['Q1'] == '3-A', a                                             # 別基は A から
    finally:
        la._all_device_parts = orig


def test_assign_by_basis_ignores_unlisted():
    # basis_of に無い機器は採番しない(設計がロケーター対象を決める)。
    parts = [{'sym': 'A1', 'x': 100, 'y': 2000.0, 'type': ''},
             {'sym': 'B1', 'x': 130, 'y': 2000.0, 'type': ''}]
    orig = la._all_device_parts
    la._all_device_parts = lambda paths: parts
    try:
        r = la.assign_by_basis(['x'], {'A1': '1'})
        assert r['by_actual'] == {'A1': '1-A'}, r['by_actual']
    finally:
        la._all_device_parts = orig


if __name__ == '__main__':
    test_letters(); test_cluster_panels(); test_assign_reading_order()
    test_reading_order_row_tolerance()
    test_assign_by_basis_grouping(); test_assign_by_basis_ignores_unlisted()
    print('locator_assign tests: OK')
