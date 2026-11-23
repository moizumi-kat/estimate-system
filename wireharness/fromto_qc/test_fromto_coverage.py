# -*- coding: utf-8 -*-
"""fromto_coverage の機器識別・場外ケーブル判定の固定テスト(両端識別100%の根拠)。"""
from . import fromto_coverage as FC


def test_classify_door_switches():
    # 扉操作SW(入=ON/切=OFF/押ボタン)は設計DXFに無くても識別可能(扉外形図の標準機器)。
    for s in ('入-101', '切-101', 'ON-1', 'OFF-2', 'BS', 'BT'):
        assert FC.classify_sym(s, set(), set(), {}) == 'door', s


def test_classify_unit_and_breaker_and_locator():
    # 計測ユニット(名称にﾕﾆｯﾄ/ユニット)・複合遮断器・回路前置ロケーターを識別。
    assert FC.classify_sym('本体-ﾕﾆｯﾄ1', set(), set(), {}) == 'unit'
    assert FC.classify_sym('MCCB.ELCB-303.304', set(), set(), {}) == 'in_dxf'
    assert FC.classify_sym('1A', set(), set(), {}) == 'locator'   # 回路前置ロケーター
    assert FC.classify_sym('A', set(), set(), {}) == 'locator'    # 素ロケーター
    assert FC.classify_sym('LT', set(), set(), {}) == 'door'      # ライン/バス


def test_is_external_cable():
    # 場外ケーブル: 端子が電線色-番号(白-1 等) または 機器が R-F/L-F/F<<。
    assert FC._is_external_key(('F<<', '白-1'))
    assert FC._is_external_key(('L-F(R)', '白-3'))
    assert FC._is_external_key(('R-F(P)', '赤-2'))
    assert not FC._is_external_key(('86X-101', '14'))   # 盤内の通常機器
    assert not FC._is_external_key(('MCCB-101', '1'))


if __name__ == '__main__':
    test_classify_door_switches()
    test_classify_unit_and_breaker_and_locator()
    test_is_external_cable()
    print('fromto_coverage tests: OK')
