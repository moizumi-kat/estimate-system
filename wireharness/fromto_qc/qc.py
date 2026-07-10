# -*- coding: utf-8 -*-
"""
製造前 電気図面 QCチェック（連結健全性）。

DrawingModel を受け取り、製造に回す前に人が直すべき結線の異常を洗い出す。
判定は決定論的（幾何ベース）。Vision結果との突合で更に精度を上げられる（vision.py）。

検出項目:
  - floating_end : 端子にも他線にも繋がらない自由端（浮き電線）
  - snap_gap     : 端子ピンに近いが乗っていない端点（0.5〜15mm・自動補正候補）
  - net_no_senban: 号線(線番)が付いていないネット
  - lonely_net   : 端子/機器が1つしか繋がらないネット（結線の描き忘れ疑い）
"""
import collections
from .geometry import pt_seg_dist


def _degree_map(segs, tol=1.0):
    def q(p):
        return (round(p[0] / tol) * tol, round(p[1] / tol) * tol)
    deg = collections.Counter()
    for a, b in segs:
        deg[q(a)] += 1
        deg[q(b)] += 1
    return deg, q


def check(model, snap_tol=15.0):
    segs = model.segments
    pins = [(t.x, t.y) for t in model.terminals]
    deg, q = _degree_map(segs)
    issues = []

    # 自由端(次数1) を検査
    free = [p for p, d in deg.items() if d == 1]
    for (x, y) in free:
        # 他線分の途中に乗っていれば結線OK（T字）
        on_seg = any(
            (a != (x, y) and b != (x, y) and pt_seg_dist(x, y, a[0], a[1], b[0], b[1])[0] <= 1.0
             and 0.02 < pt_seg_dist(x, y, a[0], a[1], b[0], b[1])[1] < 0.98)
            for a, b in segs)
        if on_seg:
            continue
        dp = min((abs(x - px) + abs(y - py) for px, py in pins), default=1e9)
        dp_euc = min((((x - px) ** 2 + (y - py) ** 2) ** 0.5 for px, py in pins), default=1e9)
        if dp_euc <= 0.5:
            continue                          # ピンにスナップ済み=OK
        elif dp_euc <= snap_tol:
            issues.append({'type': 'snap_gap', 'x': x, 'y': y, 'gap_mm': round(dp_euc, 1)})
        else:
            issues.append({'type': 'floating_end', 'x': x, 'y': y, 'nearest_pin_mm': round(dp_euc, 1)})

    # 号線なし / 孤立ネット
    for n in model.nets:
        deg_count = len(set(t.device for t in n['terminals']) | set(n['devices']))
        if deg_count < 2:
            issues.append({'type': 'lonely_net', 'center': n['center'], 'devices': n['devices']})
        if not n['id']:
            issues.append({'type': 'net_no_senban', 'center': n['center'], 'devices': n['devices']})
    return issues


def summarize(issues):
    c = collections.Counter(i['type'] for i in issues)
    return dict(c)
