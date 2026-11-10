# -*- coding: utf-8 -*-
"""dwg_convert の固定テスト(実DWG非依存=社内資産をコミットしない)。

変換器の有無に依らず壊れない配線を検証する。convert_dir は [(dwg, dxf|None, error)]。
"""
import os
import tempfile
from . import dwg_convert as DWG


def test_empty_dir_is_noop():
    with tempfile.TemporaryDirectory() as d:
        assert DWG.convert_dir(d) == []


def test_ensure_dxf_passthrough():
    # .dxf はそのまま返す(変換しない)
    assert DWG.ensure_dxf('/x/y/A-F001.dxf') == '/x/y/A-F001.dxf'
    # 対象外拡張子もそのまま
    assert DWG.ensure_dxf('/x/y/note.txt') == '/x/y/note.txt'


def test_existing_dxf_is_reused():
    # 同名の .dwg と(中身のある) .dxf があれば、変換せず既存DXFを返す
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, 'A-F001.dwg'), 'wb').write(b'\x00fake-dwg')
        dxf = os.path.join(d, 'A-F001.dxf')
        open(dxf, 'w').write('0\nSECTION\n')
        assert DWG.ensure_dxf(os.path.join(d, 'A-F001.dwg')) == dxf


def test_bad_dwg_reported_not_raised():
    # 不正DWG(または変換器が無い環境)でも convert_dir は例外を投げず error を返す。
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, 'B-F001.dwg'), 'wb').write(b'not a real dwg file')
        res = DWG.convert_dir(d, sanitize=True)
        assert len(res) == 1
        src, dxf, err = res[0]
        assert os.path.basename(src) == 'B-F001.dwg'
        assert dxf is None and err  # 失敗が報告される
