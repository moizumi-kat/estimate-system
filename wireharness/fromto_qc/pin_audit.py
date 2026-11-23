# -*- coding: utf-8 -*-
"""端子番号の健全性監査（茂泉様方針2026-11）。

目的（現場混乱ゼロ・◎誤答ゼロ）:
  本番が出す端子番号は、必ず「機器TYPE→そのピン配（terminals レジストリ）」から選ばれていること。
  機器情報が無い/未登録なら、その旨を提示し『要確認』とする（推測・連番・実在しない番号を出さない）。

本モジュールは図面＋生成シートを読むだけの純監査。各機器の端子を次に分類する:
  - ok_pin          : TYPE既知・レジストリ登録済み・端子がそのピン配に実在
  - not_in_pinout   : TYPE既知・登録済みだが端子がピン配に無い（レジストリ定義不足 or 付番誤り）
  - unregistered    : TYPEはあるがレジストリ未登録（＝要登録。ピン配不明で検証不能）
  - untyped         : 機器のTYPEが図面から取れない（＝要確認）
  - non_numeric     : 相記号/接点名など数字でない端子（U/V/W/E, NO/NC 等）は対象外

戻り値で未登録TYPE・TYPE無し機器を一覧化し、登録インターフェース（別途）へ渡す。
"""
import collections
import glob
import os
import re

import ezdxf

from . import fromto_score as FS
from . import harness_sheet as HS
from . import terminals as T


def device_types(draw_paths):
    """図面群から {機器sym(_nsym): (PARTS, TYPE)}。本体ブロック優先（接点記号 _LS_* は除外）。
    PARTS(機器カテゴリ MCCB/AM/CP 等)と TYPE(型式)の両方でピン配パターンを引けるようにする。"""
    out = {}
    for p in draw_paths:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs or str(ins.dxf.name).startswith('_LS'):
                continue
            a = {k.dxf.tag: (k.dxf.text or '').strip() for k in ins.attribs}
            dev = a.get('DEVICE', ''); d1 = a.get('DEVICE1', '')
            sym = FS._nsym(f'{dev}-{d1}' if d1 else dev, {})
            pr = a.get('PARTS', ''); ty = a.get('TYPE', '')
            if sym and (pr or ty):
                out.setdefault(sym, (pr, ty))
    return out


def pattern_all_pins(pdef):
    """パターン定義から全端子番号を集める。パターンにより端子は pins / rows / main_rows /
    aux / coil / no / nc / com / blocks / coords の各フィールドに入るため、全部をまとめる。"""
    pins = set()
    for k in ('pins', 'coil', 'aux', 'no', 'nc', 'com'):
        for x in (pdef.get(k) or []):
            pins.add(str(x))
    for k in ('rows', 'main_rows', 'blocks'):
        for row in (pdef.get(k) or []):
            for x in row:
                pins.add(str(x))
    for x in (pdef.get('coords') or {}):
        pins.add(str(x))
    return pins


def _pattern_pins(type_str, parts=''):
    """(TYPE, PARTS) → (pattern名, 全端子集合)。レジストリ未登録なら (None, set())。"""
    try:
        pat = T.resolve_pattern(parts or '', type_str or '')
    except Exception:
        pat = None
    if not pat:
        return None, set()
    return pat, pattern_all_pins(T.library()['patterns'].get(pat) or {})


def audit_seiban(seiban_dir):
    """1製番の端子健全性監査。戻り: dict（分類カウント・未登録TYPE・TYPE無し機器・レジストリ外番号例）。"""
    draws = (sorted(glob.glob(os.path.join(seiban_dir, '図面データ_*.dxf'))) +
             sorted(glob.glob(os.path.join(seiban_dir, '図面データ_*.DXF'))))
    dev_type = device_types(draws)
    try:
        sheet = HS.build_sheet(FS._seq_paths(seiban_dir), skel_paths=FS._skel_paths(seiban_dir),
                               seiban=os.path.basename(seiban_dir.rstrip('/')).split('_')[0])
    except Exception:
        sheet = []
    cls = collections.Counter()
    unregistered = collections.Counter()      # TYPE → 件数
    untyped = collections.Counter()           # 機器系列 → 件数
    not_in_pinout = collections.Counter()     # '機器(TYPE)=pattern:端子' → 件数
    for r in (sheet or []):
        for m in r.get('members', []):
            sym = FS._nsym((m.get('device') or '') + ('-' + m['no'] if m.get('no') else ''), {})
            t = (m.get('terminal') or '').strip()
            if not t or sym.upper().startswith('TB'):
                continue
            if not re.fullmatch(r'\d+', t):
                cls['non_numeric'] += 1
                continue
            pr, ty = dev_type.get(sym, ('', ''))
            if not pr and not ty:
                cls['untyped'] += 1
                untyped[(re.match(r'[A-Za-z]*', sym).group(0) or sym[:4])] += 1
                continue
            pat, pins = _pattern_pins(ty, pr)
            if not pat:
                cls['unregistered'] += 1
                unregistered[f'{pr}|{ty}'] += 1
                continue
            if t in pins:
                cls['ok_pin'] += 1
            else:
                cls['not_in_pinout'] += 1
                not_in_pinout[f'{sym}({ty})={pat}:{t}'] += 1
    return {
        'seiban': os.path.basename(seiban_dir.rstrip('/')),
        'classes': dict(cls),
        'unregistered_types': unregistered.most_common(),
        'untyped_devices': untyped.most_common(),
        'not_in_pinout': not_in_pinout.most_common(),
        'verifiable_rate': cls['ok_pin'] / max(1, sum(cls[k] for k in ('ok_pin', 'not_in_pinout', 'unregistered', 'untyped'))),
    }


def format_report(rep):
    """監査結果を人が読める文字列に。"""
    c = rep['classes']
    lines = [f"製番 {rep['seiban']} 端子健全性監査",
             f"  実在ピン確認={c.get('ok_pin',0)} / ピン配外={c.get('not_in_pinout',0)}"
             f" / 未登録TYPE={c.get('unregistered',0)} / TYPE無し={c.get('untyped',0)}"
             f"  (検証可能率={rep['verifiable_rate']*100:.0f}%)",
             "  未登録TYPE(要登録):"]
    for ty, n in rep['unregistered_types'][:15]:
        lines.append(f"    {ty}  ×{n}")
    if rep['untyped_devices']:
        lines.append("  TYPE無し機器(要確認):")
        for s, n in rep['untyped_devices'][:10]:
            lines.append(f"    {s}  ×{n}")
    return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else '.'
    print(format_report(audit_seiban(d)))
