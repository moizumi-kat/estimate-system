# -*- coding: utf-8 -*-
"""locator_apply の固定テスト(DXF非依存): 採番一覧の構造・基指定連携。"""
from . import locator_apply as LAP
from . import locator_assign as LA


def test_locator_rows_structure():
    parts = [{'sym': 'FX1', 'x': 1876, 'y': 2140, 'type': 'RU4S'},
             {'sym': 'ALX1', 'x': 1905, 'y': 2140, 'type': 'RU4S'}]
    orig = LA.actual_parts
    LA.actual_parts = lambda paths: parts
    try:
        rows = LAP.locator_rows(['x'])           # 幾何assign
        assert [(r[0], r[1]) for r in rows] == [('A', 'FX1'), ('B', 'ALX1')], rows
        assert all(len(r) == 5 for r in rows)    # (loc,sym,x,y,type)
    finally:
        LA.actual_parts = orig


def test_locator_rows_with_basis():
    parts = [{'sym': 'FX1', 'x': 100, 'y': 2000.0, 'type': ''},
             {'sym': 'ALX1', 'x': 900, 'y': 2000.0, 'type': ''}]
    orig = LA._all_device_parts
    LA._all_device_parts = lambda paths: parts
    try:
        rows = LAP.locator_rows(['x'], basis_of={'FX1': '2', 'ALX1': '2'})
        assert [(r[0], r[1]) for r in rows] == [('2-A', 'FX1'), ('2-B', 'ALX1')], rows
    finally:
        LA._all_device_parts = orig


if __name__ == '__main__':
    test_locator_rows_structure(); test_locator_rows_with_basis()
    print('locator_apply tests: OK')
