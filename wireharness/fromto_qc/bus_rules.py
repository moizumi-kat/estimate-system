# -*- coding: utf-8 -*-
"""母線（相バス・電源）ルール生成（ステップ②の補完・製造側）。

背景（実測で確定）:
  号線の無い母線(102S,1R,RC1…)は「無い」のではなく、スケルトンの単線図に
  V_SOUブロック(SOU1/2/3=相ラベル)として確実に存在する。ただし単線図の連結
  （バス線→機器記号貫通→端子）は自動トレースが最難関で不安定。
  一方、母線の結線は強い定型があり、人手ハーネス数セットから学習できる。

方針（茂泉様確定: 人手同一でなく回路的に正しいで合格）:
  C→B。人手データから母線テンプレを学習し、SOUラベル＋機器の回路番号で
  決定論生成する（不安定な幾何トレースに依存しない）。

学習テンプレ（実データで一貫）:
  breaker_out … 遮断器の相→出力端子（R→2,S→4,T→6）。CP/MCCB/ELCB。
  dev_term    … 下流機器種別×相→端子（52→A2, 51→95/97, TB→'', 表示灯→扉 等）。
保存: kenzu_data/bus_templates.json。
"""
import os
import re
import json
from collections import defaultdict, Counter
from .geometry import norm

try:
    import kenzu_store
    _DIR = kenzu_store.DATA_DIR
except Exception:
    _DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'kenzu_data')
TEMPLATE_PATH = os.path.join(_DIR, 'bus_templates.json')

# 号線名 = 回路番号 + 相(R/S/T) + 連番。例 102S, 1R, 1R1, 22T。
PHASE_RE = re.compile(r'^(?P<ckt>\d*)(?P<ph>[RST])(?P<sub>\d*)$')
BREAKERS = {'MCCB', 'ELCB', 'ELB', 'CP', 'ACB', 'LBS'}
# 遮断器 相→出力端子（3φ標準）。学習で上書きされるが既定を持つ。
DEFAULT_OUT = {'R': '2', 'S': '4', 'T': '6'}
DEFAULT_IN = {'R': '1', 'S': '3', 'T': '5'}


def _base(sym):
    return ''.join(ch for ch in str(sym).split('-')[0] if not ch.isdigit()).upper()


def _parse_human(path):
    """人手ハーネス → {号線: [(dev, no, term)]}（cp932, tab; col1号線 col5機器 col6no col7端子）。"""
    def C(c, i):
        return c[i].strip() if len(c) > i else ''
    nets = defaultdict(list)
    lines = open(path, 'rb').read().decode('cp932', errors='replace').splitlines()
    for l in lines:
        c = l.split('\t')
        g, dev, no, term = C(c, 1), C(c, 5), C(c, 6), C(c, 7)
        if not g or dev in ('*', ''):
            continue
        nets[g].append((dev, no, term))
    return nets


def learn(human_paths):
    """人手ハーネス数セットから母線テンプレを学習し保存。戻り: templates。"""
    out_term = defaultdict(Counter)   # (breaker_base, phase) -> Counter(端子)
    dev_term = defaultdict(Counter)   # (devbase, phase) -> Counter(端子)
    for p in human_paths:
        try:
            nets = _parse_human(p)
        except Exception:
            continue
        for g, members in nets.items():
            m = PHASE_RE.match(g)
            if not m:
                continue
            ph = m.group('ph')
            for dev, no, term in members:
                b = _base(dev)
                if not b or b in ('LUG', 'WAGO'):
                    continue
                if b in BREAKERS:
                    out_term[(b, ph)][term] += 1
                else:
                    dev_term[(b, ph)][term] += 1
    templates = {
        'breaker_out': {f"{b}|{ph}": cnt.most_common(1)[0][0]
                        for (b, ph), cnt in out_term.items() if cnt},
        'dev_term': {f"{b}|{ph}": cnt.most_common(1)[0][0]
                     for (b, ph), cnt in dev_term.items() if cnt},
    }
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(TEMPLATE_PATH, 'w', encoding='utf-8') as fp:
            json.dump(templates, fp, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return templates


def load_templates():
    if os.path.exists(TEMPLATE_PATH):
        try:
            with open(TEMPLATE_PATH, encoding='utf-8') as fp:
                return json.load(fp)
        except Exception:
            pass
    return {'breaker_out': {}, 'dev_term': {}}


def sou_labels(models):
    """図面(スケルトン等)の V_SOU から母線号線ラベル一覧を返す（kind='main'のsenban）。"""
    labels = set()
    for m in models:
        for (v, x, y), k in zip(getattr(m, 'senban', []), getattr(m, 'senban_kind', [])):
            g = norm(v)
            if k == 'main' and PHASE_RE.match(g):
                labels.add(g)
    return sorted(labels)


def devices_by_circuit(models):
    """図面の機器を回路番号(DEVICE1の数字)でグループ化 → {ckt: [(base, dev, no)]}。"""
    out = defaultdict(list)
    seen = set()
    for m in models:
        for e in m.msp:
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
            dev = a.get('DEVICE', '').strip()
            no = a.get('DEVICE1', '').strip()
            if not dev or not no:
                continue
            ckt = ''.join(ch for ch in no if ch.isdigit())
            key = (dev, no)
            if not ckt or key in seen:
                continue
            seen.add(key)
            out[ckt].append((_base(dev), dev, no))
    return out


def generate(models, templates=None):
    """図面のSOUラベル＋回路機器＋学習テンプレ → 母線ネット {号線: [(dev,no,term)]}。
    生成方針: 各母線 {ckt}{phase} に、その回路の
      - 遮断器 を相出力端子(学習: R→2,S→4,T→6)で
      - 下流機器 を学習した(種別×相→端子)で
    接続する。回路的に正しい母線を決定論生成する（人手完全一致は狙わない）。
    """
    templates = templates or load_templates()
    bo = dict(templates.get('breaker_out', {}))
    dt = dict(templates.get('dev_term', {}))
    labels = sou_labels(models)
    circ = devices_by_circuit(models)
    nets = {}
    for g in labels:
        m = PHASE_RE.match(g)
        ckt, ph = m.group('ckt'), m.group('ph')
        if not ckt:
            continue
        members = []
        for base, dev, no in circ.get(ckt, []):
            if base in BREAKERS:
                term = bo.get(f"{base}|{ph}") or DEFAULT_OUT.get(ph, '')
                members.append((dev, no, term))
            else:
                key = f"{base}|{ph}"
                if key in dt:                       # 学習にある下流機器だけ接続（過剰結線を抑制）
                    members.append((dev, no, dt[key]))
        if len(members) >= 2:
            nets[g] = members
    return nets
