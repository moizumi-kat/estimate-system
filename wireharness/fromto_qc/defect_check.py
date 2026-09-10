# -*- coding: utf-8 -*-
"""設計次工程で走らせる図面不具合チェッカー（決定論・AI不要）。

思想（ユーザ運用方針）:
  設計の次工程で図面不具合を検出し、前工程(設計)へ「こう直したら？」で戻す。
  新しい不具合は defect_catalog.json に登録してバージョンアップ（見逃しログ→新ルール）。
  成熟したら設計自身が走らせる（シフトレフト）。

出力（フィードバック様式）: 各指摘は
  {rule, severity, confidence, 場所, 問題, 提案, 根拠}
で返し、そのまま設計への申し送りに使える。判断は設計（確認ゲート）。

実装ルール（v0.1）:
  R1 配置図漏れ   : schematic(シーケンス/スケルトン)に在る機器が内部配置図に無い
  R2 容量逆転     : 主幹遮断器のトリップ定格 < 分岐遮断器（保護協調の逆転）
  R3 中性線端子   : 単相3線(R,N,T)回路の負荷側TBが V（Nとすべき）
拡張は defect_catalog.json（R4〜, H1 等）に沿って追加する。
"""
import re
import os
import json
import ezdxf
from .geometry import DrawingModel, norm, SKIP_DEVICES


def _finding(rule, severity, place, problem, suggest, basis, confidence='high'):
    return {'rule': rule, 'severity': severity, 'confidence': confidence,
            '場所': place, '問題': problem, '提案': suggest, '根拠': basis}


# ---------- R2: 遮断器 定格解析 ----------
def parse_breaker_rating(spec):
    """SPEC1 '3P100/75AT' → {'pole':3,'frame':100,'trip':75}。'3P100AF'→trip無し。"""
    if not spec:
        return None
    m = re.search(r'(\d)\s*P\s*(\d+)\s*(?:/\s*(\d+)\s*AT)?', spec)
    if not m:
        return None
    pole = int(m.group(1)); frame = int(m.group(2))
    trip = int(m.group(3)) if m.group(3) else None
    return {'pole': pole, 'frame': frame, 'trip': trip}


def _breakers(model):
    """遮断器(MCCB/ELCB) [{sym,parts,spec,rating,y}] を返す。"""
    out = []
    for e in model.msp:
        if e.dxftype() != 'INSERT' or not e.attribs:
            continue
        a = {at.dxf.tag: at.dxf.text.strip() for at in e.attribs}
        parts = a.get('PARTS', '')
        if parts not in ('MCCB', 'ELCB'):
            continue
        sym = f"{a.get('DEVICE','')}-{a.get('DEVICE1','')}".strip('-')
        spec = a.get('SPEC1', '')
        out.append({'sym': sym, 'parts': parts, 'dev1': a.get('DEVICE1', ''),
                    'spec': spec, 'rating': parse_breaker_rating(spec),
                    'x': e.dxf.insert.x, 'y': e.dxf.insert.y})
    return out


def rule_R2_capacity(skeleton_model):
    """容量逆転: 主幹トリップ < 分岐トリップ を検出。
    主幹の推定: トリップを持つ遮断器のうち、フレーム最大かつ最上流（DEVICE1が短い/小）。
    分岐がその主幹より大きなトリップなら指摘（保護協調の逆転）。"""
    brs = [b for b in _breakers(skeleton_model) if b['rating'] and b['rating']['trip']]
    if len(brs) < 2:
        return []
    # 主幹候補: フレーム最大群の中でDEVICE1が最短(桁数)→最小
    maxframe = max(b['rating']['frame'] for b in brs)
    mains = [b for b in brs if b['rating']['frame'] == maxframe]
    main = min(mains, key=lambda b: (len(b['dev1']), b['dev1']))
    mt = main['rating']['trip']
    findings = []
    for b in brs:
        if b['sym'] == main['sym']:
            continue
        if b['rating']['trip'] > mt:
            findings.append(_finding(
                'R2', 'high', f"{b['sym']} / 主幹 {main['sym']}",
                f"分岐 {b['sym']} のトリップ {b['rating']['trip']}AT が 主幹 {main['sym']} の {mt}AT を超えています（保護協調の逆転）。",
                f"主幹 {main['sym']} の定格を {b['rating']['trip']}AT 以上にするか、分岐 {b['sym']} を {mt}AT 以下にしてください。",
                f"SPEC1: 主幹={main['spec']} / 分岐={b['spec']}",
                confidence='med'))
    return findings


# ---------- R3: 単相3線の中性線端子(V→N) ----------
def _tb_terminals(model):
    """端子台 [{sym,dev1,terms:[...]}]（TERMINAL属性）。"""
    out = []
    for e in model.msp:
        if e.dxftype() != 'INSERT' or not e.attribs:
            continue
        a = {at.dxf.tag: at.dxf.text.strip() for at in e.attribs}
        if a.get('DEVICE', '') == 'TB' and a.get('TERMINAL'):
            terms = [t.strip() for t in a['TERMINAL'].split(',') if t.strip()]
            out.append({'sym': f"TB-{a.get('DEVICE1','')}".strip('-'),
                        'dev1': a.get('DEVICE1', ''), 'terms': terms})
    return out


