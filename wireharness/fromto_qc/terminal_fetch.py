# -*- coding: utf-8 -*-
"""未知部品の端子情報を自動取得するフレーム（ネット取得＋キャッシュ）。

terminal_db(手動整備の確定データ) に無い多端子モジュールが図面に現れたら、
メーカー別の公式資料を検索して端子情報を取得し、terminal_cache.json に蓄積する。

構成:
  resolve(parts, type, maker)        … db → cache の順で端子定義を返す。無ければ None。
  queue_unknowns(master)             … 未知部品を検出し、取得キュー(query付き)を返す。
  build_queries(parts, type, maker)  … 検索クエリ＋メーカー公式サイトの候補URL。
  save_cache(type, entry) / load_cache()

ネット取得の実体（検索→PDF→図版読取）は環境のツール(WebSearch/WebFetch＋PDF描画)で
行い、確定したら save_cache() で確定値を保存する。カタログの結線図は画像のことが多く、
完全自動抽出は保証できないため、確度(confidence)を付けて保存し、必要なら人が確認する。
"""
import os
import json
from . import terminal_db as TDB
from . import parts_master as PM

_CACHE = os.path.join(os.path.dirname(__file__), 'terminal_cache.json')

# メーカー別の公式資料の探し方（検索を絞るためのサイト/キーワード）
MAKER_SOURCES = {
    '三菱': {'site': 'mitsubishielectric.co.jp/fa', 'kw': 'FA 仕様 結線図 端子'},
    'ﾊﾟﾅｿﾆｯｸ': {'site': 'panasonic.biz', 'kw': '施工説明書 端子 結線'},
    'パナソニック': {'site': 'panasonic.biz', 'kw': '施工説明書 端子 結線'},
    '富士': {'site': 'fujielectric.co.jp', 'kw': '取扱説明書 端子 結線'},
    '寺崎': {'site': 'terasaki.co.jp', 'kw': '端子 結線'},
    'ﾊｶﾙﾌﾟﾗｽ': {'site': 'hakaru.jp', 'kw': '取扱説明書 端子 結線図'},
    'azbil': {'site': 'azbil.com', 'kw': '仕様書 端子 結線'},
    'ｱｲﾃﾞｯｸ': {'site': 'idec.com', 'kw': '端子 結線'},
    'ｵﾑﾛﾝ': {'site': 'omron.co.jp', 'kw': '端子 結線'},
}


def load_cache():
    try:
        with open(_CACHE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(type_str, entry):
    """確定した端子情報をキャッシュに保存。entry例:
       {'parts','maker','terminals':[...],'source','confidence':'high'|'low'}"""
    c = load_cache()
    c[type_str.strip().upper()] = entry
    with open(_CACHE, 'w', encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    return entry


def resolve(parts, type_str=None, maker=None):
    """端子定義を db → cache の順で返す。無ければ None。"""
    d = TDB.resolve(parts, type_str)
    if d:
        return d
    if type_str:
        c = load_cache().get(type_str.strip().upper())
        if c:
            return c
    return None


def build_queries(parts, type_str, maker):
    """メーカー別の検索クエリと公式サイト絞り込みを返す。"""
    src = MAKER_SOURCES.get((maker or '').strip(), {})
    kw = src.get('kw', '端子 結線図 端子番号')
    q = f"{maker} {type_str} {kw}".strip()
    return {'query': q, 'site': src.get('site', ''),
            'parts': parts, 'type': type_str, 'maker': maker}


# 型式固有の端子表が要る「多端子モジュール」カテゴリ。標準制御機器(PBS/AXR/COS/TM等,
# 号線・端子ピンで処理)や標準電力機器(terminal_dbカバー)は除外する。
MODULE_PARTS = {
    '電力監視機器', '電流センサ', 'WHM', 'ﾏﾙﾁﾒｰﾀ', 'マルチメーター', 'MCDT', 'INV',
    'T/U', '接点入力T/U', 'T/U付6Aﾘﾚｰﾕﾆｯﾄ', '伝送ﾕﾆｯﾄ', '年間ﾌﾟﾛｸﾞﾗﾑﾀｲﾏﾕﾆｯﾄ',
    '小形リモート', 'ﾘﾓｺﾝﾘﾚｰ', 'リモコンリレー', 'ﾘﾓｺﾝﾄﾗﾝｽ', 'リモコントランス',
    '信号線雷ｻｰｼﾞ防護ﾕﾆｯﾄ', '信号線雷サージ防護ユニット', 'DCR', 'LNF',
}


def queue_unknowns(master, module_only=True):
    """部品マスタから、端子情報が未解決の部品を取得キューとして返す（使用数の多い順）。
    module_only=True なら多端子モジュール(MODULE_PARTS)に限定（標準制御/電力機器は除外）。"""
    q = []
    for r in sorted(master, key=lambda r: -r.get('count', 0)):
        if module_only and r['parts'] not in MODULE_PARTS:
            continue
        if not module_only and PM.terminal_status(r) != 'need_lookup':
            continue
        if resolve(r['parts'], r['type'], r['maker']):
            continue
        q.append({**build_queries(r['parts'], r['type'], r['maker']), 'count': r['count']})
    return q
