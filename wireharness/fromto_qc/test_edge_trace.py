# -*- coding: utf-8 -*-
"""edge_trace の渡り分解(母線→隣接鎖)の固定テスト。DXF非依存部を中心に。"""
from . import edge_trace as et


def test_rail_order_horizontal():
    # 水平母線(xの広がりが大)→ x昇順。
    pos = {'a': (300.0, 100.0), 'b': (100.0, 100.0), 'c': (200.0, 100.0)}
    assert et._rail_order(['a', 'b', 'c'], pos) == ['b', 'c', 'a']


def test_rail_order_vertical():
    # 垂直母線(yの広がりが大)→ 上(大y)から下。
    pos = {'a': (10.0, 100.0), 'b': (10.0, 300.0), 'c': (10.0, 200.0)}
    assert et._rail_order(['a', 'b', 'c'], pos) == ['b', 'c', 'a']


def test_rail_order_chain_edges():
    # 母線の端子を鎖にすると隣接のみ(k-1本)。完全グラフ(k(k-1)/2)にしない。
    pos = {n: (float(i) * 100, 0.0) for i, n in enumerate(['n0', 'n1', 'n2', 'n3'])}
    order = et._rail_order(['n3', 'n0', 'n2', 'n1'], pos)
    edges = {frozenset((a, b)) for a, b in zip(order, order[1:])}
    assert edges == {frozenset(('n0', 'n1')), frozenset(('n1', 'n2')), frozenset(('n2', 'n3'))}
    assert len(edges) == 3        # k-1本(完全グラフなら6本)


def test_face():
    # 面(盤)=回路番号(3桁)の先頭桁。
    assert et._face('52-102') == '1'
    assert et._face('52-205') == '2'
    assert et._face('6-601') == '6'
    assert et._face('MCCB') == ''        # 回路番号(3桁)なし
    assert et._face('TB-13') == ''       # TB台番号は2桁で対象外(面跨ぎ抑制ではTBは別扱い)


if __name__ == '__main__':
    test_rail_order_horizontal(); test_rail_order_vertical(); test_rail_order_chain_edges()
    test_face()
    print('edge_trace tests: OK')
