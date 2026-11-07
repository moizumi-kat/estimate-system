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
from . import tracer
from .geometry import norm


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


def _edges_of(dxf_path):
    """edge_trace で『機器間の配線』(同点・同一機器内・重複を除去)を返す。
    戻り: [(adev, aterm, ax, ay, bdev, bterm, bx, by), ...]。太物ハーネスの一本一本。"""
    res = edge_trace.trace_edges(dxf_path, with_pos=True)
    edges = res[2] if isinstance(res, tuple) and len(res) >= 3 else []
    out, seen = [], set()
    for e in edges:
        (d1, t1, x1, y1), (d2, t2, x2, y2) = e[0], e[1]
        if abs(x1 - x2) + abs(y1 - y2) < 1 or str(d1) == str(d2):
            continue
        key = tuple(sorted([(str(d1), str(t1)), (str(d2), str(t2))]))
        if key in seen:
            continue
        seen.add(key)
        out.append((str(d1), str(t1), x1, y1, str(d2), str(t2), x2, y2))
    return out


def stage1_wires(skel_paths, seiban, out_html):
    """1人目(太物)の配線を一本ずつ図面上に表示・編集するUI。戻り: 配線本数。"""
    sheets = []
    total = 0
    for p in skel_paths:
        es = _edges_of(p)
        if not es:
            continue
        pts = [(e[2], e[3]) for e in es] + [(e[6], e[7]) for e in es]
        uri, w, h, ext = _render_base(p, pts)
        wires = []
        for (ad, at, ax, ay, bd, bt, bx, by) in es:
            ax_, ay_ = _to_px(ax, ay, ext, w, h)
            bx_, by_ = _to_px(bx, by, ext, w, h)
            wires.append({'a': f'{ad}:{at}', 'b': f'{bd}:{bt}',
                          'adev': ad, 'aterm': at, 'bdev': bd, 'bterm': bt,
                          'ax': ax_, 'ay': ay_, 'bx': bx_, 'by': by_})
        total += len(wires)
        sheets.append({'name': _basename(p), 'uri': uri, 'w': w, 'h': h, 'wires': wires})
    _write_wire_html(out_html, seiban, sheets)
    return total


def _split_dev(dev):
    if '-' in str(dev):
        d, n = str(dev).rsplit('-', 1)
        return d, n
    return str(dev), ''


def _is_tb_lug(dev, term):
    """端子番号未記入の端子台ラグ(DEVICE=TB・番号なし・端子なし/?)か。"""
    d, n = _split_dev(dev)
    return norm(d) == 'TB' and (not n) and (not term or term == '?')


def _terminals_by_sheet(seq_paths, skel_paths):
    """端子番号未記入の端子台ラグを『シートごと』に返す。仮番号は全体で一意採番。
    戻り: [(sheet_path, [{'prov','x','y','gousen':[...]}, ...]), ...]。
    (harness_sheet.build_sheet と同じ仮番号採番規則に揃える)"""
    paths = list(seq_paths) + list(skel_paths or [])
    per = {}
    gmembers = {}
    for p in paths:
        try:
            tn = tracer.trace_nodes(p)
        except Exception:
            tn = {}
        per[p] = tn
        for g, nd in tn.items():
            gmembers.setdefault(g, set()).update(nd['members'])
    lugs = set()
    for g, mem in gmembers.items():
        if str(g).startswith('M@'):
            continue
        for (dev, term, x, y) in mem:
            if _is_tb_lug(dev, term):
                lugs.add((round(x), round(y), norm(g)))
    tb_prov = {lug: f"仮{i + 1}" for i, lug in enumerate(sorted(lugs))}
    prov_gousen = {}
    for g, mem in gmembers.items():
        if str(g).startswith('M@'):
            continue
        for (dev, term, x, y) in mem:
            if _is_tb_lug(dev, term):
                prov = tb_prov.get((round(x), round(y), norm(g)))
                if prov:
                    prov_gousen.setdefault(prov, set()).add(g)
    sheets = []
    for p in paths:
        seen = {}
        for g, nd in per[p].items():
            if str(g).startswith('M@'):
                continue
            for (dev, term, x, y) in nd['members']:
                if _is_tb_lug(dev, term):
                    prov = tb_prov.get((round(x), round(y), norm(g)))
                    if prov and prov not in seen:
                        seen[prov] = {'prov': prov, 'x': x, 'y': y,
                                      'gousen': sorted(str(v) for v in prov_gousen.get(prov, []))}
        if seen:
            sheets.append((p, sorted(seen.values(),
                                     key=lambda t: int(str(t['prov']).replace('仮', '') or 0))))
    return sheets


