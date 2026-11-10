# -*- coding: utf-8 -*-
"""銘板/属性ブロック(端子座標なしの実機器)を命名規則で既存号線へ付与する
_legend_target_gousen / _attach_legend_devices の固定テスト。

対象の実機器（手本で全製番一致を確認した規則）:
  CP{n}(制御電源・LZ_MEISHOU)   → {n}R,{n}S,F{n}R,F{n}S
  CP-RM(CPRM)                   → RMR,RMR1,RMS,RMS1
  DI/TOT{p}(小形リモートI/O・LZ_ZOKUSEI) → {p}DI<n>(+含む), RMR1,RMS1
◎誤答ゼロ: 既存号線にのみ付与し、新規号線は作らない（node_keys に無い号線には付けない）。
"""
from .tracer import _legend_target_gousen


def test_cp_numeric_only_existing_nodes():
    # 既存号線が全部ある場合 → 4号線すべて
    keys = {'305R', '305S', 'F305R', 'F305S', '30552'}
    assert _legend_target_gousen('CP', '305', keys) == {'305R', '305S', 'F305R', 'F305S'}
    # 存在しない号線には付けない（新規号線を作らない）
    keys2 = {'305R', '305S'}
    assert _legend_target_gousen('CP', '305', keys2) == {'305R', '305S'}


def test_cp_rm():
    keys = {'RMR', 'RMR1', 'RMS', 'RMS1', 'X'}
    assert _legend_target_gousen('CP', 'RM', keys) == {'RMR', 'RMR1', 'RMS', 'RMS1'}


def test_ditot_channels_and_rm_rails():
    keys = {'1DI1', '1DI1+', '1DI2', '1DI7', '2DI1', 'RMR1', 'RMS1'}
    got = _legend_target_gousen('DI/TOT', '1', keys)
    # 盤1の DI チャンネル(+含む)とリモート母線のみ。盤2(2DI1)は付けない。
    assert got == {'1DI1', '1DI1+', '1DI2', '1DI7', 'RMR1', 'RMS1'}
    got2 = _legend_target_gousen('DI/TOT', '2', keys)
    assert got2 == {'2DI1', 'RMR1', 'RMS1'}


def test_unknown_device_returns_empty():
    # 規則対象外の凡例機器(フィンガーガード等)は一切付けない
    keys = {'305R', '305S', '1DI1'}
    assert _legend_target_gousen('フィンガーガード', '305', keys) == set()
    assert _legend_target_gousen('FAN用コード', '305', keys) == set()
    # CP でも数字でない DEVICE1(コネクタ参照など)は対象外
    assert _legend_target_gousen('CP', 'A1', keys) == set()


def test_no_fabrication_when_nodes_absent():
    # 付与先号線が一つも無ければ空（新規号線を作らない＝◎誤答ゼロ）
    assert _legend_target_gousen('CP', '999', {'101R', '102S'}) == set()
    assert _legend_target_gousen('DI/TOT', '9', {'101R'}) == set()
