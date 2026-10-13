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


if __name__ == '__main__':
    test_letters(); test_cluster_panels(); test_assign_reading_order()
    print('locator_assign tests: OK')
