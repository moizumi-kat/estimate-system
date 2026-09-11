# -*- coding: utf-8 -*-
"""From-To データの出力（CSV）。

From-Toの「本体」は最も綺麗な読み手（既定 Claude Vision）を採用し、
幾何・他モデルは“裏付け(corroboration)”に回す。裏付けが取れない号線に★要確認を付け、
人の確認を最小化する。
"""
import csv
from .geometry import norm


def _corroboration(devs, geom_set):
    d = set(devs)
    if not geom_set:
        return '裏付けなし', True
    if d and d <= geom_set:
        return '幾何一致', False
    if d & geom_set:
        return '幾何一部一致', True
    return '裏付けなし', True


def export_fromto_csv(primary_traced, geom_by_senban, path, extra_sources=None):
    """primary_traced: {号線:{devices:set, terminals:[{device,terminal}]}} （本体）。
    geom_by_senban: {号線:set(機器)} （裏付け）。
    extra_sources: {名前:{号線:set(機器)}} （Gemini等の追加裏付け・任意）。
    """
    extra_sources = extra_sources or {}
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['号線', '接続機器', '端子(判明分)', '裏付け', '要確認', '裏付け詳細'])
        for sid in sorted(primary_traced):
            devs = sorted(primary_traced[sid]['devices'])
            terms = sorted({f"{t['device']}:{t['terminal']}"
                            for t in primary_traced[sid].get('terminals', []) if t.get('terminal')})
            label, need = _corroboration(devs, geom_by_senban.get(sid, set()))
            detail = [f"幾何={','.join(sorted(geom_by_senban.get(sid, set())))}"]
            for name, src in extra_sources.items():
                detail.append(f"{name}={','.join(sorted(src.get(sid, set())))}")
            w.writerow([sid, ';'.join(devs), ';'.join(terms), label, '★' if need else '', ' | '.join(detail)])
    return path
