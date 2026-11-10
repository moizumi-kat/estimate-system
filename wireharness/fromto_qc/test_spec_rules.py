# -*- coding: utf-8 -*-
"""spec_rules(製作仕様書の属性ルール)の固定テスト。表値は手配図面5-21199の雲マーク箇所に基づく。"""
from . import spec_rules as sr


def test_phase_color():
    # 低圧三相3線式(最多): R赤/S白/T青
    assert sr.phase_color('R') == '赤'
    assert sr.phase_color('S') == '白'
    assert sr.phase_color('T') == '青'
    # 三相4線式では N は白(既存コードの N=黒 は誤りだった)
    assert sr.phase_color('N', wiring='三相4線式') == '白'
    # 単相3線式: N白/T黒
    assert sr.phase_color('T', wiring='単相3線式') == '黒'
    assert sr.phase_color('N', wiring='単相3線式') == '白'
    assert sr.phase_color('X') == ''        # 相でない


def test_circuit_color():
    assert sr.circuit_color('control') == '黄'
    assert sr.circuit_color('meter_secondary') == '黄'
    assert sr.circuit_color('ground') == '緑'
    assert sr.circuit_color('unknown') == ''


def test_wire_type():
    assert sr.wire_type('control') == 'IV'
    assert sr.wire_type('control', eco=True) == 'EM-IE'
    assert sr.wire_type('main') == 'HIV'
    assert sr.wire_type('main', eco=True) == 'EM-IE'
    assert sr.wire_type('none') is None


def test_wire_size():
    assert sr.wire_size(15) == '2'
    assert sr.wire_size(16) == '3.5'      # 15超20以下
    assert sr.wire_size(40) == '8'
    assert sr.wire_size(100) == '38'
    assert sr.wire_size(225) == '100'
    assert sr.wire_size(40, special=True) == '5.5'
    assert sr.wire_size(700) is None       # 範囲外
    assert sr.wire_size(None) is None


def test_ground():
    assert sr.ground_wire_size(100) == '5.5'
    assert sr.ground_wire_size(400) == '22'
    assert sr.ground_wire_size(2500) == '150'
    assert sr.ground_screw(100) == 'M6'
    assert sr.ground_screw(400) == 'M10'
    assert sr.ground_screw(1600) == 'M12'
    assert sr.ground_wire_size(3000) is None


def test_crimp_and_ct():
    assert sr.crimp_terminal('control') == 'Y形'
    assert sr.crimp_terminal('control', panel='配電盤') == '裸丸形'
    assert sr.crimp_terminal('main') == '裸丸形'
    assert sr.needs_ct(amps=25) is True
    assert sr.needs_ct(amps=15) is False
    assert sr.needs_ct(voltage=400) is True
    assert sr.needs_ct(amps=10, voltage=200) is False


if __name__ == '__main__':
    test_phase_color(); test_circuit_color(); test_wire_type()
    test_wire_size(); test_ground(); test_crimp_and_ct()
    print('spec_rules tests: OK')
