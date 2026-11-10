# -*- coding: utf-8 -*-
"""stage_interactive(図面インタラクティブ確認UI)の固定テスト。描画はモック。"""
from . import stage_interactive as SI


def test_to_px():
    ext = (0, 0, 100, 50)   # x0,y0,x1,y1
    assert SI._to_px(0, 50, ext, 200, 100) == (0.0, 0.0)       # 左上
    assert SI._to_px(100, 0, ext, 200, 100) == (200.0, 100.0)  # 右下
    assert SI._to_px(50, 25, ext, 200, 100) == (100.0, 50.0)   # 中央


def test_locator_confirm_html(tmpdir=None):
    import tempfile, os
    orig_rows = SI.LAP.locator_rows
    orig_render = SI._render_base
    SI.LAP.locator_rows = lambda layout, basis: [('1-A', 'CX101', 706, 2676, 'RU2S')]
    SI._render_base = lambda path, pts, **k: ('data:image/png;base64,AAAA', 1000, 800, (0, 0, 2000, 3000))
    try:
        out = os.path.join(tempfile.mkdtemp(), 'loc.html')
        n = SI.locator_confirm(['D001.DXF'], 'TEST', out)
        assert n == 1
        doc = open(out, encoding='utf-8').read()
        assert '1-A' in doc and 'CX101' in doc and 'ロケーター確認' in doc
        assert 'data:image/png;base64,AAAA' in doc and '"px":' in doc
    finally:
        SI.LAP.locator_rows = orig_rows
        SI._render_base = orig_render


def test_stage1_wires_html():
    import tempfile, os
    orig_trace = SI.edge_trace.trace_edges
    orig_render = SI._render_base
    SI.edge_trace.trace_edges = lambda p, with_pos=True: (
        None, None, [[['52-101', '5', 1200.0, 1670.0], ['AM-101', '1', 1200.0, 1900.0]]])
    SI._render_base = lambda path, pts, **k: ('data:image/png;base64,BBBB', 1000, 800, (0, 0, 2000, 3000))
    try:
        out = os.path.join(tempfile.mkdtemp(), 'w.html')
        m = SI.stage1_wires(['E001.DXF'], 'TEST', out)
        assert m == 1, m
        doc = open(out, encoding='utf-8').read()
        assert '52-101:5' in doc and 'AM-101:1' in doc and '太物配線' in doc
        assert 'data:image/png;base64,BBBB' in doc
    finally:
        SI.edge_trace.trace_edges = orig_trace
        SI._render_base = orig_render


def test_export_stage1_harness(tmp_path=None):
    import tempfile, os, csv
    orig = SI.edge_trace.trace_edges
    SI.edge_trace.trace_edges = lambda p, with_pos=True: (
        None, None, [[['52-101', '5', 1200.0, 1670.0], ['AM-101', '1', 1200.0, 1900.0]],
                     [['AM-101', '1', 1200.0, 1900.0], ['AM-101', '1', 1200.0, 1900.0]]])  # 自己ループは除外
    try:
        out = os.path.join(tempfile.mkdtemp(), 'h.csv')
        n = SI.export_stage1_harness(['E001.DXF'], 'TEST', out)
        assert n == 1, n
        rows = list(csv.reader(open(out, encoding='cp932')))
        assert rows[0] == ['シート', 'From機器', 'From端子', 'To機器', 'To端子', 'サイズ']
        assert rows[1] == ['E001', '52-101', '5', 'AM-101', '1', '']
    finally:
        SI.edge_trace.trace_edges = orig


if __name__ == '__main__':
    test_to_px(); test_locator_confirm_html(); test_stage1_wires_html(); test_export_stage1_harness()
    print('stage_interactive tests: OK')
