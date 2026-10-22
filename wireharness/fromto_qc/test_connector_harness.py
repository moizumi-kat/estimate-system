# -*- coding: utf-8 -*-
"""connector_harness(コネクタ・ハーネス要結線リスト)の固定テスト。DXF非依存部を中心に。"""
from . import connector_harness as ch


def test_phases():
    assert ch._phases('U,W,E') == ['U', 'W', 'E']
    assert ch._phases('U, N ,E') == ['U', 'N', 'E']
    assert ch._phases('') == []
    assert ch._phases(None) == []


def test_pin_endpoints_and_earth_bundle():
    # enumerate_connectors を差し替えてDXF非依存に。
    orig = ch.enumerate_connectors
    ch.enumerate_connectors = lambda sd: [
        {'番号': '115', '相': ['U', 'W', 'E'], 'maker': '', 'code': '', 'spec': '2P', 'pos': (0, 0)},
        {'番号': '101', '相': ['U', 'N', 'E'], 'maker': '', 'code': '', 'spec': '2P', 'pos': (1, 1)},
        {'番号': '200', '相': ['U', 'W'],      'maker': '', 'code': '', 'spec': '2P', 'pos': (2, 2)},  # E無し
    ]
    try:
        pins = ch.pin_endpoints('x')
        # 全ピンが端点として展開(3+3+2=8)
        assert len(pins) == 8
        assert {'機器': 'ｺﾈｸﾀ', '番号': '115', '端子': 'E'} in pins
        assert all(p['機器'] == 'ｺﾈｸﾀ' for p in pins)
        # 接地束: E相を持つコネクタのみ(2件)。相手はブレーカ:ET(要割付)。
        earth = ch.earth_bundle_candidates('x')
        assert len(earth) == 2
        e0 = earth[0]
        assert e0['from'] == {'機器': 'ｺﾈｸﾀ', '番号': '115', '端子': 'E'}
        assert e0['to'] == {'機器': 'ブレーカ', '番号': '要割付', '端子': 'ET'}
        assert e0['確度'] == '確度:精査'
        assert e0['sources'] == ['接地束']
        # worklist サマリ
        w = ch.build_worklist('x')
        assert w['summary'] == {'コネクタ数': 3, 'ピン端点数': 8, '接地束候補': 2}
    finally:
        ch.enumerate_connectors = orig


if __name__ == '__main__':
    test_phases(); test_pin_endpoints_and_earth_bundle()
    print('connector_harness tests: OK')
