# -*- coding: utf-8 -*-
"""負荷ケーブルの芯数から、各回路が実際に使う相数を正確に求める。

茂泉様「100%にする」: ブレーカ極数(3P/2P)は『ブレーカの極数』であって、
その負荷/端子台に実際に行く相数とは必ずしも一致しない(例: 3Pブレーカでも
負荷が単相なら端子台はU/Vの2相)。実際の相数は『負荷ケーブルの芯数』で決まる:
  4C(4芯)=3相(U/V/W)+接地E   3C=2相(U/V)+E   2C=1相(U)+E
  → 電源相数 = 芯数 - 1(接地)。相記号は U,V,W の順に芯数-1個。

ケーブル仕様テキスト(例 'CV3.5sq-4C')を単線図から拾い、同じ列(x近接)のブレーカ回路に
割り付ける。芯数が読めた回路のみ採用(誤答ゼロ: 不明は pole_count にフォールバック)。
"""
import re
import ezdxf

_CORE_RE = re.compile(r'[-－](\d+)\s*[CcＣ]\b')   # 'CV3.5sq-4C' の -4C(芯数)
_CVT_RE = re.compile(r'\bCVT\b|ＣＶＴ')            # トリプレックス=3芯(3相3線)
_BRK = re.compile(r'^(MCCB|ELCB|NFB|ELB)', re.I)
_COLX = 150.0   # 同一列とみなす x 近接(mm)


def _cable_phases(s):
    """ケーブル仕様文字 → 電源相数(接地E除く)。読めなければ None。
      CV…-4C → 4芯=3相(芯-1)、-3C → 2相。 CVT(トリプレックス) → 3相3線=3相。"""
    s = str(s or '')
    m = _CORE_RE.search(s)
    if m and ('SQ' in s.upper() or 'ｓｑ' in s):
        n = int(m.group(1))
        return max(0, min(3, n - 1)) if n >= 2 else None
    if _CVT_RE.search(s):
        return 3
    return None


def _texts_with_pos(doc):
    out = []
    for e in doc.modelspace():
        t = e.dxftype()
        if t == 'TEXT':
            out.append((e.dxf.insert.x, e.dxf.insert.y, (e.dxf.text or '').strip()))
        elif t == 'MTEXT':
            out.append((e.dxf.insert.x, e.dxf.insert.y, (e.plain_text() or '').strip()))
        elif t == 'INSERT' and e.attribs:
            for a in e.attribs:
                try:
                    out.append((a.dxf.insert.x, a.dxf.insert.y, (a.dxf.text or '').strip()))
                except Exception:
                    pass
    return out


def build_core_map(paths):
    """図面群 → {回路番号: 電源相数}。単線図の負荷ケーブルを列ごとにブレーカへ割付け、
    ケーブル種から相数を決める。1シート内で (ブレーカ列 x) に最も近いケーブルを採用、
    複数シートは多数決。相数が読めた回路のみ(誤答ゼロ)。"""
    votes = {}
    for p in (paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        brks = []   # (x, circuit)
        cabs = []   # (x, phase_count)
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev, no = d.get('DEVICE', ''), d.get('DEVICE1', '')
            if _BRK.match(dev) and re.match(r'^\d+$', str(no)):
                brks.append((ins.dxf.insert.x, str(no)))
        for (x, y, s) in _texts_with_pos(doc):
            ph = _cable_phases(s)
            if ph:
                cabs.append((x, ph))
        if not brks or not cabs:
            continue
        for (cx, ph) in cabs:
            best, bd = None, 1e18
            for (bx, circ) in brks:
                if abs(bx - cx) < bd:
                    bd, best = abs(bx - cx), circ
            if best is not None and bd <= _COLX:
                votes.setdefault(best, {}).setdefault(ph, 0)
                votes[best][ph] += 1
    return {c: max(v.items(), key=lambda kv: kv[1])[0] for c, v in votes.items()}


_PHASE_ORDER = ['U', 'V', 'W']


def phases_from_cores(phase_count):
    """電源相数 → 相記号。3→[U,V,W], 2→[U,V], 1→[U]。不明は[]。"""
    if not phase_count or phase_count < 1:
        return []
    return _PHASE_ORDER[:max(0, min(3, phase_count))]
