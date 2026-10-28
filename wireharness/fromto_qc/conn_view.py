# -*- coding: utf-8 -*-
"""シーケンス図1枚の結線を『連結ネット』で色分けした検査図を描く。

目的(茂泉様方針): 3人目(From-To)の検証を図面上で行う。
  ・実配線(線分)を tracer._build_components の連結成分(=等電位ネット)で色分け。
    成分はスナップ・交差(接続ドット)処理済み・電源レール(L_MAIN)込みなので、
    「全端子が必ずどれかのネット(色)に繋がる」(宙に浮かない)。
  ・端子ドットは確度色(青=図面確定/黄土=型式推論/赤=未解決)。
  ・結線健全性チェック(2規約):
      規約A 全端子がネットに乗る     → 乗らない端子はマゼンタ枠(floating)。
      規約B 全線端が端子に届く       → 端子にも接続ドットにも届かない線端を×で指摘。
        ・別ネットの線に近い線端 = トレース隙間(要マージ) …… マゼンタ×
        ・孤立した線端           = 作図ギャップ/要確認      …… 橙×
    接続ドット(_crossPoint1)に繋がる線端は正常(ジャンクション)として指摘しない。

使い方:
  from fromto_qc import conn_view
  stats = conn_view.render('<製番>-F001.DXF', 'out.png')          # 1枚全体
  conn_view.render('x.DXF', 'out.png', crop=(0,0.5,0.5,1.0))      # 左上ゾーン拡大
  # CLI: python -m fromto_qc.conn_view <DXF> <OUT.png> [fx0 fy0 fx1 fy1]
戻り(stats): {'nets','floating','trace_gap','isolated'}。
"""
import collections

import ezdxf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Circle

from . import review_viewer as rv
from .tracer import _build_components, _pt_seg, TOL, CROSS_DOT

_HALO = [pe.withStroke(linewidth=2.2, foreground='white')]
_TCOL = {'drawn': '#1f6fe0', 'inferred': '#c8960a', 'unknown': '#e02424', 'external': '#9aa0a6'}
_PALETTE = (list(plt.get_cmap('tab20').colors) + list(plt.get_cmap('tab20b').colors)
            + list(plt.get_cmap('tab20c').colors) + list(plt.get_cmap('Set1').colors)
            + list(plt.get_cmap('Dark2').colors))


def _ascii(dev, term):
    """ラベルをASCII安全に(matplotlibで和字はtofuになるため)。不明端子は'?'。"""
    dv = ''.join(c for c in str(dev).split('-')[0] if ord(c) < 128)
    tm = ''.join(c for c in str(term) if ord(c) < 128) or '?'
    return f'{dv}:{tm}' if dv else tm


