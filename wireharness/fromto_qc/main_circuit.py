# -*- coding: utf-8 -*-
"""主回路(電源相配線 LUG→遮断器→端子台)の From-To を『生成』する。

単線結線図は3相を1本で描くため、主回路の相別配線(U/V/W)は図面に描かれない。
人は極数(tb_phase)から回路+相を起こしている。本モジュールは同様に:
  各回路(遮断器)について相ごとに
    入線: LUG → 遮断器:入力端子(U=1,V=3,W=5)
    出線: 遮断器:出力端子(U=2,V=4,W=6) → TB:<回路><相>
    接地: 遮断器(接地) → TB:<回路>E
を生成する。相は tb_phase(TERMINAL属性＋太物判定)に従う(太物回路はEのみ)。
制御配線は edge_trace(実配線トレース)で別途取得し、本生成分と併せて From-To とする。
"""
import re
import ezdxf
from . import tb_phase

_IN = {'U': '1', 'V': '3', 'W': '5'}
_OUT = {'U': '2', 'V': '4', 'W': '6'}


def _breakers(draw_paths):
    """{回路番号: 遮断器記号(例 'MCCB-102')}。"""
    out = {}
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev, no = d.get('DEVICE', ''), d.get('DEVICE1', '')
            if re.match(r'(MCCB|ELCB)', dev.upper()) and re.match(r'^\d+$', str(no)):
                out.setdefault(str(no), f'{dev}-{no}')
    return out


def generate(draw_paths):
    """主回路の From-To 辺を生成。戻り: [{from,to,circuit,phase,confidence}]。
    confidence は tb_phase の判定(確定/要確認)を引き継ぐ。"""
    pm = tb_phase.build_phase_map(draw_paths)
    brk = _breakers(draw_paths)
    wires = []
    for circ, info in pm.items():
        bsym = brk.get(circ)
        if not bsym:
            continue
        conf = info.get('confidence', '要確認')
        for ph in info.get('phases', set()):
            if ph == 'E':
                wires.append({'from': f'{bsym}:E', 'to': f'TB:{circ}E',
                              'circuit': circ, 'phase': 'E', 'confidence': conf})
                continue
            wires.append({'from': 'LUG:', 'to': f'{bsym}:{_IN[ph]}',
                          'circuit': circ, 'phase': ph, 'confidence': conf})
            wires.append({'from': f'{bsym}:{_OUT[ph]}', 'to': f'TB:{circ}{ph}',
                          'circuit': circ, 'phase': ph, 'confidence': conf})
    return wires
