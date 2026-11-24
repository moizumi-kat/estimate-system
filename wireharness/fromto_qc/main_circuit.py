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


def _contactors(draw_paths):
    """接触器(52)がある回路番号の集合。出力側(→TB)の負荷機器が遮断器か否かを分ける。
    接触器のある回路=電動機回路＝出力は熱動51/接触器52経由(遮断器:2→TBでない)。"""
    out = set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            if d.get('DEVICE', '').upper() == '52' and re.fullmatch(r'\d+', str(d.get('DEVICE1', ''))):
                out.add(str(d['DEVICE1']))
    return out


def generate(draw_paths, load_factor=None, seiban=None):
    """主回路の From-To 辺を生成。戻り: [{from,to,circuit,phase,confidence}]。
    confidence は tb_phase の判定(確定/要確認)を引き継ぐ。
    load_factor: 製番ごとの負荷率(太物判定の確定に使う)。明示されなければ、seiban が
      与えられたとき seiban_config から製番ごとの入力を自動参照する。

    出力側(遮断器:2/4/6→TB)は【接触器(52)の無い給電回路のみ】生成する(手本一致＝遮断器:2→TB)。
    接触器のある電動機回路の出力は熱動51/接触器52経由で、図面にTYPE'51'が無く負荷機器を一意に
    決められない(手本は51/52で記録)→推測で誤機器を出さない(◎誤答ゼロ)。入力(LUG→遮断器)と接地は常に生成。"""
    if load_factor is None and seiban:
        from . import seiban_config
        load_factor = seiban_config.load_factor(seiban)
    pm = tb_phase.build_phase_map(draw_paths, load_factor=load_factor)
    brk = _breakers(draw_paths)
    strip = tb_strip.assign(draw_paths, load_factor=load_factor)   # 回路→台番号
    contactors = _contactors(draw_paths)                           # 接触器のある回路(電動機)=出力側を出さない

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
            if circ not in contactors:       # 給電回路のみ 遮断器:2/4/6→TB(電動機回路は熱動51経由で出さない)
                wires.append({'from': f'{bsym}:{_OUT[ph]}', 'to': f'{tb}:{circ}{ph}',
                              'circuit': circ, 'phase': ph, 'confidence': conf})
            elif ph in ('U', 'W'):           # 電動機回路: 遮断器出力→接触器入力(U:2→1 / W:6→5)。
                # V(4→3)は変流器(CT)経由で直結でないため出さない(手本一致・◎誤答ゼロ)。接触器=52-回路。
                wires.append({'from': f'{bsym}:{_OUT[ph]}', 'to': f'52-{circ}:{_IN[ph]}',
                              'circuit': circ, 'phase': ph, 'confidence': conf})
    # 接地: 各回路のE端子を回路順に数珠つなぎし、末端を接地箱(BOX-ET)へ。
    for i in range(len(earth_pts) - 1):
        wires.append({'from': earth_pts[i][0], 'to': earth_pts[i + 1][0],
                      'circuit': '', 'phase': 'E', 'confidence': earth_pts[i][1]})
    if earth_pts:
        wires.append({'from': earth_pts[-1][0], 'to': 'BOX-ET:',
                      'circuit': '', 'phase': 'E', 'confidence': earth_pts[-1][1]})
    return wires
