# -*- coding: utf-8 -*-
"""人が補完した配線(seiban_config.added_wires)を、同種(同じ流儀)の製番へ横展開する。

確認ループの横展開部: ツールが生成できない製造側の結線(制御TB渡り・device→TB 等)を人が
補完すると、同じ流儀タグ(seiban_config.style)の複数製番で同じ配線が繰り返し確認された時だけ、
未確認の同種製番へ候補(確度:中)として展開する。これで同じ補完を何度もやらずに済む。

安全則(◎誤答ゼロ, cross_learn と同じ整合ゲート):
  ・同一配線が min_seiban 以上の別製番で確認された時のみ横展開。
  ・展開は候補(確度:中)にとどめ、各製番の確認(added_wires)が常に優先。
  ・同種の判定は style タグに委ねる(同じ製品系列/作業手順の製番に同じ style を付ける)。
"""
import collections

from . import seiban_config

MIN_SEIBAN = 2          # この製番数以上で一致したら横展開


def _wkey(w):
    """配線 {from,to} → 向き非依存キー(frozenset)。"""
    f, t = w['from'], w['to']
    return frozenset(((f['機器'], f['番号'], f['端子']),
                      (t['機器'], t['番号'], t['端子'])))


def suggest(seiban, style=None, min_seiban=MIN_SEIBAN, configs=None):
    """同じ流儀の他製番で複数回 確認(補完)された配線を、この製番の横展開候補として返す。
    戻り: [{'from':{機器,番号,端子},'to':{...}}]。style=None なら流儀で絞らない。
    自製番で既に確認済みの配線は返さない(重複回避)。"""
    cfg = configs if configs is not None else seiban_config.all_seiban()
    counts = collections.defaultdict(set)      # wkey -> {確認した製番}
    repr_ = {}
    for sb, rec in cfg.items():
        if str(sb) == str(seiban):
            continue
        if style is not None and rec.get('style') != style:
            continue
        for w in rec.get('added_wires', []):
            k = _wkey(w)
            counts[k].add(str(sb))
            repr_[k] = w
    own = {_wkey(w) for w in seiban_config.added_wires(seiban)}
    out = []
    for k, sbs in counts.items():
        if len(sbs) >= min_seiban and k not in own:
            out.append(repr_[k])
    return out
