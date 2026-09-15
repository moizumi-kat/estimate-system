# -*- coding: utf-8 -*-
"""不具合ケースの学習ループ（自己整合では検知できない不具合を学習して検出）。

現状の検図（自己整合＋E1-E11）で出せない不具合（設計ルール違反・仕様値の誤り・
あるべき機器の欠落 等）は、現場/設計が「見逃し(missed)」として登録すると、
それを構造化ルールとして蓄積し、次回以降の図面で自動検出する。

ルール種別:
  require_with … 機器種別Aが在るなら機器種別Bも在るべき（例: MCCB→アースET）
  require_link … 機器Aは機器Bと同一号線で結線されるべき（例: 全モータ→サーマル）
  value_flag   … 機器の型式/値がブラックリスト（過去に誤った値）に一致
保存: kenzu_data/defect_rules.json（学習の蓄積）。
"""
import os
import json
from .geometry import norm

try:
    import kenzu_store
    _DIR = kenzu_store.DATA_DIR
except Exception:
    _DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'kenzu_data')
RULES_PATH = os.path.join(_DIR, 'defect_rules.json')


def _base(sym):
    return ''.join(ch for ch in str(sym).split('-')[0] if not ch.isdigit()).upper()


def load_rules():
    if not os.path.exists(RULES_PATH):
        return []
    try:
        with open(RULES_PATH, encoding='utf-8') as fp:
            return json.load(fp)
    except Exception:
        return []


def add_rule(kind, params, name='', source_run='', user=''):
    """不具合ルールを追加（見逃し登録からの学習）。"""
    os.makedirs(_DIR, exist_ok=True)
    rules = load_rules()
    rid = f"D{len(rules)+1:03d}"
    rules.append({'id': rid, 'kind': kind, 'params': params, 'name': name,
                  'source_run': source_run, 'user': user})
    with open(RULES_PATH, 'w', encoding='utf-8') as fp:
        json.dump(rules, fp, ensure_ascii=False, indent=1)
    # フィードバックにも記録（学習の履歴）
    try:
        kenzu_store.add_feedback(source_run or None, 'missed', rule=f'defect:{kind}',
                                 note=json.dumps(params, ensure_ascii=False), user=user)
    except Exception:
        pass
    return rid


def check(device_syms, gousen_nets, rules=None):
    """学習済み不具合ルールを図面に適用して findings を返す。
      device_syms : 図面に在る機器記号の集合（'52-102' 等）
      gousen_nets : {号線: set(機器記号)}（結線）
    """
    rules = rules if rules is not None else load_rules()
    bases = {_base(d) for d in device_syms}
    findings = []
    for r in rules:
        k = r['kind']
        p = r['params']
        if k == 'require_with':
            a, b = p['if'].upper(), p['need'].upper()
            if a in bases and b not in bases:
                findings.append({'rule': r['id'], 'type': 'D_require_with',
                                 'detail': f"{p['if']} が在るのに {p['need']} が図面にありません"
                                           f"（学習: {r.get('name','')}）。設計に確認してください。"})
        elif k == 'require_link':
            a, b = p['if'].upper(), p['need'].upper()
            if a in bases:
                linked = any(a in {_base(x) for x in devs} and b in {_base(x) for x in devs}
                             for devs in gousen_nets.values())
                if not linked:
                    findings.append({'rule': r['id'], 'type': 'D_require_link',
                                     'detail': f"{p['if']} が {p['need']} と結線されていません"
                                               f"（学習: {r.get('name','')}）。"})
        elif k == 'value_flag':
            bad = {norm(x) for x in p.get('values', [])}
            for d in device_syms:
                if norm(d) in bad:
                    findings.append({'rule': r['id'], 'type': 'D_value_flag',
                                     'detail': f"機器 {d} は過去に誤りが登録された値です"
                                               f"（学習: {r.get('name','')}）。確認してください。"})
    return findings


def learn_from_missed(if_base, need_base, name='', kind='require_with', user=''):
    """見逃し事例から不具合ルールを学習する簡易API。
    例: learn_from_missed('MCCB','ET','MCCBにアース必須') 。"""
    return add_rule(kind, {'if': if_base, 'need': need_base}, name=name, user=user)
