# -*- coding: utf-8 -*-
"""spec_sheet(社内確認表の抽出)の固定テスト。DXF非依存部(備考テキスト検出)を中心に。"""
import os
import tempfile

from . import spec_sheet as ss


def test_scan_notes_24h():
    # 実資料の実例(3製番の備考)を検出できること。
    assert ss.scan_notes(['9.24時間稼働条件有りです。'])['is_24h']      # 4-21088
    assert ss.scan_notes(['1.24時間操業物件です。'])['is_24h']          # 3-21052 / 1-21232
    assert ss.scan_notes(['24 時間 操業'])['is_24h']                    # 空白ゆれ
    # 無関係な備考では立たない(誤検出しない)。
    assert not ss.scan_notes(['天井面・底面塞ぎ板用の塩ビ板はt5になります。'])['is_24h']
    assert not ss.scan_notes([])['is_24h']


def test_detect_load_factor():
    assert ss.detect_load_factor(['1.24時間操業物件です。']) == '1.0'
    assert ss.detect_load_factor(['相色別は特殊']) is None


def test_extract_missing_sheet():
    # 社内確認表の無いディレクトリ → 未判定(誤って負荷率を出さない)。
    d = tempfile.mkdtemp()
    ex = ss.extract(d)
    assert ex['sheets'] == []
    assert ex['is_24h'] is False
    assert ex['load_factor'] is None


def test_suggest_load_factor_missing():
    d = tempfile.mkdtemp()
    s = ss.suggest_load_factor(d)
    assert s['load_factor'] is None and s['confidence'] is None


if __name__ == '__main__':
    test_scan_notes_24h(); test_detect_load_factor()
    test_extract_missing_sheet(); test_suggest_load_factor_missing()
    print('spec_sheet tests: OK')
