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
from . import tb_strip

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


def generate(draw_paths, load_factor=None, seiban=None):
    """主回路の From-To 辺を生成。戻り: [{from,to,circuit,phase,confidence}]。
    confidence は tb_phase の判定(確定/要確認)を引き継ぐ。
    load_factor: 製番ごとの負荷率(太物判定の確定に使う)。明示されなければ、seiban が
      与えられたとき seiban_config から製番ごとの入力を自動参照する。"""
    if load_factor is None and seiban:
        from . import seiban_config
        load_factor = seiban_config.load_factor(seiban)
    pm = tb_phase.build_phase_map(draw_paths, load_factor=load_factor)
    brk = _breakers(draw_paths)
    strip = tb_strip.assign(draw_paths, load_factor=load_factor)   # 回路→台番号

    def tbsym(circ):
        s = strip.get(circ)
        return f'TB-{s}' if s else 'TB'

    wires = []
    earth_pts = []     # 接地端子(TB-台:回路E)を回路順に。最後に接地箱へ数珠つなぎ。
    for circ in sorted(pm, key=lambda c: (len(c), c)):
        info = pm[circ]
        bsym = brk.get(circ)
        if not bsym:
            continue
        conf = info.get('confidence', '要確認')
        tb = tbsym(circ)
        for ph in sorted(info.get('phases', set())):
            if ph == 'E':
                earth_pts.append((f'{tb}:{circ}E', conf))
                continue
            wires.append({'from': 'LUG:', 'to': f'{bsym}:{_IN[ph]}',
                          'circuit': circ, 'phase': ph, 'confidence': conf})
            wires.append({'from': f'{bsym}:{_OUT[ph]}', 'to': f'{tb}:{circ}{ph}',
                          'circuit': circ, 'phase': ph, 'confidence': conf})
    # 接地: 各回路のE端子を回路順に数珠つなぎし、末端を接地箱(BOX-ET)へ。
    for i in range(len(earth_pts) - 1):
        wires.append({'from': earth_pts[i][0], 'to': earth_pts[i + 1][0],
                      'circuit': '', 'phase': 'E', 'confidence': earth_pts[i][1]})
    if earth_pts:
        wires.append({'from': earth_pts[-1][0], 'to': 'BOX-ET:',
                      'circuit': '', 'phase': 'E', 'confidence': earth_pts[-1][1]})
    return wires
