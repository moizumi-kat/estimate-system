# -*- coding: utf-8 -*-
"""
DXF構造モデル → Vision入力用の「きれいなタイル画像」を描き起こす。

生スキャンを読むより、DXFから端子ピン・号線・機器記号を強調して描いた画像の方が
Visionの誤読が桁違いに少ない。回路ブロック/号線クラスタ単位でタイル化する。
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def _bounds(model):
    xs = [p[0] for s in model.segments for p in s]
    ys = [p[1] for s in model.segments for p in s]
    return (min(xs), max(xs), min(ys), max(ys)) if xs else (0, 1, 0, 1)


def tile_grid(model, cols=2, rows=2, pad=100):
    """図面を cols×rows のタイル矩形に分割して返す（(x0,x1,y0,y1) のリスト）。"""
    x0, x1, y0, y1 = _bounds(model)
    x0 -= pad; x1 += pad; y0 -= pad; y1 += pad
    dx = (x1 - x0) / cols
    dy = (y1 - y0) / rows
    tiles = []
    for r in range(rows):
        for c in range(cols):
            tiles.append((x0 + c * dx, x0 + (c + 1) * dx, y0 + r * dy, y0 + (r + 1) * dy))
    return tiles


def render_region(model, region, out_path, dpi=115, show_symbols=True):
    """指定矩形 region=(X0,X1,Y0,Y1) を描画してPNG保存。"""
    X0, X1, Y0, Y1 = region

    def inbox(x, y):
        return X0 <= x <= X1 and Y0 <= y <= Y1

    fig, ax = plt.subplots(figsize=(16, 12))

    def dl(x1, y1, x2, y2, col, lw):
        if inbox(x1, y1) or inbox(x2, y2):
            ax.plot([x1, x2], [y1, y2], '-', color=col, lw=lw)

    for e in model.msp:
        lay = e.dxf.layer
        if e.dxftype() == 'LINE':
            if lay.startswith('L_'):
                dl(e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y, '#111', 1.4)
            elif show_symbols and lay == 'SYM':
                dl(e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y, '#999', 0.8)
        elif e.dxftype() == 'LWPOLYLINE':
            pts = [(x, y) for x, y, *_ in e.get_points()]
            col = '#111' if lay.startswith('L_') else ('#999' if (show_symbols and lay in ('SYM', 'DIAGRAM')) else None)
            if col:
                for p, q in zip(pts, pts[1:]):
                    dl(p[0], p[1], q[0], q[1], col, 1.4 if col == '#111' else 0.8)
        elif e.dxftype() == 'INSERT' and show_symbols:
            try:
                for ve in e.virtual_entities():
                    if ve.dxftype() == 'LINE' and ve.dxf.layer in ('SYM', 'L_CONTROL', 'L_MAIN', 'DENSEN'):
                        dl(ve.dxf.start.x, ve.dxf.start.y, ve.dxf.end.x, ve.dxf.end.y, '#999', 0.8)
                    elif ve.dxftype() == 'CIRCLE' and inbox(ve.dxf.center.x, ve.dxf.center.y):
                        ax.add_patch(plt.Circle((ve.dxf.center.x, ve.dxf.center.y), ve.dxf.radius,
                                                fill=False, color='#999', lw=0.8))
            except Exception:
                pass
    for t in model.terminals:
        if inbox(t.x, t.y):
            ax.plot(t.x, t.y, 'o', color='#c0504d', ms=7)
    for v, x, y in model.senban:
        if inbox(x, y):
            ax.text(x, y, v, fontsize=11, color='#1f5a8a', ha='center', va='center',
                    bbox=dict(boxstyle='round,pad=0.15', fc='#eaf1f7', ec='#9db4c7'))
    for d in model.devices:
        if inbox(d.x, d.y):
            ax.text(d.x, d.y - 40, d.sym, fontsize=11, color='#123b5e', ha='center', fontweight='bold')
    ax.set_xlim(X0, X1)
    ax.set_ylim(Y0, Y1)
    ax.set_aspect('equal')
    ax.axis('off')
    plt.tight_layout()
    plt.savefig(out_path, dpi=dpi, facecolor='white')
    plt.close(fig)
    return out_path
