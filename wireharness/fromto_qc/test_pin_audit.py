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


def test_flag_unverified():
    # A(要確認の出力): 登録機器の実ピンはOK、未登録/TYPE無し/ピン配外は要確認を付ける。
    dt = {'86X104': ('', 'RU4S-A200'), 'VS1': ('VS', 'BHN-V')}
    members = [
        {'device': '86X', 'no': '104', 'terminal': '5'},    # RU4S実ピン→OK
        {'device': '86X', 'no': '104', 'terminal': '99'},   # RU4Sに無い→要確認(ピン配外)
        {'device': 'VS', 'no': '1', 'terminal': '1'},       # 未登録→要確認(未登録)
        {'device': 'ZZZ', 'no': '9', 'terminal': '3'},      # TYPE無→要確認(TYPE無)
        {'device': 'TB', 'no': '', 'terminal': '仮3'},       # TB→対象外
    ]
    n = PA.flag_unverified(members, dt)
    assert members[0]['pin_confirm'] == '', members[0]
    assert members[1]['pin_confirm'].startswith('要確認(ピン配外'), members[1]
    assert members[2]['pin_confirm'].startswith('要確認(未登録'), members[2]
    assert members[3]['pin_confirm'] == '要確認(TYPE無)', members[3]
    assert members[4]['pin_confirm'] == '', members[4]
    assert n == 3


if __name__ == '__main__':
    test_pattern_pins_known_relay()
    test_pattern_pins_unregistered()
    test_pattern_pins_blank()
    test_flag_unverified()
    print('pin_audit tests: OK')
