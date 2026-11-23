# -*- coding: utf-8 -*-
"""pin_audit の TYPE→ピン配照合の固定テスト（端子健全性の土台）。"""
from . import pin_audit as PA


def test_pattern_pins_known_relay():
    # RU4S-A200 はレジストリ登録済み。NO接点ピン5,6,7,8 を含む（手本の86X=RU4Sに一致）。
    pat, pins = PA._pattern_pins('RU4S-A200')
    assert pat == 'relay_RU4S', pat
    assert {'5', '6', '7', '8'} <= pins, pins          # NO接点
    assert {'13', '14'} <= pins, pins                  # コイル


def test_pattern_pins_unregistered():
    # 未登録TYPEは (None, 空集合)＝検証不能→監査で『要登録』に分類される。
    pat, pins = PA._pattern_pins('YS-8NAA')
    assert pat is None and pins == set(), (pat, pins)


def test_pattern_pins_blank():
    pat, pins = PA._pattern_pins('')
    assert pat is None and pins == set()


if __name__ == '__main__':
    test_pattern_pins_known_relay()
    test_pattern_pins_unregistered()
    test_pattern_pins_blank()
    print('pin_audit tests: OK')
