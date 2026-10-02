# -*- coding: utf-8 -*-
"""図面から機器(デバイス名)を『盤毎』に抽出する。

盤の単位＝図面タイトル枠(FRAMEブロック)の TITLE1(盤記号, 例 'M-2A')。
同一盤の各シート(シーケンス/スケルトン/外形図…)は同じ TITLE1 を持つため、
TITLE1 でグルーピングすれば盤毎の機器一覧になる。TITLE1 が無ければ SEIBAN、
それも無ければファイル名で代替する。
"""
import os
import csv as _csv

import ezdxf

from .geometry import DrawingModel
from . import defect_check as _dc


def frame_attrs(doc):
    """タイトル枠(FRAME)ブロックの属性 dict。無ければ空。"""
    for e in doc.modelspace():
        if e.dxftype() == 'INSERT' and e.attribs and 'FRAME' in e.dxf.name.upper():
            return {x.dxf.tag: (x.dxf.text or '').strip() for x in e.attribs}
    return {}


def panel_key(doc, path=''):
    """盤記号(TITLE1)→SEIBAN→ファイル名 の順で盤キーを決める。"""
    fr = frame_attrs(doc)
    return (fr.get('TITLE1') or fr.get('SEIBAN') or os.path.basename(path) or '(不明)',
            fr.get('SEIBAN', ''), fr.get('TITLE2', '') or os.path.basename(path))


def devices_by_panel(paths):
    """DXF群 → 盤毎の機器一覧。
    戻り: [{'panel','seiban','sheets':[...],'devices':[{'sym','parts','count','sheets'}]}]"""
    panels = {}
    for p in paths:
        try:
            doc = ezdxf.readfile(p)
            m = DrawingModel(p)
        except Exception:
            continue
        ban, seiban, sheet = panel_key(doc, p)
        pe = panels.setdefault(ban, {'seiban': set(), 'sheets': set(), 'devices': {}})
        if seiban:
            pe['seiban'].add(seiban)
        pe['sheets'].add(sheet)
        for sym, dev, dev1, parts in _dc._dev_records(m):
            if not sym:
                continue
            d = pe['devices'].setdefault(sym, {'parts': set(), 'count': 0, 'sheets': set()})
            if parts:
                d['parts'].add(parts)
            d['count'] += 1
            d['sheets'].add(sheet)
    out = []
    for ban, pe in sorted(panels.items()):
        devs = [{'sym': sym, 'parts': '/'.join(sorted(d['parts'])),
                 'count': d['count'], 'sheets': '/'.join(sorted(d['sheets']))}
                for sym, d in sorted(pe['devices'].items())]
        out.append({'panel': ban, 'seiban': '/'.join(sorted(pe['seiban'])),
                    'sheets': sorted(pe['sheets']), 'devices': devs})
    return out


def to_csv(panels, path):
    """盤毎機器一覧を1枚のCSVに書き出す(盤記号/製番/機器記号/種別/出現数)。"""
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = _csv.writer(f)
        w.writerow(['盤記号', '製番', '機器記号', '種別(PARTS)', '出現数', '出現シート'])
        for pnl in panels:
            for d in pnl['devices']:
                w.writerow([pnl['panel'], pnl['seiban'], d['sym'], d['parts'], d['count'], d['sheets']])
    return path
