# -*- coding: utf-8 -*-
"""connector_crossover のロジック固定テスト(DXF非依存・合成データ)。

実機の手本/図面(社内資産)は同梱しないため、ここでは分類(classify)・渡り検出
(detect_crossings)・生成(generate)の決定的ロジックを合成 wire で検証する。
device→面 の図面抽出(build_device_face)は DXF 依存のため別途実機で検証済み。
"""
from . import connector_crossover as CC


def _wire(g, fd, fn, ft, td, tn, tt):
    return {'gousen': g,
            'from': {'device': fd, 'no': fn, 'terminal': ft},
            'to': {'device': td, 'no': tn, 'terminal': tt}}


def test_classify_power_by_mccb_main_terminal():
    # MCCB の主端子(1,3,5/相)を使う渡り = 太物(power)
    wires = [_wire('1G2', 'MCCB', '1G2', '1', 'MCCB', '2G2', '3')]
    assert CC.classify('1G2', wires) == 'power'
    wires2 = [_wire('2U', 'MCCB', 'X', 'U', 'TB', '1', '')]
    assert CC.classify('2U', wires2) == 'power'


def test_classify_control_by_mccb_alarm_terminal():
    # MCCB の警報/補助接点(ALc/ALa)のみ = 制御(control)
    wires = [_wire('KA', 'MCCB', '1A', 'ALc', 'TB', '2', ''),
             _wire('KA', 'MCCB', '1G', 'ALc', '30X', '1', '6')]
    assert CC.classify('KA', wires) == 'control'


def test_classify_control_without_mccb():
    # MCCB を含まない制御号線
    wires = [_wire('104', 'OL', '12', '', 'DTAL', '1', '2')]
    assert CC.classify('104', wires) == 'control'


def test_detect_crossings_spans_two_faces():
    # 号線が2面に跨れば検出、1面のみなら非検出
    dev_face = {('OL', '12'): '1', ('DTAL', '1'): '2', ('OL', '22'): '1'}
    wires = [_wire('104', 'OL', '12', '', 'DTAL', '1', '2'),   # 面1→面2 跨ぎ
             _wire('900', 'OL', '12', '', 'OL', '22', '')]      # 面1のみ
    cross = CC.detect_crossings(wires, dev_face)
    assert '104' in cross and cross['104'] == {'1', '2'}
    assert '900' not in cross


def test_detect_concat_name_fallback():
    # app の (DTAL,1) が DCT の DEVICE 'DTAL1' と連結名で一致
    dev_face = {('DTAL1', '2-I'): '2', ('OL', '12'): '1'}
    wires = [_wire('104', 'OL', '12', '', 'DTAL', '1', '2')]
    cross = CC.detect_crossings(wires, dev_face)
    assert cross.get('104') == {'1', '2'}


def test_generate_empty_without_faces():
    # device→面 が作れない(dct_paths=None)なら空=回帰ゼロ
    routed = {'wires': [_wire('104', 'OL', '12', '', 'DTAL', '1', '2')]}
    out = CC.generate(routed, None)
    assert out == {'control': [], 'power': [], 'faces': 0}


def test_color_gousen_ignored():
    # 色トークン(白/赤 等)は号線でないので渡り判定に使わない
    dev_face = {('A', '1'): '1', ('B', '2'): '2'}
    wires = [_wire('白', 'A', '1', '', 'B', '2', '')]
    assert CC.detect_crossings(wires, dev_face) == {}
