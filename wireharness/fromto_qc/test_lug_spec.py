# -*- coding: utf-8 -*-
"""ラグバー基準(lug_spec)の固定テスト。値は資料 FS-5C-002 の表の転記を検証する。"""
from . import lug_spec as L


def test_select_bar():
    # 表2.1：容量以上の最小行
    assert L.select_bar(30) == '15×3'
    assert L.select_bar(75) == '15×3'       # 100A枠に入る
    assert L.select_bar(100) == '15×3'
    assert L.select_bar(150) == '20×6'      # 225A枠
    assert L.select_bar(225) == '20×6'
    assert L.select_bar(300) == '30×6'
    assert L.select_bar(400) == '30×8'
    assert L.select_bar(600) == '50×8'


def test_space_and_pitch():
    assert L.required_space(100) == (200, 200)
    assert L.required_space(300) == (250, 300)
    assert L.bar_pitch('15×3') == 60
    assert L.bar_pitch('30×8') == 80
    assert L.bar_pitch('50×8') == 100


def test_cover_L():
    # 表2.3
    assert L.cover_L(14) == 60
    assert L.cover_L(38) == 70
    assert L.cover_L(100) == 100
    assert L.cover_L(150) == 110
    assert L.cover_L(325) == 150


def test_screw():
    # 表3.1.1 (15×3)
    assert L.screw('15×3', 5, 'branch') == ('M5', 'M5ねじ')      # 2〜8sq
    assert L.screw('15×3', 38, 'main') == ('φ9', 'M8スタッド')
    assert L.screw('15×3', 38, 'branch') == ('M6', 'M6ねじ')
    # 表3.2.1 (20×6)
    assert L.screw('20×6', 5, 'main') == ('-', '-')              # 2〜8sqは主幹欄なし
    assert L.screw('20×6', 60, 'branch') == ('M8', 'M8ボルト')
    assert L.screw('20×6', 100, 'watari') == ('M8', 'M8スタッド')
    # 表3.4.1 (30×8)
    assert L.screw('30×8', 200, 'main') == ('M12', 'M12スタッド')


def test_hole_layout():
    # 穴数 = 主幹1 + 分岐n + 渡り(有1)
    lay = L.hole_layout('15×3', 5, watari=False)
    assert lay['holes'] == 1 + 5 + 0
    assert lay['min_L'] == 125
    lay2 = L.hole_layout('20×6', 8, watari=True)
    assert lay2['holes'] == 1 + 8 + 1
    assert lay2['every_other_60sq'] is True        # 注3.2.4
    assert lay2['watari_max_sq'] == 100
    lay3 = L.hole_layout('30×6', 10, watari=False)
    assert lay3['double_row'] is True
    assert lay3['min_L'] == 140


def test_spec_for():
    s = L.spec_for(amp=225, n_branch=12, watari=True, branch_sq=5)
    assert s['bar'] == '20×6'
    assert s['holes'] == 1 + 12 + 1
    assert s['分岐ねじ'] == ('M5', 'M5ねじ')
    assert s['bar_pitch_P'] == 70


if __name__ == '__main__':
    for n, f in sorted(globals().items()):
        if n.startswith('test_') and callable(f):
            f(); print(n, 'OK')
    print('lug_spec tests: ALL OK')
