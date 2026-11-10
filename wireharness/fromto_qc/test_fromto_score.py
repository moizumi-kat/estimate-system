# -*- coding: utf-8 -*-
"""locator_resolution の優先順位(DCT採番を直接採用)固定テスト。
◎誤答ゼロ: 設計DCT(機器相番号)があれば、その採番(基-文字→実機器)を正解として直接採用し、
位置ベース再生成(幾何assign)は使わない。表記ハイフンは norm で図面側symに揃える。"""
from . import fromto_score as FS


def test_locator_resolution_prefers_dct():
    orig = dict(glob=FS.glob.glob, extract=FS.LM.extract_pairs,
                seq=FS._seq_paths, assign=FS.LA.assign)
    FS.glob.glob = lambda pat: (['dct.dxf'] if '機器相番号' in pat else [])
    FS.LM.extract_pairs = lambda p: [('2-A', '2-1'), ('2-B', 'RST1'), ('2-C', 'RST1')]
    FS._seq_paths = lambda d: []

    def _boom(layout):
        raise AssertionError('DCTがあるのに幾何assignを使用した')
    FS.LA.assign = _boom
    try:
        m = FS.locator_resolution('X')
        # 基-文字は直接採用。表記 '2-1' は norm で図面側 '21' に揃う。
        assert m['2-A'] == '21', m
        assert m['2-B'] == 'RST1' and m['2-C'] == 'RST1', m
        # 素letter 'A' は一意なので登録。'C' は重複(RST1が2箇所)でも値同一なので一意扱い。
        assert m.get('A') == '21', m
    finally:
        FS.glob.glob, FS.LM.extract_pairs = orig['glob'], orig['extract']
        FS._seq_paths, FS.LA.assign = orig['seq'], orig['assign']


def test_locator_resolution_falls_back_without_dct():
    # DCTが無ければ幾何assignで代替する。
    orig = dict(glob=FS.glob.glob, seq=FS._seq_paths, assign=FS.LA.assign,
                load=FS.ft.load_seiban)
    FS.glob.glob = lambda pat: []                      # DCTもレイアウトも無し
    FS._seq_paths = lambda d: []
    FS.LA.assign = lambda layout: {'by_locator': {'1-A': 'FX1'}}
    FS.ft.load_seiban = lambda d: {'wires': []}
    try:
        m = FS.locator_resolution('X')
        assert m['1-A'] == 'FX1' and m.get('A') == 'FX1', m
    finally:
        FS.glob.glob, FS._seq_paths = orig['glob'], orig['seq']
        FS.LA.assign, FS.ft.load_seiban = orig['assign'], orig['load']


if __name__ == '__main__':
    test_locator_resolution_prefers_dct()
    test_locator_resolution_falls_back_without_dct()
    print('fromto_score tests: OK')
