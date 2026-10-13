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


if __name__ == '__main__':
    test_strip_marks(); test_normalize_sym()
    print('device_norm tests: OK')
