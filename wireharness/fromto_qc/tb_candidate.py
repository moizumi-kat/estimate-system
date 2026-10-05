# -*- coding: utf-8 -*-
"""端子台(TB)の相端子『候補』を自動生成する(茂泉様: 候補生成→人が確認→修正を学習)。

単線結線図は3相を1本で描くため、負荷(ブレーカ)→端子台の相配線(回路+相)は図面に
個別には現れない。人は「回路番号＝負荷の番号」「相数＝ブレーカの極数」から
回路+相(例 108U/108V/108W/108E)を起こしている。本モジュールは極数(pole_count)から
その候補を自動生成する。◎誤答ゼロ: 極数が判る回路のみ。2P相選択・台番号は候補(要確認)。

戻り(build): [{'circuit','poles','terminals':[{'t','phase','kind','status'}],'strip'}]
  status: 'ok'(3Pの確定相) / 'guess'(2Pの相選択) / 'earth'(接地)。strip は候補(未確定)。
"""
from . import pole_count as _pc


def build_candidates(draw_paths, learned_2p=None, add_earth=True):
    """図面群 → 回路ごとの TB相端子候補。pole_count から生成。"""
    pm = _pc.build_pole_map(draw_paths)
    out = []
    for circ in sorted(pm, key=lambda c: (len(c), c)):
        poles = pm[circ]
        phases = _pc.phases_for(circ, pm, learned_2p=learned_2p)
        if not phases:
            continue
        terms = []
        for ph in phases:
            terms.append({'t': f'{circ}{ph}', 'phase': ph,
                          'status': 'guess' if poles == 2 else 'ok'})
        if add_earth:
            terms.append({'t': f'{circ}E', 'phase': 'E', 'status': 'earth'})
        out.append({'circuit': circ, 'poles': poles, 'terminals': terms, 'strip': ''})
    return out


def candidate_terminal_set(draw_paths, learned_2p=None):
    """回路 → 候補端子集合 {'108':{'108U','108V','108W','108E'}, ...}(照合・UI用)。"""
    res = {}
    for c in build_candidates(draw_paths, learned_2p=learned_2p):
        res[c['circuit']] = set(t['t'] for t in c['terminals'])
    return res
