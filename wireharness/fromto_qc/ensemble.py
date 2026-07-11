# -*- coding: utf-8 -*-
"""
複数の独立した読み（幾何 / Claude Vision / Gemini Vision）を号線単位で突合し、
「全一致＝自動確定 / 多数決＝準確定 / 割れ＝人が確認」を判定するアンサンブル。

思想（前段の議論より）:
  最終精度 = 1 − サイレント誤り（どの検出にも引っかからない誤り）。
  独立した検出を重ねるほど「同時に外れる」確率が激減 → 100%へ近づく。
  幾何は座標という別モダリティで最も直交、Claude/Geminiは別モデルで誤りが部分的に独立。
"""
from collections import Counter
from .geometry import norm


def geom_by_senban(model, alias=None):
    """幾何モデル → {号線: set(機器)}。"""
    alias = alias or {}
    out = {}
    for n in model.nets:
        if n['id']:
            out.setdefault(n['id'], set()).update(alias.get(norm(s), norm(s)) for s in n['devices'])
    return out


def ensemble(sources):
    """sources: {"幾何": {号線:set(機器)}, "Claude": {...}, "Gemini": {...}} （任意個）。
    戻り: 号線ごとの判定リスト。decision ∈ {confirmed, majority, split}。"""
    all_ids = set()
    for s in sources.values():
        all_ids |= set(s.keys())
    rows = []
    for sid in sorted(all_ids):
        votes = {name: set(s[sid]) for name, s in sources.items() if sid in s and s[sid]}
        if not votes:
            continue
        setlist = [frozenset(v) for v in votes.values()]
        c = Counter(setlist)
        top, topn = c.most_common(1)[0]
        n = len(setlist)
        if n >= 2 and topn == n:
            decision = 'confirmed'          # 全員一致 → 自動確定
        elif topn >= 2 and topn > n - topn:
            decision = 'majority'           # 多数決成立 → 準確定
        else:
            decision = 'split'              # 割れ → 人が確認
        rows.append({'senban': sid, 'result': sorted(top), 'agree': topn, 'of': n,
                     'decision': decision, 'votes': {k: sorted(v) for k, v in votes.items()}})
    return rows


def summarize(rows):
    c = Counter(r['decision'] for r in rows)
    total = len(rows) or 1
    auto = c.get('confirmed', 0) + c.get('majority', 0)
    return {'total': len(rows), **dict(c),
            'auto_confirm_pct': round(auto / total * 100, 1),
            'need_human_pct': round(c.get('split', 0) / total * 100, 1)}
