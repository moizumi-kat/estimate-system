# -*- coding: utf-8 -*-
"""部品カテゴリ別の端子トポロジDB ローダ。

主回路機器は図面に端子ピン(TB属性)を持たないため、カテゴリ(PARTS)標準の端子配列で
補完する。ノード分割（直列素子で線側/負荷側を別ノードに分ける）に split_series を、
端子番号付与に names/names_3p を使う。型式固有の補助接点番号が要る場合のみ datasheet で
上書き（overrides）。
"""
import os
import json

_DB = None
_DBPATH = os.path.join(os.path.dirname(__file__), 'terminal_db.json')


def load():
    global _DB
    if _DB is None:
        with open(_DBPATH, encoding='utf-8') as f:
            _DB = json.load(f)
    return _DB


def category(parts):
    """PARTS文字列 → カテゴリ定義（無ければ None）。"""
    cats = load().get('categories', {})
    return cats.get((parts or '').strip())


def is_series(parts):
    """この部品カテゴリが導体を線側/負荷側で分断する直列素子か。"""
    c = category(parts)
    return bool(c and c.get('split_series'))


def terminals(parts):
    """カテゴリの端子定義リスト（role/side/names…）。未知なら []。"""
    c = category(parts)
    return c.get('terminals', []) if c else []


def known_categories():
    return set(load().get('categories', {}).keys())
