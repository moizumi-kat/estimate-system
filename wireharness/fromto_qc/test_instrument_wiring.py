# -*- coding: utf-8 -*-
"""instrument_wiring(計器配線CT→AM)の固定テスト。"""
from . import instrument_wiring as iw


def test_generate(monkeypatch=None):
    orig = iw.detect_meters
    iw.detect_meters = lambda paths: {'101': {'CT': 'CT-101', 'AM': 'AM-101'},
                                      '102': {'AM': 'AM-102'}}   # 102はCT無し→出さない
    try:
        w = iw.generate(['x'])
        pairs = {(e['from'], e['to']) for e in w}
        assert ('CT-101:K', 'AM-101:1') in pairs
        assert ('CT-101:L', 'AM-101:2') in pairs
        assert not any('102' in e['from'] + e['to'] for e in w)   # CT欠けは生成しない
    finally:
        iw.detect_meters = orig


if __name__ == '__main__':
    test_generate(); print('instrument_wiring tests: OK')
