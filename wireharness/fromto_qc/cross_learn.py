# -*- coding: utf-8 -*-
"""複数製番の確認値から、機器の型に紐づくピン規則を横展開学習する。

学習ループ(seiban_config.pin_confirm)で人が確認した接点ピンを、製番をまたいで集計し、
『同じ機器の型・同じ接点種別なら同じピン』が複数製番で矛盾なく確認された時だけ規則化する。
これを未確認の製番にも候補として適用し、確認作業をさらに減らす(= できる限り自動化)。

安全則(◎誤答ゼロ):
  ・一致した製番数が MIN_SEIBAN 以上、かつピン値が1つに定まる(矛盾なし)時のみ規則化。
  ・転用は確度:中(候補)にとどめ、製番ごとの確認(pin_confirm)があれば常にそちらが優先。
TB台番号は製番ごとの物理配置で変わるため横展開しない(本モジュールはピンのみ)。
"""
import collections
import re

from . import seiban_config

MIN_SEIBAN = 2          # この製番数以上で一致したら規則化


def device_type(dev):
    """機器記号 → 型(回路番号を除いた部分)。'52-101'→'52', '86X-101'→'86X', 'FX-1'→'FX'。"""
    return re.sub(r'-?\d+$', '', str(dev or '')).strip() or str(dev or '')


def _iter_pin_confirms(configs=None, style=None):
    """各製番の pin_confirm を (製番, 機器型, マーカー, ピン) で列挙。
    style 指定時は、その流儀タグ(seiban_config.style)の製番だけを対象にする。"""
    cfg = configs if configs is not None else seiban_config.all_seiban()
    for seiban, rec in cfg.items():
        if style is not None and rec.get('style') != style:
            continue
        for key, pin in (rec.get('pin_confirm', {}) or {}).items():
            if ':' not in key:
                continue
            dev, mk = key.rsplit(':', 1)
            yield seiban, device_type(dev), mk, str(pin)


def aggregate(configs=None, style=None):
    """(機器型, マーカー) → {ピン: 確認した製番の集合}。横展開学習の素データ。
    style 指定で、その流儀内だけを集計(流儀をまたぐ矛盾を分離)。"""
    agg = collections.defaultdict(lambda: collections.defaultdict(set))
    for seiban, dtype, mk, pin in _iter_pin_confirms(configs, style):
        agg[(dtype, mk)][pin].add(seiban)
    return agg


def learned_pins(min_seiban=MIN_SEIBAN, configs=None, style=None):
    """横展開できるピン規則 {(機器型, マーカー): ピン}。
    値が1つに定まり(矛盾なし)、その値を確認した製番が min_seiban 以上のものだけ。
    style 指定時は同じ流儀の製番だけから学習する(作業者ごとにやり方が違う場合に分離)。"""
    out = {}
    for key, pins in aggregate(configs, style).items():
        if len(pins) != 1:              # ピン値が割れている=矛盾→学習しない
            continue
        pin, seibans = next(iter(pins.items()))
        if len(seibans) >= min_seiban:
            out[key] = pin
    return out


def suggest(dev, marker, min_seiban=MIN_SEIBAN, configs=None, style=None):
    """機器記号・マーカー → 横展開候補ピン or None。style で流儀を絞れる。"""
    return learned_pins(min_seiban, configs, style).get((device_type(dev), marker))