def stage2_terminal_assign(seq_paths, skel_paths, seiban, out_html):
    """2人目(端子番号付与)の対話UI。端子台の未記入ラグを図面上に表示し、リストの行を
    クリックするとその端子位置を図面上でハイライトする。端子番号を入力し CSV 出力。
    戻り: 端子(仮番号)件数。"""
    sheets_data = []
    total = 0
    for p, terms in _terminals_by_sheet(seq_paths, skel_paths):
        pts = [(t['x'], t['y']) for t in terms]
        uri, w, h, ext = _render_base(p, pts)
        items = []
        for t in terms:
            px, py = _to_px(t['x'], t['y'], ext, w, h)
            items.append({'prov': t['prov'], 'gousen': '／'.join(t['gousen']),
                          'px': px, 'py': py})
        total += len(items)
        sheets_data.append({'name': _basename(p), 'uri': uri, 'w': w, 'h': h, 'items': items})
    _write_terminal_html(out_html, seiban, sheets_data)
    return total


def export_stage1_harness(skel_paths, seiban, out_csv):
    """1人目(太物)ハーネス情報を CSV 出力(サーバー側・無編集の基準出力)。
    列: シート, From機器, From端子, To機器, To端子, サイズ。戻り: 行数。
    UI(stage1_wires)の『ハーネスCSV出力』は、これに人手修正を反映したもの。"""
    import csv
    rows = []
    for p in skel_paths:
        name = _basename(p)
        for (ad, at, ax, ay, bd, bt, bx, by) in _edges_of(p):
            rows.append([name, ad, at, bd, bt, ''])
    with open(out_csv, 'w', encoding='cp932', errors='replace', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['シート', 'From機器', 'From端子', 'To機器', 'To端子', 'サイズ'])
        wr.writerows(rows)
    return len(rows)


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
    """機器マーカー確認＋編集UI。ロケーター/機器を修正でき、対象外も可。CSV出力付き。"""
    body_rows = ''.join(
        f'<tr data-i="{i}">'
        f'<td><input class=cloc value="{_esc(d["loc"])}"></td>'
        f'<td><input class=csym value="{_esc(d["sym"])}"></td>'
        f'<td class=ce><input type=checkbox class=ex></td></tr>'
        for i, d in enumerate(items))
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} {_esc(subtitle)}</title><style>{_CSS}
.L input{{border:1px solid #ddd;border-radius:3px;padding:2px 4px;font-size:12.5px;width:92%}}
.ce{{text-align:center}} tr.del input{{text-decoration:line-through;color:#999;background:#f6f6f6}}
.dl{{background:#1565c0;color:#fff;border:none;border-radius:5px;padding:5px 14px;cursor:pointer;margin-left:10px;font-size:13px}}</style></head><body>
<div class=hd><h1>{_esc(seiban)}　{_esc(subtitle)}
<button class=dl onclick="exportCSV()">ロケーターCSV出力（修正反映）</button></h1>
<div class=n>機器 {len(items)}件。{_esc(note)} <b>ロケーター/機器は直接編集でき、対象外にもできます。</b></div></div>
<div class=wrap>
 <div class=L><table><thead><tr><th>{_esc(headers[0])}</th><th>{_esc(headers[1])}</th><th>対象外</th></tr></thead>
   <tbody>{body_rows}</tbody></table></div>
 <div class=R><div class=box style="width:{w}px"><img src="{uri}" width="{w}" height="{h}">
   <svg class=ov viewBox="0 0 {w} {h}" width="{w}" height="{h}"></svg></div></div>
</div>
<script>
const items={json.dumps(items, ensure_ascii=False)};
const ov=document.querySelector('.ov');
function curLoc(i){{const tr=document.querySelector(`tr[data-i="${{i}}"]`);return tr?tr.querySelector('.cloc').value:items[i].loc;}}
function draw(sel){{let s='';items.forEach((d,i)=>{{const on=i===sel;
 s+=`<circle cx="${{d.px}}" cy="${{d.py}}" r="${{on?26:12}}" fill="none" stroke="${{on?'#ff2d00':'#e65100'}}" stroke-width="${{on?5:2}}"/>`;
 if(on)s+=`<text x="${{d.px}}" y="${{d.py-32}}" fill="#ff2d00" font-size="32" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">${{curLoc(i)||''}}</text>`;}});ov.innerHTML=s;}}
draw(-1);
document.querySelectorAll('tbody tr').forEach(tr=>{{
 tr.querySelector('.ex').onchange=e=>tr.classList.toggle('del',e.target.checked);
 tr.onclick=ev=>{{if(ev.target.tagName==='INPUT')return;
  document.querySelectorAll('tbody tr').forEach(t=>t.classList.remove('on'));tr.classList.add('on');
  const i=+tr.dataset.i;draw(i);const d=items[i];
  document.querySelector('.R').scrollTo({{left:d.px-320,top:d.py-260,behavior:'smooth'}});}};}});
function exportCSV(){{let out=[['ロケーター','機器']];
 document.querySelectorAll('tbody tr').forEach(tr=>{{if(tr.querySelector('.ex').checked)return;
  out.push([tr.querySelector('.cloc').value,tr.querySelector('.csym').value]);}});
 const csv='\\ufeff'+out.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\\r\\n');
 const b=new Blob([csv],{{type:'text/csv'}});const a=document.createElement('a');
 a.href=URL.createObjectURL(b);a.download='{_esc(seiban)}_ロケーター.csv';a.click();}}
</script></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)


def _write_terminal_html(out_html, seiban, sheets):
    """2人目(端子番号付与)の確認＋編集UI。端子台の未記入ラグを図面上に表示し、
    リストの行クリックで該当端子をハイライト。黄欄に端子番号を入力し CSV 出力。"""
    total = sum(len(s['items']) for s in sheets)
    secs = []
    for si, s in enumerate(sheets):
        rows = ''.join(
            f'<tr data-i="{i}">'
            f'<td class=lc>{_esc(it["prov"])}</td>'
            f'<td><input class=eg value="{_esc(it["gousen"])}"></td>'
            f'<td><input class=tn placeholder="端子番号を入力"></td>'
            f'<td class=ce><input type=checkbox class=ex></td></tr>'
            for i, it in enumerate(s['items']))
        secs.append(f"""<section data-sheet="{si}" style="display:{'block' if si == 0 else 'none'}">
<div class=wrap>
 <div class=L><div class=sh>{_esc(s['name'])}（端子 {len(s['items'])}）</div>
  <table><thead><tr><th>仮番号</th><th>接続号線</th><th>端子番号</th><th>除外</th></tr></thead>
  <tbody>{rows}</tbody></table></div>
 <div class=R><div class=box style="width:{s['w']}px"><img src="{s['uri']}" width="{s['w']}" height="{s['h']}">
   <svg class=ov viewBox="0 0 {s['w']} {s['h']}" width="{s['w']}" height="{s['h']}"></svg></div></div>
</div></section>""")
    tabs = ''.join(f'<button data-t="{si}" class="{"on" if si == 0 else ""}">{_esc(s["name"])}</button>'
                   for si, s in enumerate(sheets))
    data = {si: s['items'] for si, s in enumerate(sheets)}
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} 2人目 端子番号付与 確認・編集</title><style>{_CSS}
.tabs{{padding:6px 12px;background:#fff}} .tabs button{{margin-right:6px;padding:4px 10px;border:1px solid #ccc;background:#f3f3f3;border-radius:5px;cursor:pointer}}
.tabs button.on{{background:#1a1a1a;color:#fff;border-color:#1a1a1a}}
.L input{{border:1px solid #ddd;border-radius:3px;padding:2px 4px;font-size:12.5px;width:95%}}
.L input.tn{{background:#fffbe9}} .ce{{text-align:center}} tr.del input{{text-decoration:line-through;color:#999;background:#f6f6f6}}
.dl{{background:#1565c0;color:#fff;border:none;border-radius:5px;padding:5px 14px;cursor:pointer;margin-left:10px;font-size:13px}}</style></head><body>
<div class=hd><h1>{_esc(seiban)}　2人目 端子番号付与 確認・編集
<button class=dl onclick="exportCSV()">端子番号CSV出力（修正反映）</button></h1>
<div class=n>端子 {total}。行クリックで図面に該当端子を表示。<b>黄欄に端子番号を入力、接続号線は編集でき、誤りは「除外」。</b>修正後「端子番号CSV出力」で保存。</div></div>
<div class=tabs>{tabs}</div>
{''.join(secs)}
<script>
const DATA={json.dumps(data, ensure_ascii=False)};
function ov(si){{return document.querySelector(`section[data-sheet="${{si}}"] .ov`);}}
function drawAll(si){{let s='';DATA[si].forEach(t=>{{s+=`<circle cx="${{t.px}}" cy="${{t.py}}" r="12" fill="none" stroke="#e65100" stroke-width="2"/>`;}});ov(si).innerHTML=s;}}
function sel(si,i){{let s='';DATA[si].forEach((t,j)=>{{const on=j===i;
 s+=`<circle cx="${{t.px}}" cy="${{t.py}}" r="${{on?26:12}}" fill="none" stroke="${{on?'#ff2d00':'#e65100'}}" stroke-width="${{on?5:2}}"/>`;
 if(on)s+=`<text x="${{t.px}}" y="${{t.py-32}}" fill="#ff2d00" font-size="30" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">${{t.prov}}</text>`;}});ov(si).innerHTML=s;}}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{{
 const si=+b.dataset.t;document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 document.querySelectorAll('section[data-sheet]').forEach(se=>se.style.display=(+se.dataset.sheet===si)?'block':'none');}});
document.querySelectorAll('section[data-sheet]').forEach(se=>{{
 const si=+se.dataset.sheet;drawAll(si);
 se.querySelectorAll('tbody tr').forEach(tr=>{{
  tr.querySelector('.ex').onchange=e=>tr.classList.toggle('del',e.target.checked);
  tr.onclick=ev=>{{if(ev.target.tagName==='INPUT')return;
   se.querySelectorAll('tbody tr').forEach(t=>t.classList.remove('on'));tr.classList.add('on');
   const i=+tr.dataset.i;sel(si,i);const t=DATA[si][i];
   se.querySelector('.R').scrollTo({{left:t.px-320,top:t.py-260,behavior:'smooth'}});}};}});}});
function exportCSV(){{let out=[['シート','仮番号','接続号線','端子番号']];
 document.querySelectorAll('section[data-sheet]').forEach(se=>{{const nm=se.querySelector('.sh').textContent.replace(/（.*/,'');
  se.querySelectorAll('tbody tr').forEach(tr=>{{if(tr.querySelector('.ex').checked)return;
   out.push([nm,tr.querySelector('.lc').textContent,tr.querySelector('.eg').value,tr.querySelector('.tn').value]);}});}});
 const csv='\\ufeff'+out.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\\r\\n');
 const b=new Blob([csv],{{type:'text/csv'}});const a=document.createElement('a');
 a.href=URL.createObjectURL(b);a.download='{_esc(seiban)}_2人目端子番号.csv';a.click();}}
</script></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)


def _write_wire_html(out_html, seiban, sheets):
    """1人目(太物)配線の確認＋編集UI。From/To/サイズを修正でき、除外も可。
    『ハーネスCSV出力』で修正を反映した1人目ハーネス情報をダウンロードする。"""
    total = sum(len(s['wires']) for s in sheets)
    secs = []
    for si, s in enumerate(sheets):
        rows = ''.join(
            f'<tr data-i="{i}">'
            f'<td><input class=fa value="{_esc(w["a"])}"></td><td class=ar>→</td>'
            f'<td><input class=fb value="{_esc(w["b"])}"></td>'
            f'<td><input class=sz value="" placeholder="例 8sq" size=6></td>'
            f'<td class=ce><input type=checkbox class=ex></td></tr>'
            for i, w in enumerate(s['wires']))
        secs.append(f"""<section data-sheet="{si}" style="display:{'block' if si==0 else 'none'}">
<div class=wrap>
 <div class=L><div class=sh>{_esc(s['name'])}（配線 {len(s['wires'])}本）</div>
  <table><thead><tr><th>From</th><th></th><th>To</th><th>サイズ</th><th>除外</th></tr></thead>
  <tbody>{rows}</tbody></table></div>
 <div class=R><div class=box style="width:{s['w']}px"><img src="{s['uri']}" width="{s['w']}" height="{s['h']}">
   <svg class=ov viewBox="0 0 {s['w']} {s['h']}" width="{s['w']}" height="{s['h']}"></svg></div></div>
</div></section>""")
    tabs = ''.join(f'<button data-t="{si}" class="{"on" if si==0 else ""}">{_esc(s["name"])}</button>'
                   for si, s in enumerate(sheets))
    data = {si: s['wires'] for si, s in enumerate(sheets)}
    names = {si: s['name'] for si, s in enumerate(sheets)}
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} 1人目 太物配線 確認・編集</title><style>{_CSS}
.tabs{{padding:6px 12px;background:#fff}} .tabs button{{margin-right:6px;padding:4px 10px;border:1px solid #ccc;background:#f3f3f3;border-radius:5px;cursor:pointer}}
.tabs button.on{{background:#1a1a1a;color:#fff;border-color:#1a1a1a}}
.L input{{border:1px solid #ddd;border-radius:3px;padding:2px 4px;font-size:12.5px;width:95%}}
.L input.sz{{width:56px}} .ce{{text-align:center}} tr.del input{{text-decoration:line-through;color:#999;background:#f6f6f6}}
.dl{{background:#1565c0;color:#fff;border:none;border-radius:5px;padding:5px 14px;cursor:pointer;margin-left:10px;font-size:13px}}</style></head><body>
<div class=hd><h1>{_esc(seiban)}　1人目 太物配線 確認・編集
<button class=dl onclick="exportCSV()">ハーネスCSV出力（修正反映）</button></h1>
<div class=n>配線 {total}本。行クリックで図面に表示。<b>From/To/サイズは直接編集でき、誤りは「除外」で外せます。</b>修正後「ハーネスCSV出力」で1人目ハーネス情報を保存。</div></div>
<div class=tabs>{tabs}</div>
{''.join(secs)}
<script>
const DATA={json.dumps(data, ensure_ascii=False)};
const NAMES={json.dumps(names, ensure_ascii=False)};
function ov(si){{return document.querySelector(`section[data-sheet="${{si}}"] .ov`);}}
function drawAll(si){{let s='';DATA[si].forEach(w=>{{s+=`<line x1="${{w.ax}}" y1="${{w.ay}}" x2="${{w.bx}}" y2="${{w.by}}" stroke="#9cc3ff" stroke-width="2"/>`;}});ov(si).innerHTML=s;}}
function sel(si,i){{let s='';DATA[si].forEach((w,j)=>{{const on=j===i;
 s+=`<line x1="${{w.ax}}" y1="${{w.ay}}" x2="${{w.bx}}" y2="${{w.by}}" stroke="${{on?'#ff2d00':'#cfe0fb'}}" stroke-width="${{on?6:1.6}}"/>`;}});
 const w=DATA[si][i];
 s+=`<circle cx="${{w.ax}}" cy="${{w.ay}}" r="11" fill="#2a7a2a"/><circle cx="${{w.bx}}" cy="${{w.by}}" r="11" fill="#c0392b"/>`;
 s+=`<text x="${{w.ax}}" y="${{w.ay-16}}" fill="#2a7a2a" font-size="30" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">From</text>`;
 s+=`<text x="${{w.bx}}" y="${{w.by-16}}" fill="#c0392b" font-size="30" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">To</text>`;
 ov(si).innerHTML=s;}}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{{
 document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 document.querySelectorAll('section').forEach(se=>se.style.display='none');
 const si=b.dataset.t;document.querySelector(`section[data-sheet="${{si}}"]`).style.display='block';drawAll(si);}});
document.querySelectorAll('section').forEach(se=>{{const si=se.dataset.sheet;drawAll(si);
 se.querySelectorAll('tbody tr').forEach(tr=>{{
  tr.querySelector('.ex').onchange=e=>tr.classList.toggle('del',e.target.checked);
  tr.onclick=ev=>{{if(ev.target.tagName==='INPUT')return;
   se.querySelectorAll('tbody tr').forEach(t=>t.classList.remove('on'));tr.classList.add('on');
   const i=+tr.dataset.i;sel(si,i);const w=DATA[si][i];
   se.querySelector('.R').scrollTo({{left:(w.ax+w.bx)/2-320,top:(w.ay+w.by)/2-260,behavior:'smooth'}});}};}});}});
function splitFT(s){{const p=String(s).split(':');return p.length>1?[p.slice(0,-1).join(':'),p[p.length-1]]:[s,''];}}
function exportCSV(){{
 let out=[['シート','From機器','From端子','To機器','To端子','サイズ']];
 document.querySelectorAll('section').forEach(se=>{{const si=se.dataset.sheet;
  se.querySelectorAll('tbody tr').forEach(tr=>{{
   if(tr.querySelector('.ex').checked)return;
   const fa=splitFT(tr.querySelector('.fa').value), fb=splitFT(tr.querySelector('.fb').value);
   out.push([NAMES[si],fa[0],fa[1],fb[0],fb[1],tr.querySelector('.sz').value]);}});}});
 const csv='\\ufeff'+out.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\\r\\n');
 const b=new Blob([csv],{{type:'text/csv'}});const a=document.createElement('a');
 a.href=URL.createObjectURL(b);a.download='{_esc(seiban)}_1人目ハーネス.csv';a.click();}}
</script></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)
