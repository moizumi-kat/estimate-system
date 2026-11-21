# -*- coding: utf-8 -*-
"""review_lamptest_bus: ランプテスト母線(LTn)が接点記法で共通RCnに併合し盤をまたぐ図面不備アラーム。

実データ裏取り(117/5-12043)に対応する決定論ルールの単体検証:
  ・3-LTn を含む号線が独立 LTn を持たず、かつ 2面以上に跨る → アラーム1件。
  ・独立 LTn が生成されていれば(分離OK) → 発火しない。
  ・盤跨ぎが共通参照リレー(30X等)だけに由来する場合 → 発火しない(117 LT3 の過検出防止)。
  ・単一面に収まる(5-12043 の無害併合相当) → 発火しない(回帰ゼロ)。
"""
from . import design_check as DC


def _w(g, fd, fn, td, tn):
    return {'gousen': g,
            'from': {'device': fd, 'no': fn, 'terminal': ''},
            'to': {'device': td, 'no': tn, 'terminal': ''}}


def test_lamptest_cross_panel_fires():
    # 117 LT2 相当: RC2 に 3-LT2(面2) と 86X-201(面1=ランプ relay) が併合=盤またぎ。
    wires = [_w('RC2', '86X', '201', '3', 'LT2'),
             _w('RC2', '86X', '201', 'MCCB', '202')]
    dev_face = {('86X', '201'): '1', ('3', 'LT2'): '2', ('MCCB', '202'): '2'}
    out = DC.review_lamptest_bus(wires, dev_face)
    assert len(out) == 1, out
    assert 'LT2' in out[0]['分類'] and out[0]['号線'] == 'RC2'


def test_lamptest_single_panel_silent():
    # 無害併合(5-12043 相当): LT4 の全メンバーが同一面 → 発火しない。
    wires = [_w('RC4', '86X', '401', '3', 'LT4'),
             _w('RC4', '86X', '401', 'MCCB', '402')]
    dev_face = {('86X', '401'): '3', ('3', 'LT4'): '3', ('MCCB', '402'): '3'}
    assert DC.review_lamptest_bus(wires, dev_face) == []


def test_lamptest_common_ref_only_silent():
    # 117 LT3 相当: 盤跨ぎが共通参照リレー 30X-2A(面2)だけに由来 → 発火しない(過検出防止)。
    wires = [_w('RC3', '86X', '301', '3', 'LT3'),
             _w('RC3', '86X', '301', '30X', '2A')]
    dev_face = {('86X', '301'): '3', ('3', 'LT3'): '3', ('30X', '2A'): '2'}
    assert DC.review_lamptest_bus(wires, dev_face) == []


def test_lamptest_separated_bus_silent():
    # 独立母線 LT5 が生成済み(分離OK) → 発火しない。
    wires = [_w('LT5', '86X', '501', '3', 'LT5'),
             _w('RC5', '86X', '501', '3', 'LT5')]
    dev_face = {('86X', '501'): '1', ('3', 'LT5'): '2'}
    assert DC.review_lamptest_bus(wires, dev_face) == []


def test_lamptest_no_wires_silent():
    assert DC.review_lamptest_bus([], {}) == []
    assert DC.review_lamptest_bus(None, None) == []
