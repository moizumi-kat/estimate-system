# -*- coding: utf-8 -*-
"""固定端点語彙の固定テスト。"""
from . import fixed_points as fp


def test_recognition():
    for d in ['LUG', 'BOX', 'BOX-ET', 'SPD', 'SPD-分離器1', '分離器2', 'ET']:
        assert fp.is_fixed(d), d
    for d in ['MCCB-105', 'ELCB-101', 'TB-11', '52-101', 'CT-101', 'A']:
        assert not fp.is_fixed(d), d


def test_base_of():
    assert fp.base_of('SPD-分離器1') == 'SPD-分離器'
    assert fp.base_of('分離器2') == 'SPD-分離器'
    assert fp.base_of('LUG') == 'LUG'
    assert fp.base_of('BOX-ET') == 'BOX'
    assert fp.base_of('MCCB-1') is None


def test_phase_of():
    assert fp.phase_of('SPD-分離器1', '1 上') == 'R'
    assert fp.phase_of('SPD-分離器1', '2 上') == 'S'
    assert fp.phase_of('SPD-分離器1', '3 上') == 'T'
    assert fp.phase_of('SPD-分離器1', 'E 下') == 'E'
    assert fp.phase_of('BOX', 'ET') == 'E'
    assert fp.phase_of('BOX', '') == 'E'
    assert fp.phase_of('LUG', '') is None       # LUGは相を持たない(入力側)


def test_terminal_known():
    assert fp.terminal_known('SPD-分離器1', '1 上')
    assert fp.terminal_known('LUG', '')
    assert not fp.terminal_known('SPD-分離器1', '99')


if __name__ == '__main__':
    test_recognition()
    test_base_of()
    test_phase_of()
    test_terminal_known()
    print('fixed_points tests: OK')
