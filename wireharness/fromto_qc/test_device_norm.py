# -*- coding: utf-8 -*-
"""機器記号正規化(device_norm)の固定テスト。"""
from . import device_norm as dn


def test_strip_marks():
    assert dn.strip_marks('○LG05') == 'LG05'
    assert dn.strip_marks('◎LG55') == 'LG55'
    assert dn.strip_marks('●A') == 'A'
    assert dn.strip_marks('LG05') == 'LG05'


def test_normalize_sym():
    assert dn.normalize_sym('ELCB', '○LG05') == 'ELCB-LG05'
    assert dn.normalize_sym('MCCB', '◎LG55') == 'MCCB-LG55'
    assert dn.normalize_sym('ELCB-○CG15') == 'ELCB-CG15'
    assert dn.normalize_sym('MCCB', '105') == 'MCCB-105'
    assert dn.normalize_sym('LUG') == 'LUG'


def test_canon():
    # ハイフン等の表記ゆれを吸収(突合の両辺に等適用)
    assert dn.canon('FX-1') == dn.canon('FX1') == 'FX1'
    assert dn.canon('ELCB-○LG05') == 'ELCBLG05'
    assert dn.canon('52-101') == dn.canon('52101') == '52101'
    assert dn.canon('SPD-分離器1') == dn.canon('SPD分離器1')


if __name__ == '__main__':
    test_strip_marks(); test_normalize_sym(); test_canon()
    print('device_norm tests: OK')