def rule_R3_neutral(model):
    """中性線端子の相名: 単相3線(端子に N を持つ電源側TB)がある盤で、
    負荷側TBが V を使っていれば V→N の疑いを指摘。"""
    tbs = _tb_terminals(model)
    single = [t for t in tbs if 'N' in t['terms'] and 'V' not in t['terms']]   # R,N,T 等
    if not single:
        return []
    # 注: 現状の図面は「相(単相3線/三相3線)」を属性で持たないため、V使用TBが単相か三相か
    #     決定論では区別できない。よって候補(low)として挙げ、設計が単相回路のTBかを確認する。
    #     ※作図仕様に「回路の相」属性を足せば、三相を除外してクリーンに検出できる（要提案）。
    findings = []
    for t in tbs:
        if 'V' in t['terms'] and 'N' not in t['terms']:
            findings.append(_finding(
                'R3', 'med', t['sym'],
                f"この盤には単相3線回路（例 {single[0]['sym']}={','.join(single[0]['terms'])}）があり、{t['sym']} は端子に V を使用（{','.join(t['terms'])}）。もし {t['sym']} が単相回路なら中性線は N とすべきです。",
                f"{t['sym']} が単相3線回路なら 'V' を 'N' に変更してください（三相回路なら V のままで正）。",
                "単相3線回路(N保有TB)が存在。相属性が無いため三相との区別は設計確認が必要",
                confidence='low'))
    return findings


# ---------- R1: 配置図漏れ ----------
# 同一機器の呼び分け（回路図↔配置図）をカテゴリで吸収する:
#   ランプ RL/GL/OL/WL/PL(機能色) ↔ SL(信号灯) 、 計器 AM/VM ↔ A/V
# → (カテゴリ, 回路番号) が配置図に在れば「漏れではない」と判定（別名辞書を手作りせず自動吸収）。
_CATEGORY = {
    'RL': 'LAMP', 'GL': 'LAMP', 'OL': 'LAMP', 'WL': 'LAMP', 'PL': 'LAMP', 'SL': 'LAMP',
    'AM': 'METER', 'VM': 'METER', 'A': 'METER', 'V': 'METER',
}


def _dev_records(model):
    """機器INSERT → [(sym, device, dev1, parts)]。"""
    out = []
    for e in model.msp:
        if e.dxftype() != 'INSERT' or not e.attribs:
            continue
        a = {at.dxf.tag: at.dxf.text.strip() for at in e.attribs}
        dev = a.get('DEVICE', '')
        if not dev or dev in SKIP_DEVICES:
            continue
        sym = f"{dev}-{a.get('DEVICE1','')}" if a.get('DEVICE1') else dev
        out.append((sym, dev, a.get('DEVICE1', ''), a.get('PARTS', '')))
    return out


def _cat_key(device, parts, dev1):
    """(カテゴリ, 回路番号) キー。カテゴリはランプ/計器を吸収、他はDEVICEそのもの。"""
    cat = _CATEGORY.get(device.upper()) or _CATEGORY.get(parts.upper()) or device.upper()
    return (cat, dev1)


def rule_R1_layout_missing(schematic_models, layout_model, alias=None):
    """schematic(シーケンス/スケルトン)に在る機器が layout(内部配置図)に無いものを指摘。
    exact名 と (カテゴリ,回路番号) の両方で突合し、呼び分け(SL⇔RL等)は漏れ扱いしない。
    alias: 追加の {schematic機器: layout機器} 手動別名。"""
    alias = alias or {}
    lay_names, lay_cat = set(), set()
    for sym, dev, dev1, parts in _dev_records(layout_model):
        lay_names.add(norm(sym))
        lay_cat.add(_cat_key(dev, parts, dev1))
    findings = []
    seen = set()
    for m in schematic_models:
        for sym, dev, dev1, parts in _dev_records(m):
            key = norm(alias.get(sym, sym))
            if key in seen:
                continue
            if key in lay_names or _cat_key(dev, parts, dev1) in lay_cat:
                continue
            # 端子台・ヒューズは配置図に別形で出るため除外
            if dev in ('TB',) or re.fullmatch(r'F', dev):
                continue
            # ランプ/計器で回路番号が無い機器は照合不能（呼び分けを一意に対応付けられない）→ 指摘しない
            if _CATEGORY.get(dev.upper()) in ('LAMP', 'METER') and not dev1:
                continue
            seen.add(key)
            findings.append(_finding(
                'R1', 'high', sym,
                f"機器 {sym} が回路図(シーケンス/スケルトン)に在りますが、内部配置図に見当たりません（配置図漏れの疑い）。",
                f"{sym} を内部配置図に追加してください。",
                "回路図に在り内部配置図に無い（機器名＋カテゴリ×回路番号で突合）",
                confidence='med'))
    return findings


def catalog():
    p = os.path.join(os.path.dirname(__file__), 'defect_catalog.json')
    return json.load(open(p, encoding='utf-8'))


def format_report(findings):
    """フィードバック様式のテキストレポート。"""
    lines = []
    for f in sorted(findings, key=lambda x: (x['rule'], x['severity'])):
        lines.append(f"[{f['rule']}/{f['severity']}/{f['confidence']}] {f['場所']}")
        lines.append(f"   問題: {f['問題']}")
        lines.append(f"   提案: {f['提案']}")
        lines.append(f"   根拠: {f['根拠']}")
    return "\n".join(lines)
