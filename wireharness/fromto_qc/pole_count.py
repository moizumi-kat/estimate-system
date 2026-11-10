# -*- coding: utf-8 -*-
"""単線結線図（スケルトン）/配置図から、各回路(ブレーカ)の極数を正確に求める。

茂泉様: 「単線結線図での各線の極数を正確に出す」。極数はTB相端子(回路+相 U/V/W)の
生成に必須。極数はブレーカ仕様文字(例 '3P50/15AT','NV63-CV 2P')の『NP』に明示される。
・一次情報: 機器ブロック属性(DEVICE=MCCB/ELCB…, DEVICE1=回路番号, SPEC/TYPE に NP)。
  → 配置図(DCT)・単線図ともブロック属性に極数があればここから確実に取得(学習不要)。
・極数と相の対応(手本検証, 5-29032で一致):
    3P → U,V,W   2P → U,W(または U,V)   1P → U    いずれも接地があれば +E
  2Pの相選択(U/W=単相3線 vs U/V=単相2線)は製番で揺れるため、既定 U/W を候補とし、
  人の修正を学習して寄せる(誤答ゼロ: 断定せず候補)。
"""
import re
import ezdxf

_POLE_RE = re.compile(r'(\d)\s*[PpＰ]')
_BREAKER = re.compile(r'^(MCCB|ELCB|NFB|ELB|MCB|ELCB・|漏電|配線用)', re.I)
_SPEC_TAGS = ('SPEC1', 'SPEC2', 'SPEC3', 'SPEC4', 'TYPE', 'TYPE_SPEC', 'DEVICE2', 'PARTS')

# 極数 → 電源相(接地Eは別途付加)。2Pの既定は U,W（単相3線）。製番差は学習で上書き。
PHASES_BY_POLE = {3: ['U', 'V', 'W'], 2: ['U', 'W'], 1: ['U']}


def _poles_from_blob(blob):
    m = _POLE_RE.search(str(blob or ''))
    if m:
        p = int(m.group(1))
        if 1 <= p <= 4:
            return p
    return None


def build_pole_map(paths):
    """図面群 → {回路番号(str): 極数(int)}。ブレーカ機器ブロックの仕様から抽出。
    同一回路に複数仕様があれば多数決(通常一致)。極数不明の回路は含めない(誤答ゼロ)。"""
    votes = {}
    for p in (paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev = d.get('DEVICE', '')
            no = d.get('DEVICE1', '') or d.get('DEVICE2', '')
            if not dev or not _BREAKER.match(dev) or not re.match(r'^\d+$', str(no)):
                continue
            blob = ' '.join(str(d.get(k, '')) for k in _SPEC_TAGS)
            poles = _poles_from_blob(blob)
            if poles:
                votes.setdefault(str(no), {}).setdefault(poles, 0)
                votes[str(no)][poles] += 1
    out = {}
    for circ, vc in votes.items():
        out[circ] = max(vc.items(), key=lambda kv: kv[1])[0]
    return out


def phases_for(circuit, pole_map, learned_2p=None):
    """回路番号 → 電源相リスト(U/V/W)。極数不明なら []。
    learned_2p: {回路: ['U','V']} のような2P相選択の学習上書き(任意)。"""
    poles = pole_map.get(str(circuit))
    if not poles:
        return []
    if poles == 2 and learned_2p and str(circuit) in learned_2p:
        return list(learned_2p[str(circuit)])
    return list(PHASES_BY_POLE.get(poles, []))
