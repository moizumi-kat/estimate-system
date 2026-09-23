# -*- coding: utf-8 -*-
"""図面(DXF)から部品マスタ（型式・メーカー・品番・GCODE・PARTS分類）を抽出する。

Furukawa の CAD ブロックは各機器に部品情報を属性として持つ。これを集約して
「どの部品が使われているか」の一覧を作り、terminal_db（カテゴリ別端子トポロジ）と
突き合わせて端子情報の充足/不足を判定する。
"""
import collections
import ezdxf
from . import terminal_db as TDB

_SKIP_PARTS = {'電線サイズ', '系統情報', '銘板', ''}
# 端子を持たない付属・機構部品（端子情報の対象外）
_ACCESSORY = {'CH', 'ﾊﾝﾄﾞﾙ', 'ハンドル', 'ﾛｯｸｶﾊﾞｰ', 'ﾌｨﾝｶﾞｰｶﾞｰﾄﾞ', '吊りﾎﾞﾙﾄ',
              '名称', '開閉ﾏｰｸ', '絶縁ﾊﾞﾘｱ', '計器窓', '検針窓', 'グロメット',
              'TB取付金具', '端子ｶﾊﾞｰ', 'ﾌﾞｯｼﾝｸﾞ', '低圧ｸﾘｰﾄ', '低圧クリート',
              'NP', 'ｷｬｯﾌﾟ', '充電部保護ｶﾊﾞｰ', 'ﾜｲﾄﾞﾊﾞｰ', 'ｻｲﾄﾞｵﾝ',
              'FAN用ｺｰﾄﾞ', '延長ケーブル', '圧着端子', 'LUG', 'Lug'}


def extract(paths):
    """複数DXF → 部品マスタ。戻り: [{parts,type,maker,code,gcode,devices,count}]（多い順）。"""
    parts = {}
    for p in paths:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for e in doc.modelspace():
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
            pt, ty = a.get('PARTS', ''), a.get('TYPE', '')
            if not ty or pt in _SKIP_PARTS:
                continue
            key = (pt, ty, a.get('MAKER', ''))
            r = parts.setdefault(key, {'parts': pt, 'type': ty, 'maker': a.get('MAKER', ''),
                                       'code': a.get('CODE', ''), 'gcode': a.get('GCODE', ''),
                                       'devices': set(), 'count': 0, 'has_pins': False})
            r['devices'].add(a.get('DEVICE', ''))
            r['count'] += 1
            if a.get('TB', '').strip():        # 図面に端子ピン(TB属性)を持つ＝端子座標が既知
                r['has_pins'] = True
    out = [{**r, 'devices': sorted(r['devices'])} for r in parts.values()]
    return sorted(out, key=lambda r: -r['count'])


def terminal_status(rec):
    """部品レコードの端子情報の状態を返す:
      'accessory'   … 端子を持たない付属・機構部品（対象外）
      'db'          … カテゴリ別端子トポロジDBで充足（主回路の直列素子等）
      'pins'        … 図面に端子ピン(TB属性)有り＝端子座標が既知（制御機器）
      'need_lookup' … いずれも無い多端子モジュール＝datasheet/ネット取得の候補
    """
    pt = rec['parts'] if isinstance(rec, dict) else rec
    if pt in _ACCESSORY:
        return 'accessory'
    if TDB.category(pt) is not None:
        return 'db'
    if isinstance(rec, dict) and rec.get('has_pins'):
        return 'pins'
    return 'need_lookup'


def coverage(master):
    """部品マスタ → 端子情報の充足状況サマリ（need_lookup が datasheet/ネット取得の候補）。"""
    by = collections.defaultdict(lambda: {'types': 0, 'count': 0})
    for r in master:
        st = terminal_status(r)
        by[st]['types'] += 1
        by[st]['count'] += r['count']
    need = sorted({(r['parts'], r['type'], r['maker']) for r in master
                   if terminal_status(r) == 'need_lookup'})
    return {'summary': dict(by), 'need_lookup': need}
