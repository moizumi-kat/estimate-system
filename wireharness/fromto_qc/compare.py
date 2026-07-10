# -*- coding: utf-8 -*-
"""
人手ハーネスデータ(.txt) の解析と、抽出From-Toとの突合（一致率）。

社内ハーネスデータ.txt の形式（実データより）:
  - タブ区切り。冒頭にタイトル行、末尾寄りに図番。
  - "* * <種類> <サイズ> * * * *" が電線種類・サイズのグループヘッダ/区切り。
  - 主回路: 線色(赤/白/青/E…)で始まる行が2行=電線1本(両端)。
  - 制御回路: 線色でない列1(=号線 例 10201,1R,RC1)でグループ化した多点ネット。
  - 各端点: col5=機器記号, col6=機器番号, col7=端子番号。
"""
import re
import itertools
from .geometry import norm

COLORS = {'E', '赤', '白', '青', '黒', '緑', '黄', '茶', '橙', '灰', '桃', '紫', 'ｴｺ', 'エコ'}


def parse_human(path, encoding='cp932'):
    raw = open(path, 'rb').read().decode(encoding, errors='replace')
    rows = [[c.strip() for c in l.split('\t')] for l in raw.splitlines()]

    def g(c, i):
        return c[i] if len(c) > i and c[i] not in ('', '*') else ''

    nets = []
    cur_type = cur_size = ''
    power_buf = []
    cur_ctrl = None
    cur_ctrl_id = None

    def flush_power():
        nonlocal power_buf
        for i in range(0, len(power_buf) - 1, 2):
            nets.append({'kind': 'power', 'id': '', 'type': cur_type, 'size': cur_size,
                         'ends': [power_buf[i], power_buf[i + 1]]})
        power_buf = []

    for c in rows:
        col1 = c[1].strip() if len(c) > 1 else ''
        col2 = c[2].strip() if len(c) > 2 else ''
        if col1 == '*' and col2 == '*':
            flush_power()
            cur_ctrl = None
            cur_ctrl_id = None
            t = g(c, 3)
            s = g(c, 4)
            if t:
                cur_type = t
            if s:
                cur_size = s
            continue
        dev, no, term = g(c, 5), g(c, 6), g(c, 7)
        if not (dev or no or term):
            continue
        end = (dev, no, term)
        if col1 in COLORS:
            power_buf.append(end)
        else:
            hid = col1 + ('_' + col2 if col2 else '')
            if hid != cur_ctrl_id:
                flush_power()
                cur_ctrl = {'kind': 'ctrl', 'id': hid, 'type': cur_type, 'size': cur_size, 'ends': []}
                nets.append(cur_ctrl)
                cur_ctrl_id = hid
            cur_ctrl['ends'].append(end)
    flush_power()
    return nets


def human_device_edges(nets, alias=None):
    """人手ネット → 機器→機器エッジ集合（端子/サイズ無視）。"""
    alias = alias or {}
    E = set()
    for n in nets:
        devs = sorted({alias.get(norm(e[0] + e[1]), norm(e[0] + e[1])) for e in n['ends'] if e[0]})
        for a, b in itertools.combinations(devs, 2):
            E.add((a, b))
    return E


def model_device_edges(model, alias=None):
    """抽出モデル → 機器→機器エッジ集合。"""
    alias = alias or {}
    E = set()
    for n in model.nets:
        devs = sorted({alias.get(norm(s), norm(s)) for s in n['devices']})
        for a, b in itertools.combinations(devs, 2):
            E.add((a, b))
    return E


def score_edges(human_E, model_E):
    inter = human_E & model_E
    rec = len(inter) / len(human_E) if human_E else 0.0
    prec = len(inter) / len(model_E) if model_E else 0.0
    return {'human': len(human_E), 'model': len(model_E), 'match': len(inter),
            'recall': round(rec * 100, 1), 'precision': round(prec * 100, 1)}
