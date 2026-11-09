# -*- coding: utf-8 -*-
"""ハーネスシートデータの人手フォーマット出力（制御／主回路を別々に）。

システムの流れ:
  ①検図（不具合検知）→ ②設計へ修正提案・承認 → ③問題解決後にハーネスシート出力。
本モジュールは③の出力を担当し、制御(control)と主回路(main)を別ファイルで、
人手ハーネスシートと同じ tab区切り(cp932)フォーマットに整形する。

人手フォーマット（列）:
  空 | 号線/線色 | 号線接尾 | サイズ/種別 | 方向 | 機器記号 | 機器番号 | 端子
グループ見出し行: 空 | * | * | <種別> | <サイズ> | * | * | * | *
"""
import collections


def _row(gousen='', suffix='', size='', direction='', dev='', no='', term=''):
    return '\t'.join(['', gousen, suffix, size, direction, dev, no, term])


def _header(wtype, size):
    return '\t'.join(['', '*', '*', wtype, size, '*', '*', '*', '*'])


def _endpoints_by_gousen(rows):
    """生成した電線(from-to) → 号線ごとの端点リスト（重複除去、順序保持）。"""
    byg = collections.OrderedDict()
    meta = {}
    for r in rows:
        g = r['gousen']
        byg.setdefault(g, [])
        meta.setdefault(g, (r.get('wtype', 'KIV'), r.get('size', '')))
        for key in ('from', 'to'):
            ep = r[key]
            direction = r.get('dir_' + key, '') or ('扉' if r.get('door_' + key) else '')
            rec = (ep[0], ep[1], ep[2], direction)
            if rec not in byg[g]:
                byg[g].append(rec)
    return byg, meta


def control_sheet(rows, seiban='', title='配線情報リスト'):
    """ハーネスシート（号線でグループ化）→ 人手フォーマット文字列。
    title は先頭行の見出し（既定は元データと同じ '配線情報リスト'）。"""
    byg, meta = _endpoints_by_gousen(rows)
    out = [f'"{title}"', '', '', '\t\t\t\t\t\t\t\t\t' + seiban]
    cur = None
    for g, eps in byg.items():
        wtype, size = meta[g]
        if (wtype, size) != cur:
            out.append(_header(wtype, size))
            cur = (wtype, size)
        for dev, no, term, direction in eps:
            out.append(_row(gousen=g, size=size, direction=direction, dev=dev, no=no, term=term))
    return '\n'.join(out) + '\n'


def main_sheet(main_rows, seiban=''):
    """主回路ハーネスシート（相/色でグループ化）→ 人手フォーマット文字列。
    main_rows は main_circuit.generate_main の出力（LUG付与済）。"""
    out = ['"配線情報リスト（主回路）"', '', '', '\t\t\t\t\t\t\t\t\t' + seiban]
    cur = None
    for r in main_rows:
        wtype, size = r.get('wtype', 'HIV'), r.get('size', '')
        if (wtype, size) != cur:
            out.append(_header(wtype, size))
            cur = (wtype, size)
        f, t = r['from'], r['to']
        color = f[2] or t[2]
        marker = r.get('marker', '')
        # from（母線側）と to（機器側）を2行で（人手の主回路と同様）
        out.append(_row(gousen=color, size=(marker or size), dev=f[0], no=f[1], term=f[2]))
        out.append(_row(gousen=color, size=size, dev=t[0], no=t[1], term=t[2]))
    return '\n'.join(out) + '\n'


def _is_main_row(r):
    """行が主回路か（相バス R/S/T 号線 or 主回路電線 HIV or kind=main）。"""
    import re
    g = str(r.get('gousen', ''))
    return (r.get('kind') == 'main' or r.get('wtype') == 'HIV'
            or bool(re.match(r'^\d*[RST]\d*$', g)))


def split_control_main(rows):
    """生成ハーネス行を 制御 / 主回路 に分割。"""
    ctrl = [r for r in rows if not _is_main_row(r)]
    main = [r for r in rows if _is_main_row(r)]
    return ctrl, main


def write_control_main(rows, out_dir, seiban=''):
    """制御・主回路を別ファイルで、cp932(本番)とUTF-8(画面確認)両方で書き出す。
    戻り: dict(制御_cp932, 主回路_cp932, 制御_utf8, 主回路_utf8)。"""
    import os
    ctrl, main = split_control_main(rows)
    outs = {}
    for tag, rs in [('制御', ctrl), ('主回路', main)]:
        text = control_sheet(rs, seiban, title=f'配線情報リスト（{tag}）')
        p932 = os.path.join(out_dir, f'{seiban}_{tag}ハーネスシート.txt')
        putf = os.path.join(out_dir, f'{seiban}_{tag}ハーネスシート_UTF8.txt')
        with open(p932, 'w', encoding='cp932', errors='replace') as f:
            f.write(text)
        with open(putf, 'w', encoding='utf-8') as f:
            f.write(text)
        outs[f'{tag}_cp932'] = p932
        outs[f'{tag}_utf8'] = putf
    return outs


def write_sheets(control_rows, main_rows, out_dir, seiban=''):
    """制御・主回路を別ファイルで書き出す。戻り: (制御path, 主回路path)。"""
    import os
    cp = os.path.join(out_dir, f'{seiban}_制御ハーネスシート.txt')
    mp = os.path.join(out_dir, f'{seiban}_主回路ハーネスシート.txt')
    with open(cp, 'w', encoding='cp932', errors='replace') as f:
        f.write(control_sheet(control_rows, seiban))
    with open(mp, 'w', encoding='cp932', errors='replace') as f:
        f.write(main_sheet(main_rows, seiban))
    return cp, mp
