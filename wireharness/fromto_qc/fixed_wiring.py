# -*- coding: utf-8 -*-
"""固定端点(SPD・分離器/LUG/BOX)の主回路・接地配線を『生成』する。

単線結線図には描かれない固定端点の配線を、10製番の④(作業者正解)で確認した一定パターンから
生成する。主回路相(main_circuit)・制御(edge_trace)と併せて From-To を構成する。

SPD・分離器の一定パターン(制御盤3φで検証: 4-26020-14/15/9):
  TB-N:{N}R ↔ SPD-分離器N:1 上      (相R)
  TB-N:{N}S ↔ SPD-分離器N:2 上      (相S)
  TB-N:{N}T ↔ SPD-分離器N:3 上      (相T)
  SPD-分離器N:E 下 ↔ SPD:            (分離器の接地→避雷器)
  SPD: ↔ BOX:                        (避雷器→接地箱) ※分離器があれば1本
分電盤は相表記(1N等)・接地経路(SPD-ET-(MCCB))が異なるため、既定の相は製番で上書き可能とし、
不明な点は confidence='要確認' とする(◎誤答ゼロ)。
"""
import re
import ezdxf

from . import fixed_points


def detect_separators(draw_paths):
    """図面群 → {分離器番号(str): 機器記号}。'SPD・分離器'/'分離器' を検出。"""
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
            if '分離器' in dev:
                n = no or '1'
                out.setdefault(str(n), f'SPD-分離器{n}')
    return out


def detect_earth_sink(draw_paths):
    """避雷器の接地先端点を図面から判定。
    'SPD-ET' 系があれば分電盤型('SPD-ET-(MCCB)', 相R/N/T)、無ければ制御盤型('SPD', 相R/S/T)。
    戻り: (sink_sym, phases)。分離器はあるが判定材料が薄い場合もあるため呼び出し側で確度判断。"""
    names = set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev = d.get('DEVICE', '')
            if 'SPD' in dev:
                names.add(dev)
    if any('ET' in n for n in names):
        return 'SPD-ET-(MCCB)', ('R', 'N', 'T')
    return 'SPD', ('R', 'S', 'T')


def generate(draw_paths, phases=None, confidence='要確認'):
    """固定端点(SPD・分離器)の主回路・接地配線の『候補』を生成する。

    分離器を検出し、主幹TBの相↔分離器↔避雷器↔接地箱 の配線を出す。相表記・接地先は
    図面から推定(制御盤=R/S/T・SPD、分電盤=R/N/T・SPD-ET-(MCCB))。
    ただし『分離器が図にあっても作業者が配線しない製番(例 5-21094-42)』があるため、
    自動確定はせず既定で confidence='要確認'(人が確認して採否)=◎誤答ゼロ。
    phases を明示指定すればそれを使う。分離器が無ければ空。"""
    seps = detect_separators(draw_paths)
    wires = []
    if not seps:
        return wires
    sink, det_phases = detect_earth_sink(draw_paths)
    ph = tuple(phases) if phases else det_phases
    for n in sorted(seps, key=lambda s: (len(s), s)):
        sep = seps[n]
        for i, p in enumerate(ph, start=1):
            wires.append({'from': f'TB-{n}:{n}{p}', 'to': f'{sep}:{i} 上',
                          'phase': p, 'confidence': confidence, 'basis': 'SPD分離器(主幹相・候補)'})
        wires.append({'from': f'{sep}:E 下', 'to': f'{sink}:', 'phase': 'E',
                      'confidence': confidence, 'basis': 'SPD分離器(接地・候補)'})
    wires.append({'from': f'{sink}:', 'to': 'BOX:', 'phase': 'E',
                  'confidence': confidence, 'basis': 'SPD→接地箱(候補)'})
    return wires
