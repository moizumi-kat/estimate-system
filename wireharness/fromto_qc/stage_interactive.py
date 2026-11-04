# -*- coding: utf-8 -*-
"""図面上で確認できる『インタラクティブ』確認UI(自己完結HTML)。

一覧の行をクリックすると、図面上の該当機器・該当配線をハイライトして示す。座標→ピクセル
変換で図面(base64内蔵PNG)に正確に重ねる。社内資産は外部に出さずローカルで確認する。

  locator_confirm(layout_paths, seiban, out_html, basis_of=None):
      ロケーター工程。どの機器にロケーターを採用し、図面上でどの名前かを確認(機器↔図面)。
  stage1_wires(skel_paths, seiban, out_html):
      1人目(太物)工程。各配線が『どこからどこへ』繋がるかを一本ずつ図面上に表示。
"""
import io
import html as _html
import base64
import json

import ezdxf

from . import locator_apply as LAP, locator_assign as LA, edge_trace


def _render_base(dxf_path, pts, margin_frac=0.06, w_in=24.0, dpi=130):
    """dxf を pts の外接範囲(＋余白)で描画し、(data_uri, Wpx, Hpx, extent) を返す。
    extent=(x0,y0,x1,y1)。座標→ピクセル: px=(x-x0)/(x1-x0)*Wpx, py=(1-(y-y0)/(y1-y0))*Hpx。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.font_manager as fm
    try:
        fm.fontManager.addfont('/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf')
        matplotlib.rcParams['font.family'] = 'IPAGothic'
    except Exception:
        pass
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing import config as C

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx = (max(xs) - min(xs)) * margin_frac + 120
    my = (max(ys) - min(ys)) * margin_frac + 120
    x0, x1 = min(xs) - mx, max(xs) + mx
    y0, y1 = min(ys) - my, max(ys) + my
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    Frontend(RenderContext(doc), MatplotlibBackend(ax),
             config=C.Configuration(color_policy=C.ColorPolicy.BLACK)).draw_layout(msp, finalize=True)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect('equal')
    ax.set_axis_off()
    fig.set_size_inches(w_in, w_in * (y1 - y0) / (x1 - x0))
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, facecolor='white')
    plt.close(fig)
    buf.seek(0)
    from PIL import Image
    w_px, h_px = Image.open(buf).size
    uri = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    return uri, w_px, h_px, (x0, y0, x1, y1)


def _to_px(x, y, extent, w_px, h_px):
    x0, y0, x1, y1 = extent
    return round((x - x0) / (x1 - x0) * w_px, 1), round((1 - (y - y0) / (y1 - y0)) * h_px, 1)


def locator_confirm(layout_paths, seiban, out_html, basis_of=None):
    """ロケーター確認UI(インタラクティブ)。戻り: 採番件数。"""
    rows = LAP.locator_rows(layout_paths, basis_of)
    pts = [(r[2], r[3]) for r in rows]
    uri, w, h, ext = _render_base(layout_paths[0], pts)
    items = []
    for loc, sym, x, y, t in rows:
        px, py = _to_px(x, y, ext, w, h)
        items.append({'loc': loc, 'sym': sym, 'px': px, 'py': py})
    _write_marker_html(out_html, seiban, 'ロケーター確認', uri, w, h, items,
                       cols=['loc', 'sym'], headers=['ロケーター', '機器'],
                       note='どの機器にロケーターを採用し、図面上でどの名前になったかの確認。'
                            '行をクリックすると図面上に表示します。')
    return len(items)


def stage1_wires(skel_paths, seiban, out_html):
    """1人目(太物)の配線を一本ずつ図面上に表示するUI。戻り: 配線本数。
    各Fシート(スケルトン)ごとに edge_trace で端点座標付きの配線を取り、from→to を線で描く。"""
    sheets = []
    total = 0
    for p in skel_paths:
        name = _basename(p)
        res = edge_trace.trace_edges(p, with_pos=True)
        edges = res[2] if isinstance(res, tuple) and len(res) >= 3 else []
        pts = []
        for e in edges:
            (d1, t1, x1, y1), (d2, t2, x2, y2) = e[0], e[1]
            pts.append((x1, y1))
            pts.append((x2, y2))
        if not pts:
            continue
        uri, w, h, ext = _render_base(p, pts)
        wires = []
        for e in edges:
            (d1, t1, x1, y1), (d2, t2, x2, y2) = e[0], e[1]
            if abs(x1 - x2) + abs(y1 - y2) < 1:
                continue      # 同点(端子内)の自己ループは除外
            ax_, ay_ = _to_px(x1, y1, ext, w, h)
            bx_, by_ = _to_px(x2, y2, ext, w, h)
            wires.append({'a': f'{d1}:{t1}', 'b': f'{d2}:{t2}',
                          'ax': ax_, 'ay': ay_, 'bx': bx_, 'by': by_})
        total += len(wires)
        sheets.append({'name': name, 'uri': uri, 'w': w, 'h': h, 'wires': wires})
    _write_wire_html(out_html, seiban, sheets)
    return total


# ---- HTML ビルダ ----
def _basename(p):
    import os
    return os.path.splitext(os.path.basename(p))[0]


def _esc(x):
    return _html.escape(str(x))


_CSS = """body{font-family:IPAGothic,"Yu Gothic",sans-serif;margin:0;background:#f4f4f5;font-size:14px;color:#1a1a1a}
.hd{padding:8px 14px;border-bottom:2px solid #1a1a1a;background:#fff}
.hd h1{font-size:17px;margin:2px 0}.hd .n{font-size:12px;color:#555}
.wrap{display:flex;gap:12px;padding:12px}
.L{flex:0 0 300px;max-height:90vh;overflow:auto;background:#fff;border-radius:6px;padding:6px}
.R{flex:1;background:#fff;border-radius:6px;overflow:auto;max-height:90vh}
.box{position:relative}.box img{display:block}
svg.ov{position:absolute;left:0;top:0;pointer-events:none}
table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{border-bottom:1px solid #eee;padding:3px 7px;text-align:left}
th{position:sticky;top:0;background:#efefef}
tbody tr{cursor:pointer} tbody tr.on td{background:#ffe3cc}
.lc{font-weight:bold;color:#c2410c;white-space:nowrap} .ar{color:#999;text-align:center}
.sh{font-weight:bold;margin:6px 4px 2px}"""


def _write_marker_html(out_html, seiban, subtitle, uri, w, h, items, cols, headers, note):
    body_rows = ''.join(
        f'<tr data-i="{i}">' + ''.join(f'<td class="{"lc" if c=="loc" else ""}">{_esc(d[c])}</td>' for c in cols) + '</tr>'
        for i, d in enumerate(items))
    head = ''.join(f'<th>{_esc(hh)}</th>' for hh in headers)
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} {_esc(subtitle)}</title><style>{_CSS}</style></head><body>
<div class=hd><h1>{_esc(seiban)}　{_esc(subtitle)}</h1><div class=n>機器 {len(items)}件。{_esc(note)}</div></div>
<div class=wrap>
 <div class=L><table><thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table></div>
 <div class=R><div class=box style="width:{w}px"><img src="{uri}" width="{w}" height="{h}">
   <svg class=ov viewBox="0 0 {w} {h}" width="{w}" height="{h}"></svg></div></div>
</div>
<script>
const items={json.dumps(items, ensure_ascii=False)};
const ov=document.querySelector('.ov');
function draw(sel){{let s='';items.forEach((d,i)=>{{const on=i===sel;
 s+=`<circle cx="${{d.px}}" cy="${{d.py}}" r="${{on?26:12}}" fill="none" stroke="${{on?'#ff2d00':'#e65100'}}" stroke-width="${{on?5:2}}"/>`;
 if(on)s+=`<text x="${{d.px}}" y="${{d.py-32}}" fill="#ff2d00" font-size="32" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">${{d.loc||''}}</text>`;}});ov.innerHTML=s;}}
draw(-1);
document.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=()=>{{
 document.querySelectorAll('tbody tr').forEach(t=>t.classList.remove('on'));tr.classList.add('on');
 const i=+tr.dataset.i;draw(i);const d=items[i];
 document.querySelector('.R').scrollTo({{left:d.px-320,top:d.py-260,behavior:'smooth'}});}});
</script></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)


def _write_wire_html(out_html, seiban, sheets):
    total = sum(len(s['wires']) for s in sheets)
    # シートセレクタ＋各シートの一覧/図
    secs = []
    for si, s in enumerate(sheets):
        rows = ''.join(
            f'<tr data-i="{i}"><td>{_esc(w["a"])}</td><td class=ar>→</td><td>{_esc(w["b"])}</td></tr>'
            for i, w in enumerate(s['wires']))
        secs.append(f"""<section data-sheet="{si}" style="display:{'block' if si==0 else 'none'}">
<div class=wrap>
 <div class=L><div class=sh>{_esc(s['name'])}（配線 {len(s['wires'])}本）</div>
  <table><thead><tr><th>From</th><th></th><th>To</th></tr></thead><tbody>{rows}</tbody></table></div>
 <div class=R><div class=box style="width:{s['w']}px"><img src="{s['uri']}" width="{s['w']}" height="{s['h']}">
   <svg class=ov viewBox="0 0 {s['w']} {s['h']}" width="{s['w']}" height="{s['h']}"></svg></div></div>
</div></section>""")
    tabs = ''.join(f'<button data-t="{si}" class="{"on" if si==0 else ""}">{_esc(s["name"])}</button>'
                   for si, s in enumerate(sheets))
    data = {si: s['wires'] for si, s in enumerate(sheets)}
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} 1人目 太物配線 確認</title><style>{_CSS}
.tabs{{padding:6px 12px;background:#fff}} .tabs button{{margin-right:6px;padding:4px 10px;border:1px solid #ccc;background:#f3f3f3;border-radius:5px;cursor:pointer}}
.tabs button.on{{background:#1a1a1a;color:#fff;border-color:#1a1a1a}}</style></head><body>
<div class=hd><h1>{_esc(seiban)}　1人目 太物配線 確認（一本ずつ図面表示）</h1>
<div class=n>配線 {total}本。行をクリックすると、その配線の From→To を図面上に線で表示します。</div></div>
<div class=tabs>{tabs}</div>
{''.join(secs)}
<script>
const DATA={json.dumps(data, ensure_ascii=False)};
function ov(si){{return document.querySelector(`section[data-sheet="${{si}}"] .ov`);}}
function drawAll(si){{const ws=DATA[si];let s='';ws.forEach(w=>{{
 s+=`<line x1="${{w.ax}}" y1="${{w.ay}}" x2="${{w.bx}}" y2="${{w.by}}" stroke="#9cc3ff" stroke-width="2"/>`;}});ov(si).innerHTML=s;}}
function sel(si,i){{const ws=DATA[si];let s='';ws.forEach((w,j)=>{{const on=j===i;
 s+=`<line x1="${{w.ax}}" y1="${{w.ay}}" x2="${{w.bx}}" y2="${{w.by}}" stroke="${{on?'#ff2d00':'#cfe0fb'}}" stroke-width="${{on?6:1.6}}"/>`;}});
 const w=ws[i];
 s+=`<circle cx="${{w.ax}}" cy="${{w.ay}}" r="11" fill="#2a7a2a"/><circle cx="${{w.bx}}" cy="${{w.by}}" r="11" fill="#c0392b"/>`;
 s+=`<text x="${{w.ax}}" y="${{w.ay-16}}" fill="#2a7a2a" font-size="30" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">From</text>`;
 s+=`<text x="${{w.bx}}" y="${{w.by-16}}" fill="#c0392b" font-size="30" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">To</text>`;
 ov(si).innerHTML=s;}}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{{
 document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 document.querySelectorAll('section').forEach(se=>se.style.display='none');
 const si=b.dataset.t;document.querySelector(`section[data-sheet="${{si}}"]`).style.display='block';drawAll(si);}});
document.querySelectorAll('section').forEach(se=>{{const si=se.dataset.sheet;drawAll(si);
 se.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=()=>{{
  se.querySelectorAll('tbody tr').forEach(t=>t.classList.remove('on'));tr.classList.add('on');
  const i=+tr.dataset.i;sel(si,i);const w=DATA[si][i];
  se.querySelector('.R').scrollTo({{left:(w.ax+w.bx)/2-320,top:(w.ay+w.by)/2-260,behavior:'smooth'}});}});}});
</script></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)
