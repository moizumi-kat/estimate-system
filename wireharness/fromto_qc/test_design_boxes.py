# -*- coding: utf-8 -*-
"""設計内部配置図の盤ブロック(InnerBox/DoorBox)による面の境界把握(茂泉様指示)。

「DCTでなく設計からの内部配置図で盤境界を把握」の実装検証。盤は MCILU_*InnerBox{N}/
DoorBox{N} ブロックで定義され、挿入x＋等幅で面のx域が決まる。
"""
from . import panel_layout as PL


class _FakeDxf:
    def __init__(self, name, x):
        self.name = name
        self.insert = (x, 1250.0)


class _FakeAttrDxf:
    def __init__(self, tag, text):
        self.tag = tag
        self.text = text


class _FakeAttr:
    def __init__(self, tag, text):
        self.dxf = _FakeAttrDxf(tag, text)


class _FakeInsert:
    def __init__(self, name, x, device=None, device1=''):
        self.dxf = _FakeDxf(name, x)
        self.attribs = ([_FakeAttr('DEVICE', device), _FakeAttr('DEVICE1', device1)]
                        if device else [])


class _FakeMsp:
    def __init__(self, inserts):
        self._ins = inserts

    def query(self, kind):
        return self._ins if 'INSERT' in str(kind).upper() else []


class _FakeDoc:
    def __init__(self, inserts):
        self._msp = _FakeMsp(inserts)

    def modelspace(self):
        return self._msp


def test_design_boxes_inner_and_door():
    doc = _FakeDoc([
        _FakeInsert('MCILU_JKN_InnerBox1_x', 400),
        _FakeInsert('MCILU_JKN_InnerBox2_x', 1000),
        _FakeInsert('MCILU_JKN_InnerBox3_x', 1600),
        _FakeInsert('MCILU_JKN_DoorBox1_x', 2600),
        _FakeInsert('MCILU_JKN_DoorBox2_x', 3200),
        _FakeInsert('MCILU_JKN_DoorBox3_x', 3800),
        _FakeInsert('MCILU_JKN_SideBox_x', 4850),
        _FakeInsert('OI_AR22F0R_K_a', 500),       # 機器ブロック=無視
    ])
    b = PL._design_boxes(doc)
    assert b is not None
    assert sorted(b['inner']) == [1, 2, 3]
    assert sorted(b['door']) == [1, 2, 3]
    # 盤幅=最小隣接間隔=600、InnerBox1 は x[400,1000]
    assert b['inner'][1] == (400, 1000)
    assert b['inner'][3] == (1600, 2200)
    assert b['door'][1] == (2600, 3200)


def test_design_boxes_none_when_absent():
    doc = _FakeDoc([_FakeInsert('OI_AR22F0R_K_a', 500),
                    _FakeInsert('X_RAIL_25W', 700)])
    assert PL._design_boxes(doc) is None


def test_panel_count_uses_max_box_number():
    # 6面(InnerBox1-6相当)を返すこと
    doc = _FakeDoc([_FakeInsert(f'MCILU_JKN_InnerBox{i}_x', 400 + 600 * (i - 1))
                    for i in range(1, 7)])
    b = PL._design_boxes(doc)
    nums = list(b['inner']) + list(b['door'])
    assert max(nums) == 6
