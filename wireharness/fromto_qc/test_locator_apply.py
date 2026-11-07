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


def test_locator_rows_prefers_dct_table():
    """◎手本準拠: 盤下ロケーター対応表(locator_map)があれば、基-文字↔実機器名を
    そのまま出力する(再生成しない)。基割当とは別値でも表どおりに出る事を検証。"""
    import fromto_qc.locator_map as LM
    parts = [{'sym': 'FX1', 'x': 100, 'y': 2000.0, 'type': ''},
             {'sym': 'ALX1', 'x': 900, 'y': 2000.0, 'type': ''}]
    orig = dict(ad=LA._all_device_parts, ep=LM.extract_pairs)
    LA._all_device_parts = lambda paths: parts
    # 対応表: 3-B↔FX1, 1-A↔ALX1(あえて basis_of(2-*)と異なる)。表どおりに出る。
    LM.extract_pairs = lambda path: [('3-B', 'FX1'), ('1-A', 'ALX1')]
    try:
        rows = LAP.locator_rows(['x'], basis_of={'FX1': '2', 'ALX1': '2'}, dct_paths=['dct'])
        got = {(r[0], r[1]) for r in rows}
        assert got == {('3-B', 'FX1'), ('1-A', 'ALX1')}, rows   # 表どおり(基-文字付き)
        # 整列: 基昇順 → 1-A が先頭
        assert rows[0][0] == '1-A', rows
    finally:
        LA._all_device_parts, LM.extract_pairs = orig['ad'], orig['ep']


def test_locator_rows_table_keeps_unplaced():
    """対応表にあるが配置図に未配置の機器も、対応(x,y=None)として保持する(取りこぼさない)。"""
    import fromto_qc.locator_map as LM
    parts = [{'sym': 'FX1', 'x': 100, 'y': 2000.0, 'type': ''}]
    orig = dict(ad=LA._all_device_parts, ep=LM.extract_pairs)
    LA._all_device_parts = lambda paths: parts
    LM.extract_pairs = lambda path: [('2-A', 'FX1'), ('2-G', 'DTX9')]  # DTX9は配置図に無い
    try:
        rows = LAP.locator_rows(['x'], dct_paths=['dct'])
        d = {r[1]: (r[0], r[2]) for r in rows}
        assert d['FX1'] == ('2-A', 100), rows
        assert d['DTX9'] == ('2-G', None), rows   # 対応は残し、位置はNone
    finally:
        LA._all_device_parts, LM.extract_pairs = orig['ad'], orig['ep']


def test_locator_rows_falls_back_without_table():
    """対応表が無ければ(DCT無し/空)、基割当にフォールバックする。"""
    import fromto_qc.locator_map as LM
    parts = [{'sym': 'FX1', 'x': 100, 'y': 2000.0, 'type': ''},
             {'sym': 'ALX1', 'x': 900, 'y': 2000.0, 'type': ''}]
    orig = dict(ad=LA._all_device_parts, ep=LM.extract_pairs)
    LA._all_device_parts = lambda paths: parts
    LM.extract_pairs = lambda path: []                    # 対応表なし
    try:
        rows = LAP.locator_rows(['x'], basis_of={'FX1': '2', 'ALX1': '2'}, dct_paths=['dct'])
        assert ('2-A', 'FX1') in [(r[0], r[1]) for r in rows], rows
    finally:
        LA._all_device_parts, LM.extract_pairs = orig['ad'], orig['ep']


if __name__ == '__main__':
    test_locator_rows_structure(); test_locator_rows_with_basis()
    test_locator_rows_prefers_dct_table(); test_locator_rows_table_keeps_unplaced()
    test_locator_rows_falls_back_without_table()
    print('locator_apply tests: OK')
