# -*- coding: utf-8 -*-
"""機器別名の自動学習。

人手ハーネスデータは、作業者の呼び方（機能名・和字）で機器を書くことがあり、
図面の機器記号と字面が一致しないことがある。例:
  - 「切102 / 入102」(選択スイッチの切/入 位置) ↔ 図面 "43-102"(COS device番号)
  - 「SL-102」(内部配置図の信号灯) ↔ "RL-102/GL-102"(シーケンスの色別ランプ)
これらは規則化されておらず担当ごとに揺れるため、過去の「図面＋人手From-To」の対から
統計的に学習して alias.json を育てる。

学習の手掛かり（強い順）:
  1) 端子番号一致: 同じ号線内で、人手の端点と図面の端子が同じ端子番号を持つ → 同一機器。
  2) 号線共起: 同じ号線に居て、互いに相手側で未マッチな 人手機器×図面機器 は同一の候補。
複数号線・複数盤で票を集計し、閾値を超えた対応だけ採用する（誤学習を避ける）。
"""
import re
import json
import collections
from .geometry import norm

_LETTER = re.compile(r'^[A-K]$')


def _ck(s):
    return re.sub(r'[^A-Z0-9]', '', norm(s))


def _human_nets_by_id(human_nets):
    """人手ネット → {号線id: [(devkey, terminal), ...]}（制御のみ、A〜K単文字は除外）。"""
    out = collections.defaultdict(list)
    for n in human_nets:
        if n.get('kind') != 'ctrl':
            continue
        sid = re.sub(r'[^0-9A-Z]', '', str(n['id']).split('_')[0].upper())
        for dev, no, term in n['ends']:
            if not dev:
                continue
            key = _ck(dev + (no or ''))
            if _LETTER.match(_ck(dev)):
                continue
            out[sid].append((key, str(term).strip()))
    return out


def _draw_nets_by_id(models):
    """図面モデル群 → {号線id(ctrl): {'devs': set(devkey), 'terms': {端子番号: set(devkey)}}}。"""
    out = collections.defaultdict(lambda: {'devs': set(), 'terms': collections.defaultdict(set)})
    for m in models:
        for n in m.nets:
            if not n['id'] or n.get('kind') != 'ctrl':
                continue
            sid = re.sub(r'[^0-9A-Z]', '', str(n['id']).upper())
            for t in n['terminals']:
                k = _ck(t.device)
                out[sid]['devs'].add(k)
                if t.name and t.name != '?':
                    out[sid]['terms'][str(t.name).strip()].add(k)
            for dk in n['devices']:
                out[sid]['devs'].add(_ck(dk))
    return out


def learn(pairs, min_votes=2, min_ratio=0.6):
    """pairs: [(human_nets, [drawing_models])] の対のリスト（複数盤）。
    戻り: {人手devkey: 図面devkey}（票と一貫性の閾値を満たしたもののみ）。"""
    # 図面機器の出現号線数（次数）。共通母線など高次数の機器は別名先として不適。
    draw_deg = collections.Counter()
    per_pair_D = []
    for human_nets, models in pairs:
        D = _draw_nets_by_id(models)
        per_pair_D.append(D)
        seen = collections.defaultdict(set)
        for sid, dinfo in D.items():
            for dk in dinfo['devs']:
                seen[dk].add(sid)
        for dk, sids in seen.items():
            draw_deg[dk] += len(sids)

    # human_key -> {draw_key: 支持した号線idの集合}（票数でなく“異なる号線での一致”を数える）
    support = collections.defaultdict(lambda: collections.defaultdict(set))
    for (human_nets, models), D in zip(pairs, per_pair_D):
        H = _human_nets_by_id(human_nets)
        for sid, ends in H.items():
            if sid not in D:
                continue
            dinfo = D[sid]
            dset = dinfo['devs']
            hset = {k for k, _ in ends}
            matched_h = set()
            # 1) 端子番号一致（相手側で未マッチ、かつ端子番号がこの号線で一意な時のみ）
            for hk, term in ends:
                if hk in dset:
                    matched_h.add(hk)
                    continue
                if term and term in dinfo['terms'] and len(dinfo['terms'][term]) == 1:
                    dk = next(iter(dinfo['terms'][term]))
                    if dk != hk and dk not in hset:
                        support[hk][dk].add(sid)
                        matched_h.add(hk)
            # 2) 一意ペアリング（双方1つずつ未マッチ）
            h_un = [hk for hk in hset if hk not in dset and hk not in matched_h]
            d_un = [dk for dk in dset if dk not in hset]
            if len(h_un) == 1 and len(d_un) == 1 and h_un[0] != d_un[0]:
                support[h_un[0]][d_un[0]].add(sid)

    alias = {}
    for hk, cand in support.items():
        # 次数で割って高次数（共通母線）を減点し、最良候補を選ぶ
        scored = {dk: len(sids) / (1 + draw_deg.get(dk, 0)) for dk, sids in cand.items()}
        dk = max(scored, key=scored.get)
        n_sid = len(cand[dk])
        total_sid = len(set().union(*cand.values()))
        # 採用条件: 異なる号線 min_votes 本以上が同じ対応を支持し、かつ最良候補が過半。
        if n_sid >= min_votes and n_sid / total_sid >= min_ratio and dk != hk \
           and draw_deg.get(dk, 0) <= 6:
            alias[hk] = dk
    return alias


def save(alias, path):
    json.dump(alias, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def load(path):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return {}
