# -*- coding: utf-8 -*-
"""cross_learn(横展開学習)の固定テスト。seiban_config に依存せず configs を直接渡す。"""
from . import cross_learn as cl


def test_device_type():
    assert cl.device_type('52-101') == '52'
    assert cl.device_type('86X-101') == '86X'
    assert cl.device_type('FX-1') == 'FX'
    assert cl.device_type('CX-210') == 'CX'


def test_learned_pins_consistency():
    # 2製番で 52:a?=13 が一致 → 学習。1製番だけの FX:b?=11 は学習しない。
    configs = {
        'S1': {'pin_confirm': {'52-101:a?': '13', 'FX-1:b?': '11'}},
        'S2': {'pin_confirm': {'52-205:a?': '13'}},
    }
    lp = cl.learned_pins(min_seiban=2, configs=configs)
    assert lp.get(('52', 'a?')) == '13'        # 2製番一致→学習
    assert ('FX', 'b?') not in lp              # 1製番のみ→学習しない


def test_learned_pins_conflict_blocked():
    # 製番間で値が割れたら学習しない(◎誤答ゼロ)。
    configs = {
        'S1': {'pin_confirm': {'52-101:a?': '13'}},
        'S2': {'pin_confirm': {'52-205:a?': '53'}},
    }
    lp = cl.learned_pins(min_seiban=2, configs=configs)
    assert ('52', 'a?') not in lp


def test_suggest():
    configs = {
        'S1': {'pin_confirm': {'86X-101:a?': '13'}},
        'S2': {'pin_confirm': {'86X-201:a?': '13'}},
    }
    assert cl.suggest('86X-301', 'a?', min_seiban=2, configs=configs) == '13'
    assert cl.suggest('86X-301', 'b?', min_seiban=2, configs=configs) is None


if __name__ == '__main__':
    test_device_type(); test_learned_pins_consistency()
    test_learned_pins_conflict_blocked(); test_suggest()
    print('cross_learn tests: OK')
