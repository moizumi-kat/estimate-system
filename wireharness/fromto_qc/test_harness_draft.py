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


def test_tb_pending_surfaced():
    # 片端TBが'?'・相手端は解決済み → 'TB:台番号要確認'で可視化(確度:精査)。
    orig = (hd.main_circuit.generate, hd.fixed_wiring.generate,
            hd.edge_trace.trace_edges, hd._layout, hd._seq)
    _patch(mc=[], fw=[],
           et={frozenset((('FX1', '13'), ('TB', '?')))})
    try:
        d = hd.build('s')
        tos = {w['to'] for w in d['wires']}
        froms = {w['from'] for w in d['wires']}
        assert 'TB:台番号要確認' in tos and 'FX1:13' in froms
        assert all(w['tier'] == hd.TIER_LOW for w in d['wires'])
    finally:
        (hd.main_circuit.generate, hd.fixed_wiring.generate,
         hd.edge_trace.trace_edges, hd._layout, hd._seq) = orig


def test_circuit_of():
    assert hd._circuit_of('52-101') == '101'
    assert hd._circuit_of('86X-103') == '103'
    assert hd._circuit_of('GL-102') == '102'
    assert hd._circuit_of('MCCB-1') is None
    assert hd._circuit_of('FX-1') is None


def test_tb_pending_circuit_grouping():
    # TB台番号は回路でグルーピング(事例規則)。機器端に回路があれば 'TB:回路NNN'。
    orig = (hd.main_circuit.generate, hd.fixed_wiring.generate,
            hd.edge_trace.trace_edges, hd._layout, hd._seq)
    _patch(mc=[], fw=[], et={frozenset((('52-101', '73'), ('TB', '?')))})
    try:
        d = hd.build('s')
        assert any(w['to'] == 'TB:回路101' for w in d['wires'])
    finally:
        (hd.main_circuit.generate, hd.fixed_wiring.generate,
         hd.edge_trace.trace_edges, hd._layout, hd._seq) = orig


def test_both_ends_unknown_dropped():
    # 両端'?'(例: 動力用多接点同士)は出さない。
    orig = (hd.main_circuit.generate, hd.fixed_wiring.generate,
            hd.edge_trace.trace_edges, hd._layout, hd._seq)
    _patch(mc=[], fw=[], et={frozenset((('52-101', '?'), ('TB', '?')))})
    try:
        assert hd.build('s')['wires'] == []
    finally:
        (hd.main_circuit.generate, hd.fixed_wiring.generate,
         hd.edge_trace.trace_edges, hd._layout, hd._seq) = orig


def test_to_csv_and_html(tmp_path=None):
    import os
    import tempfile
    draft = {
        'wires': [
            {'from': 'LUG:', 'to': 'MCCB-105:1', 'sources': ['main_circuit'], 'tier': hd.TIER_MID},
            {'from': 'FX1:13', 'to': 'CX101:14', 'sources': ['edge_trace'], 'tier': hd.TIER_LOW},
        ],
        'summary': {hd.TIER_HI: 0, hd.TIER_MID: 1, hd.TIER_LOW: 1},
    }
    d = tempfile.mkdtemp()
    cp = hd.to_csv(draft, os.path.join(d, 't.csv'))
    hp = hd.to_html(draft, os.path.join(d, 't.html'), seiban='TEST-1')
    csv_txt = open(cp, encoding='cp932').read()
    assert '確度' in csv_txt and 'MCCB-105:1' in csv_txt
    html_txt = open(hp, encoding='utf-8').read()
    assert 'TEST-1' in html_txt and hd.TIER_MID in html_txt and 'FX1:13' in html_txt


def test_write_drafts_index():
    import os
    import tempfile
    orig = hd.build
    hd.build = lambda d: {
        'wires': [{'from': 'LUG:', 'to': 'MCCB-1:1', 'sources': ['main_circuit'], 'tier': hd.TIER_MID}],
        'summary': {hd.TIER_HI: 0, hd.TIER_MID: 1, hd.TIER_LOW: 0},
    }
    try:
        d = tempfile.mkdtemp()
        ip = hd.write_drafts([os.path.join(d, '製番A'), os.path.join(d, '製番B')],
                             os.path.join(d, 'out'))
        idx = open(ip, encoding='utf-8').read()
        assert '製番A' in idx and '製番B' in idx
        assert os.path.exists(os.path.join(d, 'out', '製番A.html'))
    finally:
        hd.build = orig


if __name__ == '__main__':
    test_corroboration_is_high(); test_single_source_tiers()
    test_to_csv_and_html(); test_write_drafts_index()
    print('harness_draft tests: OK')
