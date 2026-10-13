# -*- coding: utf-8 -*-
"""統合ハーネスFrom-Toドラフト生成(半自動モデル: 確実部分＋要確認)。

◎誤答ゼロ方針: 幾何だけでは全数の完全自動は難しいため、確実に生成できる部分を『高信頼』で、
残りを『要確認』で出す。最終判断は人(確認UI)。人の作業は『白紙から作成』→『ドラフトの確認』に
縮む。各配線に出所(source)と信頼区分(tier)を付す。

信頼区分(10製番の④で適合率を実測し設定):
  高信頼 … LUG→遮断器(主回路入力, 適合率89%・TB台番号に依存しない)
  候補  … SPD・分離器の定型分岐(制御盤で高一致・製番により未配線もあるため人が採否)
  要確認 … TB台番号/相・接地(物理台番号の体系差で突合が不安定)、制御トレース(端子名が不確実)
各配線 dict: {from,to,source,tier,phase?,confidence?}。build() で統合・重複排除して返す。
"""
import glob
import os

from . import main_circuit
from . import fixed_wiring
from . import edge_trace
from . import device_norm as dn


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


def _tier_main(w):
    """main_circuit の1配線 → 信頼区分。LUG→遮断器は高信頼、TB相/接地は要確認。"""
    fr, to = w.get('from', ''), w.get('to', '')
    if fr.startswith('LUG') or to.startswith('LUG'):
        return '高信頼'
    return '要確認'          # TB相・接地はTB台番号の体系差で不安定


def build(seiban_dir):
    """製番フォルダ → 統合ドラフト。戻り: {'wires':[...], 'summary':{区分:件数}}。
    各 wire: {'from','to','source','tier','phase'?,'confidence'?}。重複は高信頼を優先して排除。"""
    layout = _layout(seiban_dir)
    seq = _seq(seiban_dir)
    out = {}        # canon((from,to)) -> wire(高信頼優先)

    def add(fr, to, source, tier, **extra):
        key = frozenset((dn.canon(fr.split(':', 1)[0]) + '|' + (fr.split(':', 1) + [''])[1],
                         dn.canon(to.split(':', 1)[0]) + '|' + (to.split(':', 1) + [''])[1]))
        rank = {'高信頼': 0, '候補': 1, '要確認': 2}
        w = {'from': fr, 'to': to, 'source': source, 'tier': tier, **extra}
        if key not in out or rank[tier] < rank[out[key]['tier']]:
            out[key] = w

    # 主回路(LUG→遮断器=高信頼 / TB相・接地=要確認)
    for w in main_circuit.generate(layout):
        add(w['from'], w['to'], 'main_circuit', _tier_main(w),
            phase=w.get('phase'), confidence=w.get('confidence'))
    # SPD分離器の定型分岐(候補: 制御盤で高一致・採否は人)
    for w in fixed_wiring.generate(layout):
        add(w['from'], w['to'], 'fixed_wiring', '候補', phase=w.get('phase'))
    # 制御トレース(要確認: 端子名が不確実。端子名'?'は出さない)
    for p in seq:
        try:
            edges, _ = edge_trace.trace_edges(p)
        except Exception:
            continue
        for e in edges:
            (da, ta), (db, tb) = tuple(e)
            if ta == '?' or tb == '?':
                continue
            add(f'{da}:{ta}', f'{db}:{tb}', 'edge_trace', '要確認')

    wires = list(out.values())
    summary = {'高信頼': 0, '候補': 0, '要確認': 0}
    for w in wires:
        summary[w['tier']] += 1
    return {'wires': wires, 'summary': summary}


def to_rows(draft):
    """ドラフト → 表示用の行(区分順)。[(tier, source, from, to)]。"""
    order = {'高信頼': 0, '候補': 1, '要確認': 2}
    rows = sorted(draft['wires'], key=lambda w: (order[w['tier']], w['source']))
    return [(w['tier'], w['source'], w['from'], w['to']) for w in rows]
