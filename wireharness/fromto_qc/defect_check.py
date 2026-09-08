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
from .geometry import DrawingModel, norm


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
    has_single = any('N' in t['terms'] and 'V' not in t['terms'] for t in tbs)  # R,N,T 等
    if not has_single:
        return []
    findings = []
    for t in tbs:
        if 'V' in t['terms'] and 'N' not in t['terms']:
            findings.append(_finding(
                'R3', 'med', t['sym'],
                f"単相3線回路のある盤で {t['sym']} の端子が {','.join(t['terms'])}（V を使用）。単相の中性線は N とすべきです。",
                f"{t['sym']} の 'V' を 'N' に変更してください（単相3線の中性線）。",
                f"電源側TBに N を持つ単相3線回路が存在（例 R,N,T）。{t['sym']}=末端の相名不一致",
                confidence='med'))
    return findings


# ---------- R1: 配置図漏れ ----------
def rule_R1_layout_missing(schematic_models, layout_model, alias=None):
    """schematic(シーケンス/スケルトン)に在る機器が layout(内部配置図)に無いものを指摘。
    alias: {schematic機器: layout機器} 別名（SL⇔RL等）で誤検出を抑制。"""
    alias = alias or {}
    lay = set()
    for e in layout_model.msp:
        if e.dxftype() == 'INSERT' and e.attribs:
            a = {at.dxf.tag: at.dxf.text.strip() for at in e.attribs}
            dev = a.get('DEVICE', '')
            if dev:
                sym = f"{dev}-{a.get('DEVICE1','')}" if a.get('DEVICE1') else dev
                lay.add(norm(sym))
    findings = []
    seen = set()
    for m in schematic_models:
        for dv in m.devices:
            key = norm(alias.get(dv.sym, dv.sym))
            if key in seen or key in lay:
                continue
            seen.add(key)
            # 端子台や号線など配置図に出ない種類は除外
            if re.fullmatch(r'TB.*|F|RL|GL|OL|WL', dv.sym):
                continue
            findings.append(_finding(
                'R1', 'high', dv.sym,
                f"機器 {dv.sym} が回路図(シーケンス/スケルトン)に在りますが、内部配置図に見当たりません（配置図漏れの疑い）。",
                f"{dv.sym} を内部配置図に追加してください。",
                "回路図に在り内部配置図に無い（機器名突合）",
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
