# -*- coding: utf-8 -*-
"""等電位ノード（電気）と 物理配線（端子ペア・渡り）の2層モデル。

方針（茂泉様との整理）:
  ・ハーネス＝From-To＝端子↔端子の物理電線。だが同じ号線に複数機器がぶら下がる母線は
    「等電位の1ノード」。ノード内の配線ペア（渡り）は一意でない（木の選び方）。
  ・一意に決まる電気的真＝「等電位ノード（どの端子が同電位か）」。号線＝ノード名。
  ・目標を2層に分ける:
      ① 等電位ノード … 100%を狙う。号線でまとまる端子集合。母線＝1ノード。
      ② 物理配線(渡り) … 一意でない。端子座標＋規則(最近傍・端子結線数上限)で木を生成。
  ・正解率はノード単位で測る（②配線の順番違いは電気的に同じなので誤りに数えない）。

本モジュール:
  parse_harness(paths)      … 人手ハーネス(cp932) → 電線[(end,end)]。end=機器:端子
  harness_nodes(paths)      … 端子ペアを併合した等電位ノード（正解）
  drawing_nodes(fused)      … 抽出(号線でまとまる端子集合)＝ノード
  compare_nodes(dn, hn)     … ノード単位の一致（端子P/R・完全一致ノード数）
  generate_wiring(terms)    … ノード内の物理配線を規則生成（最近傍デイジーチェーン）
"""
import csv
import math
import collections
from .geometry import norm


# ---------- 端子キー ----------
def tkey(dev, num, term):
    d = dev + ('-' + num if num else '')
    return norm(d) + ':' + norm(term)


# ---------- ハーネス（正解）読み込み ----------
def parse_harness(paths):
    """cp932ハーネスシート(-1,-2...) → 電線ペア一覧。各endは{dev,num,term,color,wt,size}。"""
    wires = []
    for p in paths:
        rows = list(csv.reader(open(p, encoding='cp932'), delimiter='\t'))
        wt = ''
        size = ''
        buf = []
        for r in rows:
            c = (r + [''] * 11)[:11]
            dev, num, term = c[5].strip(), c[6].strip(), c[7].strip()
            if c[1] == '*' and c[3] not in ('', '*'):     # 種別・サイズのヘッダ行
                wt, size = c[3], c[4]
                buf = []
                continue
            if not dev or dev == '*':
                continue
            buf.append({'color': c[1], 'wt': wt, 'size': c[3] or size,
                        'dev': dev, 'num': num, 'term': term})
            if len(buf) == 2:
                wires.append((buf[0], buf[1]))
                buf = []
    return wires


def _union_nodes(pairs):
    """端子キーのペア列 → 併合した集合（等電位ノード）のリスト。"""
    par = {}

    def find(a):
        par.setdefault(a, a)
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    def uni(a, b):
        par[find(a)] = find(b)

    for a, b in pairs:
        uni(a, b)
    groups = collections.defaultdict(set)
    for a, b in pairs:
        r = find(a)
        groups[r].add(a)
        groups[r].add(b)
    return [frozenset(s) for s in groups.values()]


def harness_nodes(paths):
    """人手ハーネス → 等電位ノード（frozenset[端子キー] のリスト）＝正解ノード。"""
    wires = parse_harness(paths)
    pairs = [(tkey(**{k: e[k] for k in ('dev', 'num', 'term')}),
              tkey(**{k: f[k] for k in ('dev', 'num', 'term')})) for e, f in wires]
    return _union_nodes(pairs)


# ---------- 図面抽出 → ノード ----------
def drawing_nodes(fused):
    """harness.fuse 結果 → {号線: set(端子キー)}。号線＝等電位ノード名。
    端子の号線番号自体もノードキーに含める（端子台端子=号線名 の一致を拾うため）。"""
    out = {}
    for sid, net in fused['nets'].items():
        keys = set()
        for t in net.get('terminals', []):
            dev = t.device
            if '-' in dev:
                d, n = dev.rsplit('-', 1)
            else:
                d, n = dev, ''
            nm = t.name if (t.name and t.name != '?') else norm(sid)
            keys.add(tkey(d, n, nm))
        # 端子の無い機器も、その号線をノードとして端子キー化
        for d in net.get('devices', []):
            if d == 'TB':
                continue
            if '-' in d:
                dd, nn = d.rsplit('-', 1)
            else:
                dd, nn = d, ''
            keys.add(tkey(dd, nn, norm(sid)))
        if keys:
            out[norm(sid)] = keys
    return out


# ---------- ノード単位の照合 ----------
def compare_nodes(dnodes, hnodes):
    """抽出ノード(dict 号線→set) と 正解ノード(list[set]) を端子集合で照合。
    各正解ノードに最も重なる抽出ノードを当て、端子の再現率/一致数を測る。"""
    dlist = list(dnodes.values())
    total_h_terms = 0
    hit_terms = 0
    exact = 0
    partial = 0
    per = []
    for hn in hnodes:
        best = set()
        for dn in dlist:
            inter = hn & dn
            if len(inter) > len(best):
                best = inter
        total_h_terms += len(hn)
        hit_terms += len(best)
        if best == hn and len(hn) > 1:
            exact += 1
        elif best:
            partial += 1
        per.append({'node': sorted(hn), 'recovered': sorted(best),
                    'ratio': round(len(best) / max(len(hn), 1), 2)})
    multi = [h for h in hnodes if len(h) > 1]
    return {'正解ノード数': len(hnodes), 'うち多端子(母線含む)': len(multi),
            '端子再現率%': round(hit_terms / max(total_h_terms, 1) * 100, 1),
            '完全一致ノード': exact, '部分一致ノード': partial,
            'detail': per}


# ---------- ② 物理配線の規則生成（渡り＝木） ----------
def generate_wiring(terminals, max_degree=2):
    """等電位ノードの端子群 → 物理配線(端子ペア列)を規則生成。
    terminals: [(key, x, y)]。最近傍デイジーチェーン（各端子の結線数<=max_degree）。
    戻り: [(keyA, keyB)] （N端子ならN-1本）。"""
    pts = [t for t in terminals if t[1] is not None and t[2] is not None]
    if len(pts) <= 1:
        return []
    # 最近傍でチェーン化：端(最も外側)から貪欲に最近未訪問へ
    remaining = pts[:]
    # 始点＝x+yが最小の端子
    start = min(remaining, key=lambda t: t[1] + t[2])
    chain = [start]
    remaining.remove(start)
    while remaining:
        cx, cy = chain[-1][1], chain[-1][2]
        nxt = min(remaining, key=lambda t: math.hypot(t[1] - cx, t[2] - cy))
        chain.append(nxt)
        remaining.remove(nxt)
    return [(chain[i][0], chain[i + 1][0]) for i in range(len(chain) - 1)]
