# -*- coding: utf-8 -*-
"""操作スイッチ 3-<回路> の接点を手本式 入/切 に変換する _onoff_switch の固定テスト。
F001実データで検証した対応: 11/12=切, 13/14=入（AR22 b接点/a接点）。"""
from .harness_sheet import _onoff_switch


def test_off_contacts():
    # b接点(11,12) → 切、端子は空
    assert _onoff_switch('3', '101', '11') == ('切', '101', '')
    assert _onoff_switch('3', '101', '12') == ('切', '101', '')
    assert _onoff_switch('3', '202', '12') == ('切', '202', '')


def test_on_contacts():
    # a接点(13,14) → 入、端子は空
    assert _onoff_switch('3', '101', '13') == ('入', '101', '')
    assert _onoff_switch('3', '102', '14') == ('入', '102', '')


def test_pushbuttons_unchanged():
    # 押ボタン(英字番号 BT1/BS1/LT1)は対象外＝そのまま
    assert _onoff_switch('3', 'BT1', '13') == ('3', 'BT1', '13')
    assert _onoff_switch('3', 'BS1', '14') == ('3', 'BS1', '14')
    assert _onoff_switch('3', 'LT1', '?') == ('3', 'LT1', '?')


def test_other_devices_unchanged():
    # 他機器・他端子は不変
    assert _onoff_switch('52', '101', '13') == ('52', '101', '13')
    assert _onoff_switch('3', '101', '9') == ('3', '101', '9')   # 11-14以外
    assert _onoff_switch('86X', '101', '11') == ('86X', '101', '11')


if __name__ == '__main__':
    test_off_contacts(); test_on_contacts()
    test_pushbuttons_unchanged(); test_other_devices_unchanged()
    print('onoff_switch tests: OK')
