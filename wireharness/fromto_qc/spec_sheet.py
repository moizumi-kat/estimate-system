# -*- coding: utf-8 -*-
"""社内確認表(制・分)= 設計から受け取る『仕様・属性』資料の抽出。

図面(=From-To/結線)とは別レーンの入力。社内確認表は製番ごとの製作仕様チェックリストで、
From-To(どの端子とどの端子が繋がるか)は持たないが、以下を持つ:
  ・備考欄の自由記述(例:「24時間操業物件です」= 稼働条件の宣言)
  ・電線属性/端末処理の標準・要求差異(電線被覆色/相色別/盤内エコ電線/圧着端子/CH打ち 等)

稼働条件(24時間操業/稼働)は FS電線選定基準の負荷率=1.0 に対応する。負荷率は
『太物/細物(=ハーネス対象)の境界が電線サイズだけでは一意に決まらない回路』の確定に効く
(seiban_config.load_factor、茂泉様確認済の製番入力)。個々の制御線の From-To は変えない。
本モジュールは、従来は人が指定していたこの入力を社内確認表の備考から自動検出する。

◎誤答ゼロ:
  ・検出は図面内の『テキスト要素』のみを対象にする決定的処理。
  ・チェックの●印は視覚記号で、行×列の対応は幾何校正が要る(最初の実Z図面で校正)。
    校正前に●位置を推定して属性を確定しない(=誤答を出さない)。本モジュールは当面
    備考テキスト(稼働条件)の検出に限定し、属性行は『要確認』として行名のみ提示する。
  ・自動検出した負荷率は『候補(確度:中)』。人の確認で seiban_config に確定(確度:高)。
"""
import glob
import os
import re

import ezdxf

# 稼働条件(24時間操業/稼働)→ 負荷率1.0。備考欄の宣言文を検出する。
#   実例: 「9.24時間稼働条件有りです。」「1.24時間操業物件です。」
_RE_24H = re.compile(r'24\s*時間\s*(?:操業|稼働)')

# ハーネスに効く属性行(将来、●の行×列判定で標準/要求差異を読む対象)。参考として保持。
ATTR_ROWS = [
    '電線被覆色', '相色別', '盤内エコ電線仕様',
    '圧着端子及び色別キャップの付属', 'CHタイプ打ち', 'R-RY T/Uアドレス設定',
]


def find_sheets(seiban_dir):
    """社内確認表の図面(Z系: Dwg.No 000-Z0xx)パス一覧。無ければ空。"""
    pats = ['図面データ_*Z0*.DXF', '図面データ_*Z0*.dxf',
            '図面データ_*Z*.DXF', '図面データ_*Z*.dxf']
    out = []
    for pat in pats:
        out += glob.glob(os.path.join(seiban_dir, pat))
    return sorted(set(out))


def _texts(path):
    """DXF の全テキスト(TEXT/MTEXT/INSERT属性)を文字列リストで返す。読めなければ空。"""
    out = []
    try:
        doc = ezdxf.readfile(path)
    except Exception:
        return out
    msp = doc.modelspace()
    for e in msp.query('TEXT'):
        out.append(e.dxf.text or '')
    for e in msp.query('MTEXT'):
        try:
            out.append(e.text or '')
        except Exception:
            pass
    for ins in msp.query('INSERT'):
        for a in (ins.attribs or []):
            out.append((a.dxf.text or ''))
    return [t for t in out if t]


def scan_notes(texts):
    """テキスト群 → 検出フラグ dict。
    戻り: {'is_24h': bool}。24時間操業/稼働の宣言を備考から検出する。"""
    joined = '\n'.join(texts or [])
    return {'is_24h': bool(_RE_24H.search(joined))}


def detect_load_factor(texts):
    """テキスト群 → 社内確認表から読める負荷率の候補。
    24時間操業/稼働 → '1.0'、記載が読めなければ None(=製番入力に委ねる)。"""
    return '1.0' if scan_notes(texts)['is_24h'] else None


def extract(seiban_dir):
    """製番ディレクトリ → 社内確認表の抽出結果。
    戻り: {'sheets':[path...], 'is_24h':bool, 'load_factor':'1.0'|None}。
    社内確認表が無ければ sheets 空・is_24h False・load_factor None(=未判定)。"""
    sheets = find_sheets(seiban_dir)
    texts = []
    for p in sheets:
        texts += _texts(p)
    notes = scan_notes(texts)
    return {'sheets': sheets, 'is_24h': notes['is_24h'],
            'load_factor': '1.0' if notes['is_24h'] else None}


def suggest_load_factor(seiban_dir):
    """社内確認表から負荷率の候補を返す(確認UI向け)。
    戻り: {'load_factor':'1.0'|None, 'confidence':'確度:中'|None, 'basis':str}。
    ◎誤答ゼロ: 自動確定はしない。人の確認で seiban_config.set_load_factor → 確度:高。
    負荷率は太物/細物境界(=ハーネス対象回路)の確定に使い、制御線の From-To は変えない。"""
    ex = extract(seiban_dir)
    if ex['load_factor'] == '1.0':
        return {'load_factor': '1.0', 'confidence': '確度:中',
                'basis': '社内確認表の備考『24時間操業/稼働』→負荷率1.0(要確認)'}
    return {'load_factor': None, 'confidence': None,
            'basis': '社内確認表に稼働条件の記載なし(負荷率は製番入力)'}
