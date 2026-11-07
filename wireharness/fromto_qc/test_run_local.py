# -*- coding: utf-8 -*-
"""run_local CLI の固定テスト(DXF非依存): ファイル探索・引数処理・run_workflow連携。"""
import os
import tempfile
from . import run_local as RL


def _touch(d, name):
    open(os.path.join(d, name), 'w').close()


def test_seibans_in_and_collect():
    d = tempfile.mkdtemp()
    for n in ['1-12188-132-F001.DXF', '1-12188-132-E001.DXF', '1-12188-132-D001.DXF',
              '1-12188-132-D001-DCT.dxf', '1-12188-132-1.txt',
              '5-21071-008-G001.DXF', 'readme.md']:
        _touch(d, n)
    assert RL.seibans_in(d) == ['1-12188-132', '5-21071-008'], RL.seibans_in(d)
    seq, skel, layout, dct = RL.collect_inputs(d, '1-12188-132')
    assert [os.path.basename(f) for f in seq] == ['1-12188-132-F001.DXF']
    assert [os.path.basename(f) for f in skel] == ['1-12188-132-E001.DXF']
    # 配置図は D*(DCT除外)。G* も対象だが別製番なので含まれない。
    assert [os.path.basename(f) for f in layout] == ['1-12188-132-D001.DXF']
    assert [os.path.basename(f) for f in dct] == ['1-12188-132-D001-DCT.dxf']


def test_run_calls_workflow():
    d = tempfile.mkdtemp()
    _touch(d, '1-12188-132-F001.DXF')
    captured = {}

    def fake(seiban, seq, skel, layout, dct_paths=None, out_dir='.'):
        captured.update(seiban=seiban, seq=seq, skel=skel, layout=layout,
                        dct=dct_paths, out_dir=out_dir)
        return {'hub': f'{seiban}_00_ハブ.html', '2人目': {'files': {'x': 'a.html'}}}
    orig = RL.workflow.run_workflow
    RL.workflow.run_workflow = fake
    try:
        res = RL.run(d, '1-12188-132', out_dir=os.path.join(d, 'out'))
        assert captured['seiban'] == '1-12188-132'
        assert [os.path.basename(f) for f in captured['seq']] == ['1-12188-132-F001.DXF']
        assert captured['out_dir'].endswith('out')
        assert res['hub'] == '1-12188-132_00_ハブ.html'
    finally:
        RL.workflow.run_workflow = orig


def test_main_lists_seibans_without_arg():
    d = tempfile.mkdtemp()
    _touch(d, '3-12033-025-F001.DXF')
    assert RL.main([d]) == 0        # 製番省略: 一覧表示して正常終了


if __name__ == '__main__':
    test_seibans_in_and_collect()
    test_run_calls_workflow()
    test_main_lists_seibans_without_arg()
    print('run_local tests: OK')
