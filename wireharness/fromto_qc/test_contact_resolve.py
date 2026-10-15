# -*- coding: utf-8 -*-
"""contact_resolve(接点シンボルのピン解決)の固定テスト。DXF非依存部を中心に。"""
from . import contact_resolve as cr


def test_contact_type():
    assert cr.contact_type('_LS_A--T') == 'a'
    assert cr.contact_type('_LS_B--T') == 'b'
    assert cr.contact_type('_LS_AH-T') == 'a_alarm'
    assert cr.contact_type('_LS_BH-T') == 'b_alarm'
    assert cr.contact_type('_LS_CH-B_C') == 'c'
    assert cr.contact_type('LA_AXR') is None       # 本体ブロックは接点シンボルでない
    assert cr.contact_type('') is None


def test_is_aux_relay():
    assert cr.is_aux_relay('86X') is True
    assert cr.is_aux_relay('FX') is True
    assert cr.is_aux_relay('ALX-1') is True
    assert cr.is_aux_relay('CX101') is True
    assert cr.is_aux_relay('52') is False          # 動力用接触器
    assert cr.is_aux_relay('MCCB') is False


def test_std_pins():
    assert cr._std_pins('a', 1) == ('13', '14')    # NO 第1接点
    assert cr._std_pins('b', 1) == ('11', '12')    # NC 第1接点
    assert cr._std_pins('a', 2) == ('23', '24')
    assert cr._std_pins('c', 1) is None


def test_resolve_relay_single_vs_power(monkeypatch=None):
    # scan を差し替え、リレー単一接点=確定候補／動力用=要確認 を検証
    orig = cr.scan
    cr.scan = lambda paths: {
        '86X-101': {'unlabeled': [{'type': 'a', 'base': '86X', 'x': 0, 'y': 0}],
                    'labeled_pins': set()},
        '52-101': {'unlabeled': [{'type': 'b', 'base': '52', 'x': 0, 'y': 0}],
                   'labeled_pins': set()},
        'FX-1': {'unlabeled': [{'type': 'b', 'base': 'FX', 'x': 0, 'y': 10},
                               {'type': 'b', 'base': 'FX', 'x': 0, 'y': 0}],
                 'labeled_pins': set()},
    }
    try:
        r = {x['device']: x for x in cr.resolve(['x'])}
        # 省略されないよう device 重複時は最後が残る。個別に確認:
        res = cr.resolve(['x'])
        d86 = [x for x in res if x['device'] == '86X-101'][0]
        assert d86['pins'] == ('13', '14') and d86['confidence'] == '確度:精査'
        d52 = [x for x in res if x['device'] == '52-101'][0]
        assert d52['pins'] is None and d52['confidence'] == '要確認'   # 動力用は未確定
        fx = [x for x in res if x['device'] == 'FX-1']
        assert all(x['pins'] is None for x in fx)                     # 複数b接点=未確定
    finally:
        cr.scan = orig


if __name__ == '__main__':
    test_contact_type(); test_is_aux_relay(); test_std_pins()
    test_resolve_relay_single_vs_power()
    print('contact_resolve tests: OK')