def render(dxf_path, out_path, crop=None, dpi=175):
    """1シートの結線検査図を out_path(PNG)に描く。crop=(fx0,fy0,fx1,fy1)は
    内容bboxに対する割合(0=左/下)でゾーン拡大。戻り: stats dict。"""
    d = rv.extract(dxf_path)
    c = _build_components(dxf_path)
    segs, find = c['segs'], c['find']
    comp_of_seg = [find(i) for i in range(len(segs))]

    # 成分bbox → 枠(図面枠/タイトル枠=縦横とも大)判定
    span = {}
    for i, (p1, p2) in enumerate(segs):
        b = span.setdefault(comp_of_seg[i], [1e18, 1e18, -1e18, -1e18])
        b[0] = min(b[0], p1[0], p2[0]); b[1] = min(b[1], p1[1], p2[1])
        b[2] = max(b[2], p1[0], p2[0]); b[3] = max(b[3], p1[1], p2[1])

    xs = [t[2] for t in d['terminals']]; ys = [t[3] for t in d['terminals']]
    for (p1, p2) in segs:
        xs += [p1[0], p2[0]]; ys += [p1[1], p2[1]]
    if not xs:
        xs, ys = [0, 1], [0, 1]
    mx, Mx, my, My = min(xs), max(xs), min(ys), max(ys)
    pad = 0.04 * max(Mx - mx, My - my, 1)
    mx -= pad; Mx += pad; my -= pad; My += pad
    FW, FH = Mx - mx, My - my
    is_frame = {cc: (b[2] - b[0] > 0.6 * FW and b[3] - b[1] > 0.6 * FH) for cc, b in span.items()}
    nonframe = [cc for cc in sorted(span, key=str) if not is_frame[cc]]
    col_of = {cc: _PALETTE[(i * 7) % len(_PALETTE)] for i, cc in enumerate(nonframe)}

    if crop:
        fx0, fy0, fx1, fy1 = crop
        mx, Mx, my, My = mx + fx0 * FW, mx + fx1 * FW, my + fy0 * FH, my + fy1 * FH
    W, H = Mx - mx, My - my
    LF = 9.0 if crop else 6.0
    RF = 48 if crop else 44
    R = max(W, H) / 150

    def _in(x, y):
        return mx <= x <= Mx and my <= y <= My

    fig, ax = plt.subplots(figsize=(min(39, W / RF + 2), min(39, H / RF + 2)), dpi=dpi)

    # 線分: 成分色(枠は淡色)
    for i, (p1, p2) in enumerate(segs):
        if not (_in(*p1) or _in(*p2)):
            continue
        cc = comp_of_seg[i]
        if is_frame[cc]:
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color='#e4e4e4', lw=0.7, zorder=1)
        else:
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=col_of[cc], lw=2.2, alpha=.9,
                    zorder=2, solid_capstyle='round')

    def comp_near(x, y, tol=TOL * 2.5):
        best, bd = None, tol
        for i, (a, b) in enumerate(segs):
            dd = _pt_seg((x, y), a, b)
            if dd < bd:
                bd, best = dd, i
        return comp_of_seg[best] if best is not None else None

    # 端子ドット＋ラベル。どのネットにも乗らない=floating(規約A違反)
    floating = 0
    for dev, term, x, y, tier in d['terminals']:
        if not _in(x, y):
            continue
        cc = comp_near(x, y)
        ax.add_patch(Circle((x, y), R, facecolor=_TCOL.get(tier, '#e02424'), ec='white', lw=0.6, zorder=5))
        if cc is None or is_frame.get(cc):
            ax.add_patch(Circle((x, y), R * 1.9, fill=False, ec='#d400aa', lw=1.8, zorder=6))
            floating += 1
        ax.text(x + R * 1.4, y + R * 1.1, _ascii(dev, term), fontsize=LF, color='#1a1a1a',
                zorder=7, clip_on=True, path_effects=_HALO)

    # 規約B: 線端(次数1)が端子/接続ドットに届くか
    def nk(p):
        return (round(p[0] / TOL), round(p[1] / TOL))
    deg = collections.Counter(); npos = {}; comp_of_node = {}
    for i, (p1, p2) in enumerate(segs):
        if is_frame[comp_of_seg[i]]:
            continue
        for p in (p1, p2):
            k = nk(p); deg[k] += 1; npos[k] = p; comp_of_node[k] = comp_of_seg[i]
    terms_xy = [(t[2], t[3]) for t in d['terminals']]
    dots_xy = [(ins.dxf.insert.x, ins.dxf.insert.y)
               for ins in ezdxf.readfile(dxf_path).modelspace().query('INSERT')
               if ins.dxf.name == CROSS_DOT]

    def near_any(x, y, pts, tol=TOL * 2.5):
        return any(abs(x - px) <= tol and abs(y - py) <= tol for px, py in pts)

    def other_comp_near(x, y, myc, tol=TOL * 4):
        for i, (p1, p2) in enumerate(segs):
            if is_frame[comp_of_seg[i]] or comp_of_seg[i] == myc:
                continue
            if _pt_seg((x, y), p1, p2) <= tol:
                return True
        return False

    trace_gap = isolated = 0
    for k, g in deg.items():
        if g != 1:
            continue
        x, y = npos[k]
        if not _in(x, y) or near_any(x, y, terms_xy) or near_any(x, y, dots_xy):
            continue
        if other_comp_near(x, y, comp_of_node[k]):
            ax.plot(x, y, marker='x', ms=11, mec='#d400aa', mew=2.6, zorder=8)
            trace_gap += 1
        else:
            ax.plot(x, y, marker='x', ms=11, mec='#ff8800', mew=2.6, zorder=8)
            isolated += 1

    leg = (f"Connectivity by net (color = one connected net)\n"
           f"nets={len(nonframe)}  floating terminals={floating}\n"
           f"magenta X = wire end near another net (trace gap, mergeable)={trace_gap}\n"
           f"orange X  = wire end isolated (drawing gap / check)={isolated}\n"
           f"dot: blue drawn / ochre infer / red unknown")
    ax.text(mx + 0.02 * W, My - 0.005 * H, leg, fontsize=9 if crop else 7, weight='bold',
            family='monospace', va='top', bbox=dict(boxstyle='round', fc='white', ec='#888'), zorder=9)
    ax.set_xlim(mx, Mx); ax.set_ylim(my, My); ax.set_aspect('equal'); ax.axis('off')
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return {'nets': len(nonframe), 'floating': floating,
            'trace_gap': trace_gap, 'isolated': isolated}


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("usage: python -m fromto_qc.conn_view <DXF> <OUT.png> [fx0 fy0 fx1 fy1]")
        sys.exit(1)
    cr = tuple(float(v) for v in sys.argv[3:7]) if len(sys.argv) >= 7 else None
    s = render(sys.argv[1], sys.argv[2], crop=cr)
    print(f"wrote {sys.argv[2]} | " + " ".join(f"{k}={v}" for k, v in s.items()))
