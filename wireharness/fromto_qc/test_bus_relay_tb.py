# -*- coding: utf-8 -*-
"""_correct_bus_relay_tb: 集約端子台バスの中継TB台番号補正(5-12043 の 237 母線)。"""
from . import harness_produce as HP


class _FakeLayout:
    """Layout の最小スタブ(devices 辞書のみ)。"""
    def __init__(self, devices):
        self.devices = devices


def _wire(g, fd, fn, td, tn):
    return {'gousen': g,
            'from': {'device': fd, 'no': fn, 'terminal': ''},
            'to': {'device': td, 'no': tn, 'terminal': ''}}


def _setup():
    # 盤5の遮断器(x~3175) と 盤6の遮断器 52-601(x3860)、盤6の共通リレーTB TB63/TB64、
    # TB63/64 右脇の共通リレー 30X。電源TB TB62 は 30X が近くに無い=補正対象外。
    devices = {
        'TB62': (3620, 2528), 'TB63': (3620, 2030), 'TB64': (3620, 1580),
        '30X101': (3830, 2075),   # TB63 近傍(<=260) → TB63=共通リレーTB
        '30X503': (3833, 1675),   # TB64 近傍 → TB64=共通リレーTB
        '52503': (3175, 2300), '52504': (3175, 2320), '52601': (3860, 2650),
    }
    dev_face = {('52', '503'): '5', ('52', '504'): '5', ('52', '601'): '6'}
    return [_FakeLayout(devices)], dev_face


def test_bus_relay_tb_corrected_to_master_panel_block():
    lays, dev_face = _setup()
    wires = [_wire('237A', 'TB', '55', '52', '601'),   # 盤6到達(マスター)
             _wire('237B', 'TB', '55', '52', '503'),   # 盤5(幾何最近傍だと盤5TBに誤る)
             _wire('237C', 'TB', '56', '52', '504')]
    HP._correct_bus_relay_tb(wires, lays, dev_face)
    tbs = {w['from']['no'] for w in wires}        # TB は全て from 側
    # マスター盤(盤6)の共通リレーTBのうち 52-601 に最も近い TB63 に統一される
    assert tbs == {'63'}, tbs


def test_bus_relay_tb_untouched_when_not_a_bus():
    lays, dev_face = _setup()
    # メンバ2件のみ(<3)＝集約バス不成立 → 台番号は不変
    wires = [_wire('900A', 'TB', '55', '52', '503'),
             _wire('900B', 'TB', '56', '52', '504')]
    HP._correct_bus_relay_tb(wires, lays, dev_face)
    assert {w['from']['no'] for w in wires} == {'55', '56'}


def test_bus_relay_tb_untouched_when_single_panel():
    lays, dev_face = _setup()
    # 3件だが全て盤5(非TB面が1盤)＝集約バス不成立 → 不変
    wires = [_wire('901A', 'TB', '55', '52', '503'),
             _wire('901B', 'TB', '56', '52', '504'),
             _wire('901C', 'TB', '52', '52', '503')]
    HP._correct_bus_relay_tb(wires, lays, dev_face)
    assert {w['from']['no'] for w in wires} == {'55', '56', '52'}
