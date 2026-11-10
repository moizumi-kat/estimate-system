# -*- coding: utf-8 -*-
"""固定端点(特殊端点)の語彙。

ハーネスFrom-Toには、設計シーケンスにDEVICE属性ブロックとして現れない『固定端点』が
出てくる。これらを既知端点として語彙化し、生成・突合・端子付与で『不明機器』扱いしない。
10製番の④(作業者正解)から確認した実在端子に基づく:

  LUG          端子ラグ(主回路の入力)。端子番号なし。遮断器(MCCB/ELCB)の入力側へ。
  BOX          接地箱。端子なし/ET。各機器の接地(E)を集める共通点。
  SPD          避雷器。端子 1上/2上/3上/E下(相＋接地)または無し。
  SPD-分離器   SPDの分離器。1上=R,2上=S,3上=T,E下=E。端子台(TB)等へ。
  ET           接地端子(接地箱側)。

phase_of(): 固定端点の端子→相(R/S/T/E)。主回路/接地の生成・突合に使う。
"""
import re

# 固定端点のベース記号(大文字)。endpoint の DEVICE ベースがこれなら固定端点。
BASES = {'LUG', 'BOX', 'SPD', 'ET', '分離器', 'アース'}

VOCAB = {
    'LUG': {'terminals': {''}, 'role': '主回路入力(ラグ)', 'phases': {}},
    'BOX': {'terminals': {'', 'ET'}, 'role': '接地箱', 'phases': {'ET': 'E'}},
    'ET':  {'terminals': {'', 'ET'}, 'role': '接地端子', 'phases': {'ET': 'E'}},
    'SPD': {'terminals': {'', '1 上', '2 上', '3 上', 'E 下', 'COM上', 'NO 上', '-'},
            'role': '避雷器', 'phases': {'1 上': 'R', '2 上': 'S', '3 上': 'T', 'E 下': 'E'}},
    'SPD-分離器': {'terminals': {'1 上', '2 上', '3 上', 'E 下', '上', '下'},
                   'role': 'SPD分離器', 'phases': {'1 上': 'R', '2 上': 'S', '3 上': 'T', 'E 下': 'E'}},
}


def base_of(device):
    """機器記号 → 固定端点のベースキー。固定端点でなければ None。
    例 'SPD-分離器1'→'SPD-分離器', 'BOX'→'BOX', 'LUG'→'LUG', 'MCCB-1'→None。"""
    s = str(device or '').strip()
    if '分離器' in s:
        return 'SPD-分離器'
    head = re.split(r'[-0-9]', s.upper())[0]
    if head in BASES:
        return head
    if s.upper() in BASES:
        return s.upper()
    return None


def is_fixed(device):
    """固定端点か。"""
    return base_of(device) is not None


def terminals(device):
    """その固定端点で実在が確認された端子の集合(空欄含む)。未知なら空集合。"""
    b = base_of(device)
    return set(VOCAB.get(b, {}).get('terminals', set())) if b else set()


def phase_of(device, term):
    """固定端点の端子 → 相(R/S/T/E)。該当しなければ None。
    接地系(BOX/ET/E下)は 'E'、SPD/分離器の 1上/2上/3上 は R/S/T。"""
    b = base_of(device)
    if not b:
        return None
    t = (term or '').strip()
    ph = VOCAB.get(b, {}).get('phases', {})
    if t in ph:
        return ph[t]
    if re.search(r'E', t) or b in ('BOX', 'ET'):
        return 'E'
    return None


def terminal_known(device, term):
    """その固定端点で端子が語彙に在るか(表示・検証の根拠)。"""
    return (term or '').strip() in terminals(device)
