# -*- coding: utf-8 -*-
"""ランプテスト母線(LTn)の『接点記法』を連続バスへ補正した検証用図面を生成するツール。

背景(2026-11・117/5-12043 で裏取り・LEARNING_NOTES 7-3b/7-3c):
  LTランプテスト母線は本来バスだが、図面では接点記号＋飛び飛びの短線で描かれる。このため
  幾何追跡が独立号線 LTn を作れず共通 RCn へ併合し、LTn が盤をまたぐ時に渡りを1件落とす
  (117 LT2=盤1 86X-201 ↔ 盤2 3-LT2)。これは図面側の表現不備で、設計が LTn を連続バスとして
  描けば既存システムがそのまま正しい渡りを検出する(コード変更不要)。

本ツールの役割(検証専用):
  原図のシーケンス(F*)シートで、ランプ relay(86X)のリード端(y≈ランプテスト帯のクロスポイント)が
  LTバス(3-LTn を含む長い水平バス)まで届かず残す隙間を、短い縦線で埋めて『正しく描かれた図面』を
  再現する。これにより LTn が独立号線として立ち上がり、盤またぎが渡りとして検出される。
  ◎安全: ランプ relay(86X)のリードだけを対象にする(MCCB/51 の alarm リードは RC 側＝対象外)。
         本番の抽出/選定ロジックは一切変更しない。生成物は検証(accuracy_check)専用。

使い方(wireharness ディレクトリで実行):
  python -I tools/fix_lamptest_bus.py <入力図面dir> <出力dir>
  # 入力dir の *.DXF/*.dxf を出力dir へコピーし、F* シートだけ LT バス補正を加える。
  # 以後の検証は出力dir を accuracy_check の <root>/<製番>/in/ として使う。
  # 実測(117): F001..F004 に計6本の縦線を追加 → LT2 が独立号線になり盤1↔2 渡りを検出。

社内資産の図面はリポジトリに含めないため、図面パスは外部から渡す(accuracy_check と同方針)。
"""
import os
import re
import sys
import glob

import ezdxf
from ezdxf import recover

# wireharness をパスに載せて fromto_qc を import 可能にする
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from fromto_qc.tracer import _build_components, _pt_seg, TOL, CROSS_DOT, DrawingModel  # noqa: E402

_LAMP_RELAY = re.compile(r'86X')     # ランプ relay(LT側)。MCCB/51 は RC側=対象外。
_MAX_VGAP = 60                        # LTバスとランプ relay リード端の隙間上限(実測=50)


def _read(path):
    try:
        return ezdxf.readfile(path)
    except Exception:
        return recover.readfile(path)[0]


def fix_sheet(doc, src_path):
    """F* シート1枚に LT バス補正の縦線を加える。加えた本数を返す(0=無変更)。"""
    c = _build_components(src_path)
    segs, find, cd = c['segs'], c['find'], c['comp_dev']
    m = DrawingModel(src_path)
    wl = sorted(m.wire_layers)[0] if m.wire_layers else '0'

    def comp_at(p, maxd=TOL * 2.5):
        best, bd = None, maxd
        for i, (a, b) in enumerate(segs):
            d = _pt_seg(p, a, b)
            if d < bd:
                bd, best = d, i
        return find(best) if best is not None else None

    # LTバス成分 = 3-LTn 押ボタンを含み かつ 長い水平バスを持つ成分 → その成分とバスy
    lt_bus_y = {}
    for k in cd:
        if not any(re.match(r'3-?LT\d', str(d)) for d in cd[k]):
            continue
        for i, (a, b) in enumerate(segs):
            if find(i) == k and abs(a[1] - b[1]) < 2 and abs(a[0] - b[0]) > 1000:
                lt_bus_y.setdefault(k, a[1])

    if not lt_bus_y:
        return 0

    dots = [(e.dxf.insert.x, e.dxf.insert.y) for e in m.msp
            if e.dxftype() == 'INSERT' and e.dxf.name == CROSS_DOT]
    added = 0
    for d in dots:
        k = comp_at(d)
        if k is None or k in lt_bus_y:
            continue
        devs = {str(x) for x in cd.get(k, set())}
        if not any(_LAMP_RELAY.match(dd) for dd in devs):   # ランプ relay(86X)のみ
            continue
        for by in lt_bus_y.values():                        # 直上の LTバスへ縦線で接続
            if TOL <= abs(d[1] - by) <= _MAX_VGAP:
                doc.modelspace().add_line((d[0], d[1]), (d[0], by),
                                          dxfattribs={'layer': wl})
                added += 1
                break
    return added


def fix_dir(in_dir, out_dir):
    """in_dir の図面を out_dir へコピーし、F* シートに LT バス補正を加える。
    戻り: {ファイル名: 追加本数}。"""
    os.makedirs(out_dir, exist_ok=True)
    report = {}
    for src in sorted(glob.glob(os.path.join(in_dir, '*.DXF'))
                      + glob.glob(os.path.join(in_dir, '*.dxf'))):
        base = os.path.basename(src)
        doc = _read(src)
        added = 0
        if re.search(r'-F\d', base.upper()):     # シーケンス図のみ補正
            try:
                added = fix_sheet(doc, src)
            except Exception:
                added = 0
        doc.saveas(os.path.join(out_dir, base))
        report[base] = added
    return report


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 1
    rep = fix_dir(argv[0], argv[1])
    tot = sum(rep.values())
    for nm, n in rep.items():
        if n:
            print(f'  {nm}: +{n} 本 (86X→LTバス)')
    print(f'完了: {tot} 本の LT バス補正を加え、{argv[1]} へ出力しました。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
