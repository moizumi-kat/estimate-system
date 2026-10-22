# -*- coding: utf-8 -*-
"""分電盤のコネクタ・ハーネスを『要結線リスト』として安全に出力する。

背景(2026-10-22 調査):
  分電盤では配線本数の 7〜8 割がコネクタ(ｱｲﾃﾞｯｸ UI_BN30W 等)経由だが、これは
  トレース(図面に描かれた線分)では復元できない。実データで次を確認した。
    ・コネクタ↔ブレーカの配線は H系図面に線分として描かれていない(ブレーカ端子近傍の
      線分=0本)。製造側がハーネスで配線する『描かれない配線』。
    ・④(人のFrom-To)でも相手ブレーカは番号を持たず「MCCB:ET」等とだけ記録される
      (端子台の台番号と同じく、製造/配置で決まり図面に無い)。
    ・コネクタは機器列と別の帯に整列し、全コネクタが最近傍ブレーカから等距離=空間的にも
      相手を一意に特定できない。DXFのDEVICE1(115等)と④のピン記号(◎1等)も対応不明。
  したがって『コネクタピン→特定ブレーカ』の From-To 自動断定は誤答になる(◎誤答ゼロに反する)。

安全に出せるもの(本モジュール):
  (1) コネクタ端点の忠実な列挙 … DXFのコネクタINSERT(DEVICE1, TERMINAL)から各ピンを展開。
      これは図面に実在し検証可能。2人目(端子付与)のコネクタ網羅に使える。
  (2) 接地束テンプレート … 『緑(E)コネクタピン → ブレーカ:ET』は④で 70/70=100% 純粋。
      相手ブレーカ番号は不定(要割付)だが、接続の型は確実。確認ループ/横展開の土台にする。

これらは採点対象の From-To 行(fromto_data の 'rows')には混ぜない(ラベル流儀が④と異なり
見かけ上の精度を下げるため)。別建ての worklist として人の補完・確認に供する。
"""
import glob
import os
import re

import ezdxf


_H_PATTERNS = (
    '図面データ_*H[0-9]*.DXF', '図面データ_*H[0-9]*.dxf',
)


def _h_paths(seiban_dir):
    """配線図(H系)図面のパス一覧。分電盤の配線はH系に載る。"""
    out = []
    for pat in _H_PATTERNS:
        out += glob.glob(os.path.join(seiban_dir, pat))
    return sorted(set(out))


def _phases(terminal):
    """TERMINAL属性 'U,W,E' → ['U','W','E']。空なら空リスト。"""
    return [t.strip() for t in str(terminal or '').split(',') if t.strip()]


def enumerate_connectors(seiban_dir):
    """コネクタINSERTを列挙。戻り: [{'番号':DEVICE1,'相':[...],'maker':...,'code':...}]。
    図面に実在するコネクタとその宣言ピン(TERMINAL)をそのまま返す(検証可能な一次情報)。"""
    conns = []
    seen = set()
    for p in _h_paths(seiban_dir):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            dev = a.get('DEVICE', '')
            if 'ｺﾈｸﾀ' not in dev and 'コネクタ' not in dev:
                continue
            num = a.get('DEVICE1', '')
            x = round(ins.dxf.insert.x, 1)
            y = round(ins.dxf.insert.y, 1)
            key = (num, x, y)
            if key in seen:                       # 同一コネクタの重複INSERTを除く
                continue
            seen.add(key)
            conns.append({
                '番号': num,
                '相': _phases(a.get('TERMINAL', '')),
                'maker': a.get('MAKER', ''),
                'code': a.get('CODE', ''),
                'spec': a.get('SPEC5', ''),
                'pos': (x, y),
            })
    return conns


def pin_endpoints(seiban_dir):
    """コネクタの全ピンを端点として展開。戻り: [{'機器':'ｺﾈｸﾀ','番号':DEVICE1,'端子':相}]。
    2人目のコネクタ端子網羅・レビュー用。相手側は本関数では付けない(要割付)。"""
    eps = []
    for c in enumerate_connectors(seiban_dir):
        for ph in c['相']:
            eps.append({'機器': 'ｺﾈｸﾀ', '番号': c['番号'], '端子': ph})
    return eps


def earth_bundle_candidates(seiban_dir):
    """接地束テンプレート: 各コネクタのE相ピン → ブレーカ:ET。
    戻り: [{'from':{機器,番号,端子}, 'to':{機器,番号,端子}, '確度', 'sources'}]。
    相手ブレーカ番号は図面/④に無いため '要割付'。接続の型(E→ブレーカ:ET)は④で100%検証済。
    ◎誤答ゼロ: 型は確実・番号は未確定なので 確度:精査(人が割付/確認)。"""
    rows = []
    for c in enumerate_connectors(seiban_dir):
        if 'E' not in c['相']:
            continue
        rows.append({
            'from': {'機器': 'ｺﾈｸﾀ', '番号': c['番号'], '端子': 'E'},
            'to': {'機器': 'ブレーカ', '番号': '要割付', '端子': 'ET'},
            '確度': '確度:精査',
            'sources': ['接地束'],
        })
    return rows


def build_worklist(seiban_dir):
    """コネクタ・ハーネスの要結線リストをまとめて返す。
    戻り: {'connectors':[...], 'pins':[...], 'earth_bundle':[...], 'summary':{...}}。"""
    conns = enumerate_connectors(seiban_dir)
    pins = pin_endpoints(seiban_dir)
    earth = earth_bundle_candidates(seiban_dir)
    return {
        'connectors': conns,
        'pins': pins,
        'earth_bundle': earth,
        'summary': {
            'コネクタ数': len(conns),
            'ピン端点数': len(pins),
            '接地束候補': len(earth),
        },
    }
