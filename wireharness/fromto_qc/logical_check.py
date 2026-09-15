# -*- coding: utf-8 -*-
"""論理層（設計 From-To）の検証基盤。

目的:
  ハーネス「シート」データは物理・製造層（LUG/WAGO/中継/ダクト方向を含む）なので、
  wire-trace が生成する論理層 From-To と直接一致で測ると差が出て当然。
  そこで論理層（号線ネット＝機器の集合）で比較し、かつ名称ゆらぎ（A↔ALX1 等）を
  「同じ号線に載る機器の共起」から自動推定して吸収する。

提供:
  - auto_alias(draw_g, human_g): 同一号線の機器差から別名(人手名→図面名)を自動提案。
  - logical_match(draw_g, human_g, alias): 号線ごとの機器集合一致を測定。
  - drawing_nets_by_gousen(fused): 図面(wire-trace融合)の 号線→機器集合。
  - human_ctrl_nets_by_gousen(path): 人手データの制御 号線→機器集合。
"""
import collections
from .geometry import norm
from .compare import parse_human

# 論理層では製造中継要素を機器として数えない（物理層の付加のため）
PASSTHROUGH = {'LUG', 'WAGO'}


def _dev_norm(sym):
    return norm(sym)


def drawing_nets_by_gousen(fused):
    """wire-trace 融合結果 → {号線(norm): 機器集合(norm)}。"""
    out = {}
    for sid, n in fused['nets'].items():
        g = norm(sid)
        if not g:
            continue
        devs = {_dev_norm(d) for d in n['devices']}
        devs = {d for d in devs if d and d not in PASSTHROUGH}
        if devs:
            out.setdefault(g, set()).update(devs)
    return out


def human_ctrl_nets_by_gousen(path):
    """人手データ → {号線(norm): 機器集合(norm)}（制御ネットのみ, id=号線）。"""
    out = {}
    for n in parse_human(path):
        if n.get('kind') != 'ctrl' or not n.get('id'):
            continue
        g = norm(n['id'])
        if not g:
            continue
        devs = {_dev_norm(e[0] + e[1]) for e in n['ends'] if e[0]}
        devs = {d for d in devs if d and d not in PASSTHROUGH}
        if devs:
            out.setdefault(g, set()).update(devs)
    return out


def auto_alias(draw_g, human_g, min_votes=1):
    """同一号線に載る機器の差から、人手名→図面名の別名を自動提案。

    各号線 gで、人手だけの機器 ho と図面だけの機器 do を見て、
    1対1(|ho|=|do|=1)なら強い対応票、そうでなければ候補として弱く記録。
    複数号線で一貫して同じ対応が出れば採用。
    戻り: (alias, votes) alias={人手名: 図面名}, votes=対応の投票詳細。
    """
    pair_votes = collections.Counter()   # (human_dev, draw_dev) -> 票
    for g in set(draw_g) & set(human_g):
        ho = human_g[g] - draw_g[g]
        do = draw_g[g] - human_g[g]
        if not ho or not do:
            continue
        if len(ho) == 1 and len(do) == 1:
            pair_votes[(next(iter(ho)), next(iter(do)))] += 2   # 強い票
        else:
            for h in ho:
                for d in do:
                    pair_votes[(h, d)] += 1                      # 弱い候補
    # 保守化: 強い票(=1対1が複数号線で一貫, 票>=2)のみ採用し、
    # さらに図面名の取り合いを1対1に解決（曖昧な対応＝ノイズを排除）。
    cand = [(v, h, d) for (h, d), v in pair_votes.items() if v >= max(min_votes, 2)]
    cand.sort(reverse=True)
    alias = {}
    used_draw = set()
    used_human = set()
    for v, h, d in cand:
        if h in used_human or d in used_draw:
            continue
        alias[h] = d
        used_human.add(h)
        used_draw.add(d)
    return alias, pair_votes


def _apply(devs, alias):
    return {alias.get(d, d) for d in devs}


def logical_match(draw_g, human_g, alias=None):
    """号線ごとの機器集合一致（論理層）。alias は人手→図面。"""
    alias = alias or {}
    common = set(draw_g) & set(human_g)
    only_h = set(human_g) - set(draw_g)   # 人手にあり図面に号線が無い
    only_d = set(draw_g) - set(human_g)
    exact = 0
    subset = 0                            # 図面が人手の機器を全部含む（＋余剰）
    partial = 0
    dev_inter = dev_h = dev_d = 0
    detail = []
    for g in sorted(common):
        h = _apply(human_g[g], alias)
        d = draw_g[g]
        inter = h & d
        dev_inter += len(inter); dev_h += len(h); dev_d += len(d)
        if h == d:
            exact += 1
        elif h <= d:
            subset += 1
        elif inter:
            partial += 1
        detail.append((g, sorted(h), sorted(d)))
    return {
        'gousen_common': len(common), 'gousen_only_human': len(only_h),
        'gousen_only_draw': len(only_d),
        'exact': exact, 'subset(図面⊇人手)': subset, 'partial': partial,
        'gousen_recall%': round(len(common) / max(len(human_g), 1) * 100, 1),
        'dev_recall%': round(dev_inter / max(dev_h, 1) * 100, 1),
        'detail': detail,
        'only_human_gousen': sorted(only_h),
    }
