# -*- coding: utf-8 -*-
"""母線号線ラベルの照合 _bus_label の固定テスト（DXF非依存）。
F001 実データの配置を再現: 号線ラベルは母線レールの端（リードイン）に置かれ、
y=3750 の RC1 と y=3700 の 101R が 50 だけ離れて重なる。
クランプ距離では端から150手前の 101R ラベルを取りこぼし、perp+lead で拾えること、
かつ 50 離れた重なり母線を取り違えないことを保証する。"""
from .tracer import _bus_label

# F001 の H_SENBAN ラベル（号線, INSERT名, x, y）
SB = [
    ('RC1', 'H_SENBAN', 150, 3750),    # 長レール(y=3750) の左端手前
    ('101R', 'H_SENBAN', 200, 3700),   # 短レール(y=3700, x350-2200) の 150 手前
    ('102R', 'H_SENBAN', 2550, 3700),  # 短レール(y=3700, x2600-4300) の 50 手前
    ('LT1', 'H_SENBAN', 550, 3800),    # 別レール(L_CONTROL y=3800)
]

RC1_RAIL = ((100, 3750), (5500, 3750))     # 長い母線
R101_RAIL = ((350, 3700), (2200, 3700))    # 101R 短レール（左）
R102_RAIL = ((2600, 3700), (4300, 3700))   # 102R 短レール（右）


def test_lead_in_label_matched():
    # ラベルがレール始点の150手前にあっても拾う（旧クランプ距離60では落ちていた）。
    assert _bus_label(*R101_RAIL, SB, 'H_SENBAN') == '101R'
    assert _bus_label(*R102_RAIL, SB, 'H_SENBAN') == '102R'


def test_stacked_rails_not_confused():
    # y=3750(RC1) と y=3700(101R) は 50 離れ。perp_tol=40 で層を厳密に分離する。
    assert _bus_label(*RC1_RAIL, SB, 'H_SENBAN') == 'RC1'        # 101R(50下)を拾わない
    assert _bus_label(*R101_RAIL, SB, 'H_SENBAN') == '101R'      # RC1(50上)を拾わない


def test_left_rail_excludes_right_label():
    # 左レール(x350-2200)は右回路 102R ラベル(x2550)を span 外として拾わない。
    assert _bus_label(*R101_RAIL, SB, 'H_SENBAN') != '102R'


def test_orientation_filter():
    # V レールには H_SENBAN を当てない（want 不一致は無視）。
    v_rail = ((100, 600), (100, 3000))
    assert _bus_label(*v_rail, SB, 'V_SENBAN') is None   # SB は H_SENBAN のみ


if __name__ == '__main__':
    test_lead_in_label_matched()
    test_stacked_rails_not_confused()
    test_left_rail_excludes_right_label()
    test_orientation_filter()
    print('bus_label tests: OK')
