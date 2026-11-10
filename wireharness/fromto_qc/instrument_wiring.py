# -*- coding: utf-8 -*-
"""計器配線(変流器CT→電流計AM)の定型配線を生成する。

現場確認(2026-10-16): 貫通形変流器CTの一次側は主回路(太物)。二次側(細物)は電流計AMへ
  CT:K → AM:1 / CT:L → AM:2  の2本で接続する(標準のCT二次 k,l → 計器 1,2)。
E図(スケルトン)に描かれ、Fシートの制御トレース対象外のため、別途ここで生成する。
回路番号で CT-N と AM-N を対応付ける。単純な電流計のみ対象(マルチメーター等の相別・多端子は
製番依存で複雑なため対象外=要確認のまま)。
"""
import collections

import ezdxf


def detect_meters(draw_paths):
    """E/配置図群 → {回路(str): {'CT':sym, 'AM':sym}}。CT/AM を回路番号で集める。"""
    out = collections.defaultdict(dict)
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
            if dev in ('CT', 'AM') and no:
                out[str(no)].setdefault(dev, f'{dev}-{no}')
    return out


def generate(draw_paths, confidence='確度:中'):
    """計器配線(CT→AM)の定型を生成。戻り: [{from,to,confidence,basis}]。
    回路に CT と AM が揃う時だけ CT:K→AM:1 / CT:L→AM:2 を出す。"""
    wires = []
    for circ, devs in sorted(detect_meters(draw_paths).items()):
        if 'CT' in devs and 'AM' in devs:
            ct, am = devs['CT'], devs['AM']
            wires.append({'from': f'{ct}:K', 'to': f'{am}:1',
                          'confidence': confidence, 'basis': '計器配線(CT二次k→AM1)'})
            wires.append({'from': f'{ct}:L', 'to': f'{am}:2',
                          'confidence': confidence, 'basis': '計器配線(CT二次l→AM2)'})
    return wires
