# -*- coding: utf-8 -*-
"""harness_draft の確度ランク(相互裏取り)・重複排除の固定テスト(DXF非依存)。"""
from . import harness_draft as hd


def _patch(mc, fw, et):
    hd.main_circuit.generate = lambda paths: mc
    hd.fixed_wiring.generate = lambda paths: fw
    hd.edge_trace.trace_edges = lambda p: (et, set())
    hd._layout = lambda d: ['x']
    hd._seq = lambda d: ['y']


def test_corroboration_is_high():
    orig = (hd.main_circuit.generate, hd.fixed_wiring.generate,
            hd.edge_trace.trace_edges, hd._layout, hd._seq)
    # 同じ配線を main と edge_trace が出す → 相互裏取り=確度:高
    _patch(
        mc=[{'from': 'LUG:', 'to': 'MCCB-105:1'}],
        fw=[],
        et={frozenset((('LUG', ''), ('MCCB-105', '1')))},
    )
    try:
        d = hd.build('s')
        assert len(d['wires']) == 1
        w = d['wires'][0]
        assert w['tier'] == hd.TIER_HI, w
        assert set(w['sources']) == {'main_circuit', 'edge_trace'}
    finally:
        (hd.main_circuit.generate, hd.fixed_wiring.generate,
         hd.edge_trace.trace_edges, hd._layout, hd._seq) = orig


def test_single_source_tiers():
    orig = (hd.main_circuit.generate, hd.fixed_wiring.generate,
            hd.edge_trace.trace_edges, hd._layout, hd._seq)
    _patch(
        mc=[{'from': 'LUG:', 'to': 'MCCB-105:1'}],          # 中(ルール単独)
        fw=[{'from': 'TB-1:1R', 'to': 'SPD-分離器1:1 上'}],  # 中
        et={frozenset((('51-101', '97'), ('51-101', '95')))},  # 精査(トレース単独)
    )
    try:
        d = hd.build('s')
        assert d['summary'][hd.TIER_MID] == 2, d['summary']
        assert d['summary'][hd.TIER_LOW] == 1, d['summary']
        assert d['summary'][hd.TIER_HI] == 0
    finally:
        (hd.main_circuit.generate, hd.fixed_wiring.generate,
         hd.edge_trace.trace_edges, hd._layout, hd._seq) = orig


if __name__ == '__main__':
    test_corroboration_is_high(); test_single_source_tiers()
    print('harness_draft tests: OK')
