# -*- coding: utf-8 -*-
"""harness_draft の統合・区分・重複排除ロジックの固定テスト(DXF非依存)。"""
from . import harness_draft as hd


def _patch(mc, fw, et):
    hd.main_circuit.generate = lambda paths: mc
    hd.fixed_wiring.generate = lambda paths: fw
    hd.edge_trace.trace_edges = lambda p: (et, set())
    hd._layout = lambda d: ['x']
    hd._seq = lambda d: ['y']


def test_tiers_and_dedup():
    orig = (hd.main_circuit.generate, hd.fixed_wiring.generate,
            hd.edge_trace.trace_edges, hd._layout, hd._seq)
    _patch(
        mc=[{'from': 'LUG:', 'to': 'MCCB-105:1', 'phase': 'U', 'confidence': '確定'},
            {'from': 'MCCB-105:2', 'to': 'TB-1:105U', 'phase': 'U', 'confidence': '確定'}],
        fw=[{'from': 'TB-1:1R', 'to': 'SPD-分離器1:1 上', 'phase': 'R'}],
        et={frozenset((('51-101', '97'), ('51-101', '95')))},
    )
    try:
        d = hd.build('seiban')
        assert d['summary']['高信頼'] == 1, d['summary']     # LUG→遮断器のみ高信頼
        assert d['summary']['候補'] == 1                      # SPD
        assert d['summary']['要確認'] == 2                    # TB相＋制御トレース
        tiers = {w['tier'] for w in d['wires']}
        assert tiers == {'高信頼', '候補', '要確認'}
    finally:
        (hd.main_circuit.generate, hd.fixed_wiring.generate,
         hd.edge_trace.trace_edges, hd._layout, hd._seq) = orig


def test_dedup_prefers_higher_tier():
    orig = (hd.main_circuit.generate, hd.fixed_wiring.generate,
            hd.edge_trace.trace_edges, hd._layout, hd._seq)
    # 同じ配線が main(高信頼) と edge_trace(要確認) に出たら高信頼を残す
    _patch(
        mc=[{'from': 'LUG:', 'to': 'MCCB-105:1'}],
        fw=[],
        et={frozenset((('LUG', ''), ('MCCB-105', '1')))},
    )
    try:
        d = hd.build('s')
        assert len(d['wires']) == 1
        assert d['wires'][0]['tier'] == '高信頼'
    finally:
        (hd.main_circuit.generate, hd.fixed_wiring.generate,
         hd.edge_trace.trace_edges, hd._layout, hd._seq) = orig


if __name__ == '__main__':
    test_tiers_and_dedup(); test_dedup_prefers_higher_tier()
    print('harness_draft tests: OK')
