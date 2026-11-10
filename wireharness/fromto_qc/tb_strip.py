# -*- coding: utf-8 -*-
"""端子台の台番号(どの回路がどのストリップに載るか)を配置図から決める。

人は、配置図の各端子台ストリップの『端子数(容量)』に合わせて、回路を順番に
割り付けている(検証: 4-21088)。例:
  TB101=BN30Wx12(12端子), TB102=x12, TB103=x12, TB104=x4
  3P回路は U/V/W/E の4端子 → TB101に 3回路(101,102,103)=12端子ちょうど、…TB104に1回路(110)。
規則: ストリップを番号順に、各回路の端子数(=相数, tb_phaseの相)を順に詰め、容量を
超えたら次のストリップへ。容量は TYPE_SPEC 'BN..Wx<N>' の N。
"""
import re
import ezdxf
from . import tb_phase

_CAP_RE = re.compile(r'[xX×](\d+)')


def read_strips(draw_paths):
    """配置図等 → [(台番号(str), 容量(int))] を台番号順。端子台ストリップのみ。"""
    strips = {}
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev = str(d.get('DEVICE', ''))
            m = re.match(r'^TB(\d+)$', dev)
            if not m:
                continue
            spec = d.get('TYPE_SPEC', '') or d.get('TYPE', '')
            # 主幹用の太物端子台(BN100W等)・主幹ストリップ(1桁番号 TB1/TB2)は
            # 分岐回路の相端子台ではないため除外。
            if re.search(r'100W|60W', spec) or len(m.group(1)) < 2:
                continue
            cap = None
            mm = _CAP_RE.search(spec)
            if mm:
                cap = int(mm.group(1))
            strips.setdefault(m.group(1), cap)
    # 容量が読めたストリップのみ、番号順
    out = [(k, v) for k, v in strips.items() if v]
    out.sort(key=lambda kv: int(kv[0]))
    return out


def assign(draw_paths, load_factor=None):
    """回路 → 台番号 の割付。配置図のストリップ容量に合わせ回路を順に詰める。
    容量が読めない/ストリップ不足なら、入り切らない回路は未割当(None)。
    load_factor: 製番ごとの負荷率(tb_phase.build_phase_map へ渡す)。"""
    strips = read_strips(draw_paths)
    pm = tb_phase.build_phase_map(draw_paths, load_factor=load_factor)
    # 回路ごとの端子数(=相数, E含む)。相が空(未判定)は4(3P+E)で仮置き。
    circ_n = {}
    for circ, info in pm.items():
        n = len(info.get('phases') or [])
        circ_n[circ] = n if n else 4
    circs = sorted(circ_n, key=lambda c: (len(c), c))
    out = {}
    si = 0
    used = 0
    for c in circs:
        n = circ_n[c]
        if si >= len(strips):
            out[c] = None
            continue
        name, cap = strips[si]
        if used + n > cap and used > 0:
            si += 1
            used = 0
            if si >= len(strips):
                out[c] = None
                continue
            name, cap = strips[si]
        out[c] = name
        used += n
    return out
