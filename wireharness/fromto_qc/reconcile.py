# -*- coding: utf-8 -*-
"""照合ゲート（開発・検証フェーズ用）。

人手ハーネスデータ(.txt) から作った From-To（＝答え合わせの基準）と、
アドオンが図面から生成した From-To（CSV）を突き合わせ、
差分を「質問・修正候補」として提示する。

運用（茂泉様の方針）:
  1) 人手ハーネス → From-To（基準）
  2) アドオン From-To（図面から）
  3) 差が出たら → ゲートが質問／修正候補を提示
  4) 図面を直して差が消えれば → その図面は「100%読める」＝内容検図・ハーネス生成へ

対象は設計・論理レベル（機器to機器）。製造要因（渡り/中継/コネクタ）は
「余剰」側に出るが誤りとは限らないため、その旨を添えて提示する。
"""
import csv
import itertools
from .compare import parse_human
from .geometry import norm

# 名称ゆらぎの対応表（図面↔人手）。運用で追記可能。
DEFAULT_ALIAS = {
    '切102': '3102', '入102': '3102',   # 扉ツマミセレクタ 3-102
    'A': 'ALX1',                          # 警報補助リレー ALX-1
    'B': '86X102',                        # 回路102補助リレー 86X-102
}
DROP_DEVICES = {'LUG'}                    # 製造端子（LUG）は論理照合から除外


def amap(sym, alias):
    s = norm(sym)
    return alias.get(s, s)


def parse_addon_csv(path, encoding='cp932'):
    """アドオン出力 From-To CSV（号線,サイズ,機器記号,機器番号,端子番号）を読む。"""
    rows = []
    with open(path, encoding=encoding, errors='replace', newline='') as f:
        r = csv.reader(f)
        for i, c in enumerate(r):
            if not c or (i == 0 and c and c[0].strip() in ('号線', '﻿号線')):
                continue
            c = (c + ['', '', '', '', ''])[:5]
            rows.append({'gousen': c[0].strip(), 'size': c[1].strip(),
                         'dev': c[2].strip(), 'no': c[3].strip(), 'term': c[4].strip()})
    return rows


def _edges_from_devsets(devsets):
    """号線ごとの機器集合 → 機器to機器エッジ集合。"""
    E = set()
    for ds in devsets:
        for a, b in itertools.combinations(sorted(ds), 2):
            E.add((a, b))
    return E


def human_edges(nets, alias):
    devsets = []
    for n in nets:
        ds = {amap(e[0] + e[1], alias) for e in n['ends'] if e[0]}
        ds = {d for d in ds if d and d not in DROP_DEVICES}
        if len(ds) >= 2:
            devsets.append(ds)
    return _edges_from_devsets(devsets)


def addon_edges(rows, alias):
    from collections import defaultdict
    g = defaultdict(set)
    for r in rows:
        d = amap(r['dev'] + r['no'], alias)
        if d and d not in DROP_DEVICES:
            g[r['gousen']].add(d)
    return _edges_from_devsets(g.values())


