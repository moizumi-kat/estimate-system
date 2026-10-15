# -*- coding: utf-8 -*-
"""fixed_wiring(SPD分離器の固定配線生成)の固定テスト。DXFに依存しない。"""
from . import fixed_wiring as fw


def test_no_separator_generates_nothing(monkeypatch=None):
    # 分離器が無ければ何も足さない(対象外製番で安全)
    import types
    orig = fw.detect_separators
    fw.detect_separators = lambda paths: {}
    try:
        assert fw.generate(['dummy']) == []
    finally:
        fw.detect_separators = orig


def _patch(seps, sink_phases):
    fw.detect_separators = lambda paths: seps
    fw.detect_earth_sink = lambda paths: sink_phases


def test_control_panel_pattern():
    # 制御盤3φ: SPD接地・R/S/T。候補は全て要確認(自動確定しない=誤答ゼロ)。
    os = (fw.detect_separators, fw.detect_earth_sink)
    _patch({'1': 'SPD-分離器1'}, ('SPD', ('R', 'S', 'T')))
    try:
        w = fw.generate(['x'])
        pairs = {(e['from'], e['to']) for e in w}
        assert ('TB-1:1R', 'SPD-分離器1:1 上') in pairs
        assert ('TB-1:1S', 'SPD-分離器1:2 上') in pairs
        assert ('TB-1:1T', 'SPD-分離器1:3 上') in pairs
        assert ('SPD-分離器1:E 下', 'SPD:') in pairs
        assert ('SPD:', 'BOX:') in pairs
        assert all(e['confidence'] == '要確認' for e in w)   # ◎誤答ゼロ
    finally:
        fw.detect_separators, fw.detect_earth_sink = os


def test_distribution_panel_pattern():
    # 分電盤: SPD-ET-(MCCB)接地・R/N/T。
    os = (fw.detect_separators, fw.detect_earth_sink)
    _patch({'1': 'SPD-分離器1'}, ('SPD-ET-(MCCB)', ('R', 'N', 'T')))
    try:
        w = fw.generate(['x'])
        pairs = {(e['from'], e['to']) for e in w}
        assert ('TB-1:1N', 'SPD-分離器1:2 上') in pairs      # 中性相N
        assert ('SPD-分離器1:E 下', 'SPD-ET-(MCCB):') in pairs
        assert ('SPD-ET-(MCCB):', 'BOX:') in pairs
    finally:
        fw.detect_separators, fw.detect_earth_sink = os


def test_class1_excluded():
    # クラスⅠ(14sq=太物)は細物生成しない。全てⅠなら接地箱配線も出ない(現場回答2026-10-15)。
    os_ = (fw.detect_separators, fw.detect_earth_sink)
    fw.detect_separators = lambda paths: {'1': {'sym': 'SPD-分離器1', 'class': 'Ⅰ', 'integrated': False}}
    fw.detect_earth_sink = lambda paths: ('SPD', ('R', 'S', 'T'))
    try:
        assert fw.generate(['x']) == []
    finally:
        fw.detect_separators, fw.detect_earth_sink = os_


def test_class2_integrated_noted():
    # クラスⅡ一体型は生成するが、配線しない例があるため basis に明示(要確認)。
    os_ = (fw.detect_separators, fw.detect_earth_sink)
    fw.detect_separators = lambda paths: {'1': {'sym': 'SPD-分離器1', 'class': 'Ⅱ', 'integrated': True}}
    fw.detect_earth_sink = lambda paths: ('SPD', ('R', 'S', 'T'))
    try:
        w = fw.generate(['x'])
        assert w and any('一体型' in e['basis'] for e in w)
        assert all(e['confidence'] == '要確認' for e in w)
    finally:
        fw.detect_separators, fw.detect_earth_sink = os_


def test_legacy_str_format():
    # 旧形式(番号→記号文字列)でも動く(後方互換)。
    os_ = (fw.detect_separators, fw.detect_earth_sink)
    fw.detect_separators = lambda paths: {'1': 'SPD-分離器1'}
    fw.detect_earth_sink = lambda paths: ('SPD', ('R', 'S', 'T'))
    try:
        w = fw.generate(['x'])
        assert ('TB-1:1R', 'SPD-分離器1:1 上') in {(e['from'], e['to']) for e in w}
    finally:
        fw.detect_separators, fw.detect_earth_sink = os_


if __name__ == '__main__':
    test_no_separator_generates_nothing()
    test_control_panel_pattern()
    test_distribution_panel_pattern()
    test_class1_excluded()
    test_class2_integrated_noted()
    test_legacy_str_format()
    print('fixed_wiring tests: OK')
