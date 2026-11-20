# -*- coding: utf-8 -*-
"""panel_layout の純ロジック(DXF非依存)テスト。"""
from . import panel_layout as PL


def test_kmeans1d_splits_clusters():
    # 3つの塊 → 中心3つが各塊付近に来る
    xs = [100, 110, 120, 500, 510, 520, 900, 910, 920]
    cs = PL._kmeans1d(xs, 3)
    assert len(cs) == 3
    assert abs(cs[0] - 110) < 30 and abs(cs[1] - 510) < 30 and abs(cs[2] - 910) < 30


def test_kmeans1d_fewer_unique_than_n():
    # ユニーク点が N 未満ならその点を返す
    cs = PL._kmeans1d([5, 5, 9], 6)
    assert cs == [5, 9]


def test_norm():
    assert PL._norm(' mccb ') == 'MCCB'
    assert PL._norm(None) == ''


def test_layout_paths_excludes_dct():
    # -DCT は除外、平の -D\\d は配置図として採用
    paths = ['x/A-D001-DCT.dxf', 'x/A-D001.DXF', 'x/A-D002.dxf', 'x/A-E001.dxf', 'x/A-F001.dxf']
    got = PL._layout_paths(paths)
    assert 'x/A-D001.DXF' in got and 'x/A-D002.dxf' in got
    assert 'x/A-D001-DCT.dxf' not in got        # DCT 除外
    assert 'x/A-E001.dxf' not in got            # スケルトンは対象外
