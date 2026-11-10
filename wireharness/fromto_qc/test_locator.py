# -*- coding: utf-8 -*-
"""locator_map の欠落補完・ピン照合の固定テスト(DXF不要の純ロジック)。"""
from . import locator_map as LM


def test_resolve_bijection_unique_and_elimination():
    # 4-26020-14 実データ由来: D/E/F = TX101/CX101/FX1。
    # FX1 はシーケンスの端子が不完全({13,14}のみ)でピンだけでは確証できない。
    letter_pins = {
        'D': {'1', '9', '13', '14'},
        'E': {'5', '9', '13', '14'},
        'F': {'5', '9', '13', '14'},
    }
    candidates = {
        'TX101': {'1', '9', '13', '14'},
        'CX101': {'5', '9', '13', '14'},
        'FX1': {'13', '14'},          # 不完全
    }
    r = LM.resolve_by_terminals(letter_pins, candidates)
    assert r['D'] == ('TX101', '確定'), r['D']       # ピン1で一意
    assert r['E'] == ('CX101', '確定'), r['E']       # 一意
    assert r['F'] == ('FX1', '要確認'), r['F']        # 消去法(ピン未確証)=要確認
    # 全単射: 同じ機器を2文字に割り当てない
    syms = [s for s, _ in r.values() if s]
    assert len(syms) == len(set(syms)), r


def test_resolve_insufficient_evidence_is_unconfirmed():
    # どの候補にも一致しない → None/要確認(でっち上げない=◎誤答ゼロ)
    r = LM.resolve_by_terminals({'X': {'99'}}, {'A1': {'1', '2'}})
    assert r['X'][1] == '要確認'


def test_next_letters_skips_used():
    assert LM._next_letters({'A', 'B', 'C'}, 3) == ['D', 'E', 'F']
    assert LM._next_letters(set(), 2) == ['A', 'B']


if __name__ == '__main__':
    test_resolve_bijection_unique_and_elimination()
    test_resolve_insufficient_evidence_is_unconfirmed()
    test_next_letters_skips_used()
    print('locator tests: OK')
