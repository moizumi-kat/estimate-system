# -*- coding: utf-8 -*-
"""stage_views(確認UI生成)の固定テスト。DXF非依存(生成部をモック)。"""
import os
import tempfile
from . import stage_views as SV


def test_stage3_fromto_html():
    rows = [['101C1', 'main', 'CT', '101', 'K', 'CT', '101', 'L'],
            ['1A01', 'ctrl', '30X1A', '', '', 'ELCB101', '', 'ALa']]
    orig_rows = SV._fromto_rows
    orig_render = SV.conn_view.render
    orig_img = SV._img_b64
    SV._fromto_rows = lambda seq, skel, sb, tmp: rows
    SV.conn_view.render = lambda p, png: {'nets': 9, 'floating': 0, 'trace_gap': 0}
    SV._img_b64 = lambda png, max_w=1800: 'data:image/png;base64,AAAA'
    try:
        d = tempfile.mkdtemp()
        out = os.path.join(d, 't.html')
        st = SV.stage3_fromto(['F001.DXF'], [], 'TEST', out)
        assert st == {'gousen': 2, 'wires': 2, 'ctrl': 1, 'main': 1, 'sheets': 1}, st
        doc = open(out, encoding='utf-8').read()
        assert '101C1' in doc and 'CT101 :K' in doc and 'CT101 :L' in doc
        assert 'ELCB101 :ALa' in doc and '30X1A' in doc
        assert 'data:image/png;base64,AAAA' in doc      # 検査図が埋め込まれている
        assert '3人目 From-To 確認UI' in doc
    finally:
        SV._fromto_rows, SV.conn_view.render, SV._img_b64 = orig_rows, orig_render, orig_img


def test_stage2_terminals_html():
    orig_def = SV.harness_produce.terminal_number_defects
    orig_imgs = SV._conn_imgs
    SV.harness_produce.terminal_number_defects = lambda seq, skel_paths, seiban: {
        'items': [{'provisional': '仮1', 'position': {'x': 1300.0, 'y': 2100.0},
                   'gousen': ['101A'], 'fix': ''}],
        'count': 1, 'note': 'テスト'}
    SV._conn_imgs = lambda seq, tmp: [('F001', 'data:image/png;base64,AAAA', {'nets': 5})]
    try:
        import tempfile, os
        out = os.path.join(tempfile.mkdtemp(), 't.html')
        st = SV.stage2_terminals(['F001.DXF'], [], 'TEST', out)
        assert st == {'tb': 1, 'kakunin': 1, 'sheets': 1}, st
        doc = open(out, encoding='utf-8').read()
        assert '仮1' in doc and '101A' in doc and '要付番' in doc
        assert '2人目 端子番号付与 確認UI' in doc
        assert 'data:image/png;base64,AAAA' in doc
    finally:
        SV.harness_produce.terminal_number_defects = orig_def
        SV._conn_imgs = orig_imgs


def test_stage1_mainwire_html():
    import fromto_qc.main_circuit as MC
    orig_gen = MC.generate
    orig_imgs = SV._conn_imgs
    MC.generate = lambda skel, seiban=None: [
        {'from': 'LUG:', 'to': 'MCCB-101:1', 'circuit': '101', 'phase': 'U', 'confidence': '確定'},
        {'from': 'TB:302E', 'to': 'TB:401E', 'circuit': '', 'phase': 'E', 'confidence': '要確認'}]
    SV._conn_imgs = lambda seq, tmp: [('E001', 'data:image/png;base64,AAAA', {'nets': 3})]
    try:
        import tempfile, os
        out = os.path.join(tempfile.mkdtemp(), 't.html')
        st = SV.stage1_mainwire(['E001.DXF'], 'TEST', out)
        assert st['wires'] == 2 and st['kakutei'] == 1, st
        doc = open(out, encoding='utf-8').read()
        assert 'MCCB-101:1' in doc and '要確認' in doc and '太物経路' in doc
        assert 'data:image/png;base64,AAAA' in doc
    finally:
        MC.generate = orig_gen
        SV._conn_imgs = orig_imgs


if __name__ == '__main__':
    test_stage3_fromto_html()
    test_stage2_terminals_html()
    test_stage1_mainwire_html()
    print('stage_views tests: OK')
