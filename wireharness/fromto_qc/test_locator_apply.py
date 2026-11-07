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


def test_locator_rows_prefers_dct_printed():
    """◎手本準拠: DCT(機器相番号)に印字されたロケーター文字があれば、再生成せず
    その文字をそのまま出力する。位置照合が過半を満たす場合に採用。"""
    parts = [{'sym': 'FX1', 'x': 100, 'y': 2000.0, 'type': ''},
             {'sym': 'ALX1', 'x': 900, 'y': 2000.0, 'type': ''}]
    orig = dict(ap=LA.actual_parts, ad=LA._all_device_parts, rp=LA.read_printed)
    LA.actual_parts = lambda paths: parts
    LA._all_device_parts = lambda paths: parts
    # 手本の印字: FX1→D, ALX1→C(あえて基割当(A,B)と異なる値)。これがそのまま出る事を検証。
    LA.read_printed = lambda dct, layout: {'FX1': 'D', 'ALX1': 'C'}
    try:
        rows = LAP.locator_rows(['x'], basis_of={'FX1': '2', 'ALX1': '2'}, dct_paths=['dct'])
        got = {(r[1], r[0]) for r in rows}
        assert got == {('FX1', 'D'), ('ALX1', 'C')}, rows   # 手本の印字どおり
    finally:
        LA.actual_parts, LA._all_device_parts, LA.read_printed = orig['ap'], orig['ad'], orig['rp']


def test_locator_rows_falls_back_when_dct_coverage_low():
    """座標系ずれ等でDCT印字の照合が過半に満たない場合は、基割当にフォールバック(取りこぼし防止)。"""
    parts = [{'sym': 'FX1', 'x': 100, 'y': 2000.0, 'type': ''},
             {'sym': 'ALX1', 'x': 900, 'y': 2000.0, 'type': ''},
             {'sym': 'BX1', 'x': 1700, 'y': 2000.0, 'type': ''},
             {'sym': 'CX1', 'x': 2500, 'y': 2000.0, 'type': ''}]
    orig = dict(ap=LA.actual_parts, ad=LA._all_device_parts, rp=LA.read_printed)
    LA.actual_parts = lambda paths: parts
    LA._all_device_parts = lambda paths: parts
    LA.read_printed = lambda dct, layout: {'FX1': 'D'}     # 4台中1台のみ照合(<50%)
    try:
        rows = LAP.locator_rows(['x'], basis_of={p['sym']: '2' for p in parts}, dct_paths=['dct'])
        # フォールバック: 全台に基割当(2-A..2-D)。印字のみの1件返しにならない。
        assert len(rows) == 4, rows
        assert ('2-A', 'FX1') in [(r[0], r[1]) for r in rows], rows
    finally:
        LA.actual_parts, LA._all_device_parts, LA.read_printed = orig['ap'], orig['ad'], orig['rp']


if __name__ == '__main__':
    test_locator_rows_structure(); test_locator_rows_with_basis()
    test_locator_rows_prefers_dct_printed(); test_locator_rows_falls_back_when_dct_coverage_low()
    print('locator_apply tests: OK')