def reconcile(addon_rows, human_path, alias=None, draw_devices=None):
    """人手基準とアドオン結果を突合し、質問・修正候補つきの結果を返す。

    draw_devices: 図面に実在する全機器（記号+番号の文字列 or (記号,番号)タプル）の一覧。
        欠品（図面に機器が無い）と未接続（機器は在るが結線が読めない）を正しく
        切り分けるために必須。アドオンの機器一覧出力（<base>_devices.csv）を渡す。
        None の場合は From-To 行から代用するが、結線に乗らなかった機器を誤って
        「欠品」と判定しうるため非推奨（レポートに注意を出す）。
    """
    alias = dict(DEFAULT_ALIAS if alias is None else alias)
    nets = parse_human(human_path)

    H = human_edges(nets, alias)
    A = addon_edges(addon_rows, alias)

    # 図面に実在する機器の集合（欠品判定の基準）
    dev_from_rows_only = draw_devices is None
    if draw_devices is None:
        src = [r['dev'] + r['no'] for r in addon_rows]
    else:
        src = [(x if isinstance(x, str) else (x[0] + x[1])) for x in draw_devices]
    draw_dev = {amap(s, alias) for s in src}
    draw_dev = {d for d in draw_dev if d and d not in DROP_DEVICES}
    # 人手に登場する機器の集合
    human_dev = set()
    for n in nets:
        for e in n['ends']:
            if e[0]:
                d = amap(e[0] + e[1], alias)
                if d and d not in DROP_DEVICES:
                    human_dev.add(d)

    match = H & A
    missing = H - A          # 人手にあってアドオンに無い＝図面から読めていない
    extra = A - H            # アドオンにあって人手に無い＝製造要因 or 誤配線

    findings = []
    for a, b in sorted(missing):
        absent = [d for d in (a, b) if d not in draw_dev]
        if absent:
            findings.append({
                'type': 'missing', 'cause': '欠品',
                'edge': (a, b),
                'question': f"接続 {a}―{b} が図面から読めません。",
                'candidate': f"機器 {', '.join(absent)} が図面にありません。追加してください。"})
        else:
            findings.append({
                'type': 'missing', 'cause': '未接続/号線未付与',
                'edge': (a, b),
                'question': f"接続 {a}―{b} が図面から読めません。",
                'candidate': (f"{a} と {b} に同じ号線を付けたか、電線端が端子に届いているか"
                              f"確認してください（号線ラベルを両端子のそばに配置）。")})
    for a, b in sorted(extra):
        findings.append({
            'type': 'extra', 'cause': '製造要因/要確認',
            'edge': (a, b),
            'question': f"図面には接続 {a}―{b} がありますが、人手ハーネスにありません。",
            'candidate': ("渡り/中継/コネクタ等の製造要因なら正常です。"
                          "そうでなければ誤配線の可能性を確認してください。")})

    pct = round(len(match) / len(H) * 100, 1) if H else 100.0
    verdict = 'PASS' if not missing else 'FIX'
    return {
        'verdict': verdict,
        'human': len(H), 'addon': len(A), 'match': len(match),
        'recall': pct,
        'precision': round(len(match) / len(A) * 100, 1) if A else 0.0,
        'missing': sorted(missing), 'extra': sorted(extra),
        'findings': findings,
        'dev_from_rows_only': dev_from_rows_only,
    }


def format_report(res):
    """照合ゲートのレポート文字列を返す。"""
    L = []
    L.append("=== From-To 照合ゲート（人手基準 vs アドオン）===")
    v = "◎ 合格（人手基準を100%再現）" if res['verdict'] == 'PASS' \
        else "× 要修正（下記の質問・修正候補を確認してください）"
    L.append(f"判定: {v}")
    L.append(f"基準エッジ={res['human']}  アドオン={res['addon']}  "
             f"一致={res['match']}  整合率={res['recall']}%  適合率={res['precision']}%")
    if res.get('dev_from_rows_only'):
        L.append("※注意: 図面の機器一覧が未指定のため、欠品/未接続の切り分けが不正確です"
                 "（アドオンの機器一覧 <base>_devices.csv を渡してください）。")
    L.append("")
    miss = [f for f in res['findings'] if f['type'] == 'missing']
    extra = [f for f in res['findings'] if f['type'] == 'extra']
    if miss:
        L.append(f"― 図面から読めていない接続（{len(miss)}件）＝100%阻害要因 ―")
        for f in miss:
            L.append(f"[{f['cause']}] {f['question']}")
            L.append(f"    → 修正候補: {f['candidate']}")
    if extra:
        L.append("")
        L.append(f"― 図面にあるが人手に無い接続（{len(extra)}件）＝製造要因の可能性 ―")
        for f in extra:
            L.append(f"[{f['cause']}] {f['question']}")
            L.append(f"    → {f['candidate']}")
    return "\n".join(L)
