# -*- coding: utf-8 -*-
"""統合 細物ハーネス From-To ドラフト生成(半自動モデル)。

対象は細物(≤5.5sq=2人目・3人目の工程)。太物(≥8sq=1人目)は別工程(⑤⑥)で対象外。
幾何だけでは全数の完全自動は難しいため、生成した配線を『確度』でランク付けして出し、
最終判断は人(確認UI)で行う=◎誤答ゼロは人の確認ゲートで担保。確度は正誤の保証ではなく、
確認の優先順位(確度:高から確認すれば労力最小)。

確度(10製番の④で適合率を実測し設定):
  確度:高   … 複数の独立手法が同一配線を生成(相互裏取り)。最も確からしい。
  確度:中   … 単一のルール生成(主回路 LUG→遮断器→TB相, SPD分離器の定型分岐)。
              製番構造(多盤・母線)で外れることがあるため人が採否。
  確度:精査 … 実配線トレース単独(端子名が不確実)・TB台番号/接地(物理体系差)。重点確認。
各配線 dict: {from,to,sources(list),tier,phase?}。build() で統合・重複排除して返す。
"""
import glob
import os

from . import main_circuit
from . import fixed_wiring
from . import edge_trace
from . import device_norm as dn

TIER_HI = '確度:高'
TIER_MID = '確度:中'
TIER_LOW = '確度:精査'


def _layout(seiban_dir):
    return (glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.DXF')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.dxf')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*E*.DXF')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*E*.dxf')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.DXF')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.dxf')))


def _seq(seiban_dir):
    return (glob.glob(os.path.join(seiban_dir, '図面データ_*F*.DXF')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*F*.dxf')))


_RANK = {TIER_HI: 0, TIER_MID: 1, TIER_LOW: 2}


def _main_tier(w):
    """main_circuit 1配線の単独確度。LUG→遮断器=中(TB台番号非依存)、TB相/接地=精査(体系差)。"""
    fr, to = w.get('from', ''), w.get('to', '')
    if fr.startswith('LUG') or to.startswith('LUG'):
        return TIER_MID
    return TIER_LOW


def _key(fr, to):
    return frozenset((dn.canon(fr.split(':', 1)[0]) + '|' + (fr.split(':', 1) + [''])[1],
                      dn.canon(to.split(':', 1)[0]) + '|' + (to.split(':', 1) + [''])[1]))


def build(seiban_dir):
    """製番フォルダ → 統合ドラフト。戻り: {'wires':[...], 'summary':{確度:件数}}。
    各 wire: {'from','to','sources'(list),'tier','phase'?}。
    確度: 複数ソース一致=高。単独は内容別(LUG→遮断器/SPD=中, TB相/接地/トレース=精査)。"""
    layout = _layout(seiban_dir)
    seq = _seq(seiban_dir)
    acc = {}        # key -> {'from','to','sources':set,'tiers':set,'phase'}

    def add(fr, to, source, base_tier, **extra):
        k = _key(fr, to)
        if k not in acc:
            acc[k] = {'from': fr, 'to': to, 'sources': set(), 'tiers': set(), **extra}
        acc[k]['sources'].add(source)
        acc[k]['tiers'].add(base_tier)
        for kk, vv in extra.items():
            acc[k].setdefault(kk, vv)

    for w in main_circuit.generate(layout):
        add(w['from'], w['to'], 'main_circuit', _main_tier(w), phase=w.get('phase'))
    for w in fixed_wiring.generate(layout):
        add(w['from'], w['to'], 'fixed_wiring', TIER_MID, phase=w.get('phase'))
    for p in seq:
        try:
            edges, _ = edge_trace.trace_edges(p)
        except Exception:
            continue
        for e in edges:
            (da, ta), (db, tb) = tuple(e)
            if ta == '?' or tb == '?':         # 端子名不明は出さない
                continue
            add(f'{da}:{ta}', f'{db}:{tb}', 'edge_trace', TIER_LOW)

    wires = []
    summary = {TIER_HI: 0, TIER_MID: 0, TIER_LOW: 0}
    for v in acc.values():
        srcs = sorted(v['sources'])
        if len(srcs) >= 2:
            tier = TIER_HI                              # 複数手法が一致=相互裏取り
        else:
            tier = min(v['tiers'], key=lambda t: _RANK[t])   # 単独は最良の内容別確度
        w = {'from': v['from'], 'to': v['to'], 'sources': srcs, 'tier': tier}
        if v.get('phase'):
            w['phase'] = v['phase']
        wires.append(w)
        summary[tier] += 1
    return {'wires': wires, 'summary': summary}


def to_rows(draft):
    """ドラフト → 表示用の行(確度順)。[(tier, sources, from, to)]。"""
    order = {TIER_HI: 0, TIER_MID: 1, TIER_LOW: 2}
    rows = sorted(draft['wires'], key=lambda w: (order[w['tier']], ','.join(w['sources'])))
    return [(w['tier'], ','.join(w['sources']), w['from'], w['to']) for w in rows]
