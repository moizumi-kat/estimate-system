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


if __name__ == '__main__':
    test_stage3_fromto_html()
    print('stage_views tests: OK')
