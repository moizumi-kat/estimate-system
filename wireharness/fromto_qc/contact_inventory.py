# -*- coding: utf-8 -*-
"""機器型ごとの『接点在庫』(空き端子)を④から学習し、小さい順の割付と機能判定に使う。

現場確認・ユーザー整理(2026-10-16):
  ・リレー/接触器の同種接点(a接点=NO/b接点=NC)はどれも電気的に等価。
  ・だから割付は『空き端子を数字の小さい順に割り付ける』でよい(動作は同じ)。
  ・正解率は実番号の一致でなく『同じ機能の端子か』で見る。
本モジュールは④から機器型(52, 3, 43…)ごとの NO/NC 端子の在庫(ペア列)を学習する。
例: 52 → NO=[(13,14),(53,54),(73,74),(83,84)], NC=[(21,22)] など(機器型で異なる)。
在庫は小さい順に並べて保持し、必要数だけ先頭から割り付ける。
"""
import collections
import json
import re

from . import fromto_truth as ft
from . import contact_pins as cp
from . import cross_learn
from .harness_learn import _state_path

INVENTORY_PATH = _state_path('contact_inventory.json')


def _pairs(nums):
    """ソート済みピン列 → (奇,偶)ペア列。[13,14,53,54]→[('13','14'),('53','54')]。"""
    s = sorted(set(int(n) for n in nums))
    out, used = [], set()
    for n in s:
        if n in used:
            continue
        mate = n + 1 if n % 2 == 1 else None     # NO/NCは 奇-偶 の対(13-14, 53-54, 21-22)
        if mate in set(s):
            out.append((str(n), str(mate)))
            used.add(n); used.add(mate)
    return out


def learn(seiban_dirs):
    """④群 → {機器型: {'NO':[(ピン,ピン)...], 'NC':[...]}}。端子の機能で分類し小さい順。"""
    no = collections.defaultdict(set)
    nc = collections.defaultdict(set)
    for d in seiban_dirs:
        try:
            r = ft.load_seiban(d)
        except Exception:
            continue
        for w in r['wires']:
            for e in w['ends']:
                sym = ft.endpoint_sym(e)
                dt = cross_learn.device_type(sym)
                t = (e['term'] or '').strip()
                if not re.fullmatch(r'\d\d', t):
                    continue
                f = cp.function(t, dt)
                if f == 'NO':
                    no[dt].add(t)
                elif f == 'NC':
                    nc[dt].add(t)
    inv = {}
    for dt in set(no) | set(nc):
        inv[dt] = {'NO': _pairs(no[dt]), 'NC': _pairs(nc[dt])}
    return inv


def save(inv, path=INVENTORY_PATH):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(inv, f, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def load(path=INVENTORY_PATH):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def pairs_for(device, kind, inv=None):
    """機器記号・種別('NO'/'NC') → 在庫のペア列(小さい順)。未知型は空。"""
    inv = inv if inv is not None else load()
    dt = cross_learn.device_type(device)
    return [tuple(p) for p in inv.get(dt, {}).get(kind, [])]
