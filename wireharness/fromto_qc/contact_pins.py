# -*- coding: utf-8 -*-
"""接点の付番規則(IEC/JIS標準、10製番の設計CAD CMNTJで確認)。

③手書き追記で人が接点の脇に書くピン番号(13/14, 21/22, 53/54, 95-98, A1/A2…)は、
機器種別ごとの標準付番に従う。設計CADの TERMINAL1/2 にも概ね入っているが、欠落や
検証のために標準規則を保持する。用途:
  ・function(pin): ピン→機能(coil/pole/NO/NC/aux)。UI表示・検証・相判定に使う。
  ・nth_aux(kind,n): 補助接点の第n接点のピン対(NO=x3/x4, NC=x1/x2)。欠落時の付番。
  ・is_valid(device_base, pin): その機器種別で標準的に在り得るピンか(誤答検知)。

確認した対応(設計CAD CMNTJ):
  11-12=切(NC) / 13-14=入(NO) / 21-22,23-24=第2接点 / 95-96=NC・97-98=NO(熱動51) /
  A1-A2=コイル / 1-6=主回路極(1/3/5入, 2/4/6出)。選択SW43は 13,21/14,22 等。
"""
import re

# 熱動継電器(51/OL 系)の標準トリップ接点
THERMAL_PINS = {'95': 'NC', '96': 'NC', '97': 'NO', '98': 'NO'}
COIL_PINS = {'A1', 'A2'}
POLE_IN = {'1', '3', '5'}
POLE_OUT = {'2', '4', '6'}


def function(pin, device_base=''):
    """ピン番号 → 機能。coil/pole_in/pole_out/NO/NC/aux/None。
    機器種別(device_base)を与えると熱動(51/OL)の95-98等を優先判定。"""
    p = str(pin or '').strip().upper()
    p = re.sub(r'\(.*?\)', '', p)                       # '14(+)'→'14'
    if not p:
        return None
    b = str(device_base or '').upper()
    if re.match(r'(51|OL|TH)', b) and p in THERMAL_PINS:
        return THERMAL_PINS[p]
    if p in COIL_PINS:
        return 'coil'
    # 選択(切替)SW 43/CS/COS の接点は『位置接点』でNO/NC一律判定が当たらない→selector
    if re.match(r'(43|CS|COS|SS)', b) and re.fullmatch(r'\d{1,2}', p):
        return 'selector'
    if p in POLE_IN:
        return 'pole_in'
    if p in POLE_OUT:
        return 'pole_out'
    if p in THERMAL_PINS:                                # 95-98 は熱動接点
        return THERMAL_PINS[p]
    m = re.fullmatch(r'(\d)(\d)', p)                     # 2桁の補助接点: 十の位=接点番号, 一の位=種別
    if m:
        units = m.group(2)
        if units in ('1', '2'):
            return 'NC'
        if units in ('3', '4'):
            return 'NO'
        return 'aux'                                     # 53/54,61/62,83/84 等は特殊補助
    return None


def nth_aux(kind, n):
    """補助接点の第n接点(n>=1)のピン対。kind='NO'→(n3,n4), 'NC'→(n1,n2)。
    例 nth_aux('NO',1)=('13','14'), nth_aux('NC',2)=('21','22')。"""
    n = int(n)
    if kind == 'NO':
        return (f'{n}3', f'{n}4')
    if kind == 'NC':
        return (f'{n}1', f'{n}2')
    raise ValueError(kind)


def is_no(pin, device_base=''):
    return function(pin, device_base) == 'NO'


def is_nc(pin, device_base=''):
    return function(pin, device_base) == 'NC'


def is_valid(device_base, pin, learned=None):
    """その機器種別でピンが標準的に在り得るか。learned(terminal_rule)があれば実績も加味。
    標準規則で機能が付けば True。学習語彙にあれば True。どちらも不可なら False(誤答候補)。"""
    if function(pin, device_base) is not None:
        return True
    if learned:
        r = learned.get(str(device_base).upper()) or learned.get(str(device_base))
        if r and any(str(pin).strip() == t for t, _ in r.get('terms', [])):
            return True
    return False
