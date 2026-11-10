# -*- coding: utf-8 -*-
"""conn_view のDXF非依存部(ラベルASCII化)の固定テスト。描画本体はDXFが要るため別途。"""
from . import conn_view as cv


def test_ascii_label():
    assert cv._ascii('52-102', 'A1') == '52:A1'      # 機器-番号 → 機器:端子
    assert cv._ascii('86X', '13') == '86X:13'
    assert cv._ascii('接点', '?') == '?'              # 和字dev・不明端子 → '?'
    assert cv._ascii('接点', '1BZ06') == '1BZ06'      # 和字devは除去、端子は残す
    assert cv._ascii('OL', '+') == 'OL:+'


def test_render_is_callable():
    # 署名と既定値の健全性(実描画はDXFフィクスチャが要るのでここでは呼ばない)
    import inspect
    sig = inspect.signature(cv.render)
    assert list(sig.parameters) == ['dxf_path', 'out_path', 'crop', 'dpi']
    assert sig.parameters['crop'].default is None


if __name__ == '__main__':
    test_ascii_label()
    test_render_is_callable()
    print('conn_view tests: OK')
