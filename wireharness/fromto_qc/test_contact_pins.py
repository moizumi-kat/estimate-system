# -*- coding: utf-8 -*-
"""接点付番規則(contact_pins)の固定テスト。設計CAD CMNTJで整合確認済みの規則を固定。"""
from . import contact_pins as cp


def test_function_basic():
    assert cp.function('13') == 'NO'
    assert cp.function('14') == 'NO'
    assert cp.function('11') == 'NC'
    assert cp.function('12') == 'NC'
    assert cp.function('23') == 'NO'
    assert cp.function('21') == 'NC'
    assert cp.function('A1') == 'coil'
    assert cp.function('A2') == 'coil'
    assert cp.function('1') == 'pole_in'
    assert cp.function('2') == 'pole_out'
    assert cp.function('14(+)') == 'NO'       # 括弧付きも正規化


def test_thermal():
    assert cp.function('95', '51') == 'NC'
    assert cp.function('96', '51') == 'NC'
    assert cp.function('97', '51') == 'NO'
    assert cp.function('98', '51') == 'NO'
    # OL(熱動)も同様
    assert cp.function('97', 'OL') == 'NO'


def test_selector_not_no_nc():
    # 選択SW43の接点は位置接点→selector(NO/NCと誤判定しない)
    assert cp.function('13', '43') == 'selector'
    assert cp.function('21', '43') == 'selector'
    assert cp.function('13', '43') != 'NO'


def test_nth_aux():
    assert cp.nth_aux('NO', 1) == ('13', '14')
    assert cp.nth_aux('NO', 2) == ('23', '24')
    assert cp.nth_aux('NC', 1) == ('11', '12')
    assert cp.nth_aux('NC', 2) == ('21', '22')


def test_is_valid():
    assert cp.is_valid('52', '13')
    assert cp.is_valid('51', '95')
    assert not cp.is_valid('52', 'ZZZ')     # 規則外・学習外は誤答候補


if __name__ == '__main__':
    for fn in [test_function_basic, test_thermal, test_selector_not_no_nc,
               test_nth_aux, test_is_valid]:
        fn()
    print('contact_pins tests: OK')
