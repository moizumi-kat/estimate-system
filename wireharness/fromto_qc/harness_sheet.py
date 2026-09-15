# -*- coding: utf-8 -*-
"""ハーネス「シート」データ（製造・物理層）の構造化パーサ。

ハーネスシート＝製造工程が使う最終成果物。From-To に製造情報
（電線種別・ダクト方向・LUG/WAGO/中継/渡り・扉）が乗る。
本モジュールは、そのシートを行単位で構造化し、製造マーカーを分類する。
用途: (1) 生成した製造ハーネスの検証基準、(2) 製造ルール学習の教材。

列（tab区切り, cp932, 先頭に空列あり）:
  1=号線/線色  2=号線接尾  3=サイズ/種別  4=方向/位置  5=機器記号  6=機器番号  7=端子(+方向)

製造マーカー（実データで確認）:
  LUG           … 盤間・主回路(電源)  … 直後行の遮断器(MCCB/ELCB)へ大銅端子で接続
  WAGO          … 盤間・制御          … コネクタ/WAGOを介する制御線
  つなぎ/つなぎ小 … 渡り(ジャンパ)
  中継/中欠用    … 中継端子台
  扉            … 扉付け機器(操作器・表示灯)
  上/下/左/右/外  … ダクト方向（内部配置図の機器⇔ダクト位置で決まる物理情報）
"""
import re

WIRE_TYPES = {'KIV', 'HIV', 'IV', 'TGV', 'エコ', 'ｴｺ', 'SPEV', 'CVV', 'VCT'}
DIRECTIONS = ('上', '下', '左', '右', '外')


def _cell(c, i):
    return c[i].strip() if len(c) > i else ''


def parse(path, encoding='cp932'):
    """ハーネスシート → 構造化レコードのリスト。
    各レコード: dict(gousen, size, wtype, direction, device, no, terminal, marker, term_dir)
      marker ∈ {'LUG','WAGO','渡り','中継','', ...}
      term_dir … 端子に付く方向（例 '1R 上' の '上'）
    グループ見出し行（* * 種別 サイズ …）は cur_type/cur_size に反映して読み継ぐ。
    """
    raw = open(path, 'rb').read().decode(encoding, errors='replace')
    out = []
    cur_type = cur_size = ''
    for l in raw.splitlines():
        c = l.split('\t')
        col1 = _cell(c, 1)
        col3 = _cell(c, 3)   # サイズ/種別
        col4 = _cell(c, 4)   # 方向/位置
        dev = _cell(c, 5)
        no = _cell(c, 6)
        term = _cell(c, 7)
        # グループ見出し: col1=='*' and col5=='*'（種別・サイズ・色の宣言）
        if col1 == '*' and _cell(c, 5) == '*':
            t = col3
            s = col4
            if t and t != '*':
                cur_type = t
            if s and s != '*':
                cur_size = s
            continue
        if not (dev and dev != '*'):
            continue
        # マーカー分類
        marker = ''
        up = (col3 + ' ' + col4).upper()
        if dev == 'LUG':
            marker = 'LUG'
        elif 'WAGO' in up or 'ﾜｺﾞ' in (col3 + col4) or 'ワゴ' in (col3 + col4):
            marker = 'WAGO'
        elif 'つなぎ' in col3:
            marker = '渡り'
        if '中継' in no or '中欠' in no:
            marker = (marker + '+中継') if marker else '中継'
        # サイズと電線種別
        size = ''
        wtype = cur_type
        if re.match(r'^[\d.]+$', col3):
            size = col3
        elif col3 in WIRE_TYPES:
            wtype = col3
        else:
            size = cur_size
        # 端子/位置の方向
        term_dir = ''
        for d in DIRECTIONS:
            if d in col4 or d in term:
                term_dir = d
                break
        door = (col4 == '扉')
        out.append({'gousen': col1, 'size': size or cur_size, 'wtype': wtype,
                    'device': dev, 'no': no, 'terminal': re.sub(r'[上下左右外\s]', '', term),
                    'marker': marker, 'term_dir': term_dir, 'door': door})
    return out


def summary(records):
    """製造マーカーの集計。"""
    import collections
    c = collections.Counter(r['marker'] for r in records if r['marker'])
    dirs = collections.Counter(r['term_dir'] for r in records if r['term_dir'])
    doors = sum(1 for r in records if r['door'])
    return {'rows': len(records), 'markers': dict(c),
            'directions': dict(dirs), 'door': doors}
