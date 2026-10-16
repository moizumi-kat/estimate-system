# -*- coding: utf-8 -*-
"""contact_inventory(接点在庫の学習)の固定テスト。"""
from . import contact_inventory as ci


def test_pairs():
    assert ci._pairs(['13', '14', '53', '54']) == [('13', '14'), ('53', '54')]
    assert ci._pairs(['14', '13']) == [('13', '14')]
    assert ci._pairs(['13']) == []          # 相方が無ければ対にしない


def test_pairs_for():
    inv = {'52': {'NO': [['13', '14'], ['53', '54']], 'NC': [['21', '22']]}}
    assert ci.pairs_for('52-101', 'NO', inv) == [('13', '14'), ('53', '54')]
    assert ci.pairs_for('52-101', 'NC', inv) == [('21', '22')]
    assert ci.pairs_for('99-1', 'NO', inv) == []   # 未知型


if __name__ == '__main__':
    test_pairs(); test_pairs_for(); print('contact_inventory tests: OK')
