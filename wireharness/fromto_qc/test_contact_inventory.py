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


def test_is_device_type():
    # 実機器(数字コード/補助リレー/多文字型)は学習対象。
    for s in ('52', '51', '42', '6', '86X', 'FX', 'CX', 'ALX', 'MCCB', 'ELCB', 'OL', '52R'):
        assert ci._is_device_type(s), s
    # ロケーター仮名称(素の英字1字・基1桁+英字1字)は除外。'X'(素)も除外。
    for s in ('A', 'X', 'Z', '1A', '1-A', '1O', '9B', ''):
        assert not ci._is_device_type(s), s


if __name__ == '__main__':
    test_pairs(); test_pairs_for(); test_is_device_type()
    print('contact_inventory tests: OK')
