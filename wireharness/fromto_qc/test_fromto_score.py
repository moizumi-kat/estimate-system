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


def test_union_find_nets_groups_connected():
    # ネット採点の連結成分: A-B, B-C は1ネット, D-E は別ネット。
    e = {frozenset((('A', '1'), ('B', '2'))),
         frozenset((('B', '2'), ('C', '3'))),
         frozenset((('D', '1'), ('E', '2')))}
    find, nodes = FS._union_find_nets(e)
    assert find(('A', '1')) == find(('C', '3'))          # A と C は同じネット(B経由)
    assert find(('A', '1')) != find(('D', '1'))          # A と D は別ネット
    assert ('A', '1') in nodes and ('E', '2') in nodes


def test_net_recall_credits_any_chain_order():
    # 手本が A-B-C を A-B,B-C の順で記録、生成が A-C,C-B の別鎖順でも同一ネット→全辺hit。
    find, nodes = FS._union_find_nets(
        {frozenset((('A', '1'), ('C', '3'))), frozenset((('C', '3'), ('B', '2')))})
    truth = [frozenset((('A', '1'), ('B', '2'))), frozenset((('B', '2'), ('C', '3')))]
    hit = sum(1 for e in truth for a, b in [tuple(e)] if find(a) == find(b))
    assert hit == 2                                      # 鎖順が違っても機能同一=2辺とも一致


def test_fold_term_contacts_to_nonc():
    # 接点は NO/NC に畳む(機能等価)。主回路端子・空欄はそのまま(大文字化/空白除去)。
    assert FS._fold_term('86X101', '13') == 'NO'        # a接点
    assert FS._fold_term('86X101', '11') == 'NC'        # b接点
    assert FS._fold_term('MCCB101', '1') == '1'         # 主回路端子はそのまま
    assert FS._fold_term('GL101', '') == ''             # 空欄はそのまま


if __name__ == '__main__':
    test_locator_resolution_prefers_dct()
    test_locator_resolution_falls_back_without_dct()
    test_union_find_nets_groups_connected()
    test_net_recall_credits_any_chain_order()
    test_fold_term_contacts_to_nonc()
    print('fromto_score tests: OK')
