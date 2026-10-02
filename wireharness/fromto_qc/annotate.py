# -*- coding: utf-8 -*-
"""確認図面（From-To↔図面の相互ハイライト／網羅確認ビュー）。

目的(茂泉様):
  生成した From-To リストの各行(=1本の線)をクリックすると、その線の端点が
  スケルトン図・シーケンス図の両方で図面上にハイライトされる。
  「確認済み」にすると色が変わり、全ての線が図面上で拾えているかを目視で網羅確認できる。

仕組み:
  ・図面は dxf_svg でSVG描画(座標変換 X=x-xmin, Y=ymax-y)。
  ・各シートで tracer から (機器-番号, 端子)→(x,y) の位置辞書を作る。
  ・routed の各線の from/to 端点(device+no:terminal)を各シートで座標照合し、
    マーカー(円)と接続線を data-wire=行番号 で重ねる。
  ・クリックで選択ハイライト、チェックで確認済み(緑)。確認状態は localStorage に保存。
"""
import os
import json
import html as _html

from . import dxf_svg, tracer

_POWER = {'赤': '#e23b3b', '白': '#9aa0a6', '青': '#2554d8', '緑': '#1a9a3a'}


def _esc(s):
    return _html.escape(str(s if s is not None else ''))


def _sym(dev, no):
    return f'{dev}-{no}' if no else str(dev)


def _sheet_index(path):
    """シートの位置辞書: {(機器-番号, 端子):(x,y)} と {機器-番号:(x,y)}。"""
    by_dt, by_d = {}, {}
    try:
        nodes = tracer.trace_nodes(path)
    except Exception:
        return by_dt, by_d
    for g, nd in nodes.items():
        for mem in nd.get('members', []):
            try:
                d, t, x, y = mem
            except Exception:
                continue
            by_dt.setdefault((str(d), str(t)), (x, y))
            by_d.setdefault(str(d), (x, y))
    return by_dt, by_d


def _endpoint_xy(idx_dt, idx_d, dev, no, term):
    sym = _sym(dev, no)
    return (idx_dt.get((sym, str(term))) or idx_d.get(sym))


def _endpoint_g(ep_to_g, dev, no, term):
    sym = _sym(dev, no)
    return ep_to_g.get((sym, str(term))) or ep_to_g.get(sym)


def _sheet_graph(path):
    """シート → (位置辞書 dt,d / 端点→号線 ep_to_g / 号線→線分 g_segs)。
    号線(等電位ノード)に属する実際の配線線分を取り出し、線のハイライトに使う。"""
    by_dt, by_d, ep_to_g, g_segs = {}, {}, {}, {}
    try:
        from . import tracer as T
        c = T._build_components(path)
        m, segs, find = c['m'], c['segs'], c['find']
        comp_terms = c['comp_terms']
        assign = T._assign_gousen(m, segs, find)
    except Exception:
        return by_dt, by_d, ep_to_g, g_segs
    comp2g = {}
    for g, comps in assign.items():
        for comp in comps:
            comp2g[comp] = str(g)
    for comp, terms in comp_terms.items():
        g = comp2g.get(comp)
        for (d, t, x, y) in terms:
            by_dt.setdefault((str(d), str(t)), (x, y))
            by_d.setdefault(str(d), (x, y))
            if g:
                ep_to_g.setdefault((str(d), str(t)), g)
                ep_to_g.setdefault(str(d), g)
    comp_segs = {}
    for i in range(len(segs)):
        comp_segs.setdefault(find(i), []).append(i)
    for g, comps in assign.items():
        lst = []
        for comp in comps:
            for i in comp_segs.get(comp, []):
                lst.append(segs[i])
        g_segs[str(g)] = lst
    return by_dt, by_d, ep_to_g, g_segs


def build_coverage_html(routed, seq_paths, skel_paths, seiban=''):
    sheets = []   # [name, render, dt, d, ep_to_g, g_segs]
    for p in list(skel_paths or []) + list(seq_paths or []):
        try:
            r = dxf_svg.render([p])
        except Exception:
            continue
        dt, d, ep2g, gseg = _sheet_graph(p)
        sheets.append([os.path.basename(p), r, dt, d, ep2g, gseg])

    # 端子番号ラベル(各機器の接続点に端子番号を図面上に記載。トグルで表示)
    term_ov = [[] for _ in sheets]
    for si, sh in enumerate(sheets):
        r, dt = sh[1], sh[2]
        xmin, ymax = r['xmin'], r['ymax']
        for (sym, term), (x, y) in dt.items():
            t = str(term).strip()
            if not t or t == '?':
                continue
            term_ov[si].append(
                f'<text class="tl" x="{round(x - xmin, 1) + 2}" y="{round(ymax - y, 1) - 2}">{_esc(t)}</text>')

    wires = []            # list行データ(JS/表示用)
    overlays = [[] for _ in sheets]   # シートごとのSVG断片
    no_pos = 0
    for i, w in enumerate(routed.get('wires', [])):
        frm, to = w['from'], w['to']
        col = _POWER.get(w.get('color'), '')
        placed = False
        for si, sh in enumerate(sheets):
            r, dt, d, ep2g, gseg = sh[1], sh[2], sh[3], sh[4], sh[5]
            xmin, ymax = r['xmin'], r['ymax']
            mr = max(4.0, round(max(r['w'], r['h']) / 300.0, 1))
            # この線(From-To)が属する号線 → その号線の実配線線分をハイライト対象にする
            g = (_endpoint_g(ep2g, frm.get('device', ''), frm.get('no', ''), frm.get('terminal', '')) or
                 _endpoint_g(ep2g, to.get('device', ''), to.get('no', ''), to.get('terminal', '')))
            segs = gseg.get(g, []) if g else []
            fp = _endpoint_xy(dt, d, frm.get('device', ''), frm.get('no', ''), frm.get('terminal', ''))
            tp = _endpoint_xy(dt, d, to.get('device', ''), to.get('no', ''), to.get('terminal', ''))
            if not segs and not (fp or tp):
                continue
            placed = True
            for (p1, p2) in segs:
                x1, y1 = round(p1[0] - xmin, 1), round(ymax - p1[1], 1)
                x2, y2 = round(p2[0] - xmin, 1), round(ymax - p2[1], 1)
                overlays[si].append(
                    f'<line class="wln" data-w="{i}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>')
            for p_ in (fp, tp):
                if p_:
                    overlays[si].append(
                        f'<circle class="mk" data-w="{i}" cx="{round(p_[0] - xmin, 1)}" '
                        f'cy="{round(ymax - p_[1], 1)}" r="{mr}"/>')
        if not placed:
            no_pos += 1
        wires.append({
            'i': i, 'g': g, 'col': col,
            'type': (w.get('color') or '') + (w.get('kind') or ''), 'size': w.get('size', ''),
            'frm': (frm.get('place', ''), _sym(frm.get('device', ''), frm.get('no', '')), frm.get('terminal', '')),
            'to': (to.get('place', ''), _sym(to.get('device', ''), to.get('no', '')), to.get('terminal', '')),
            'len': w.get('length', ''), 'placed': placed,
        })

    # 表(From-To)
    trows = ''
    for w in wires:
        fp = f"{w['frm'][1]}" + (f":{w['frm'][2]}" if w['frm'][2] else '')
        tp = f"{w['to'][1]}" + (f":{w['to'][2]}" if w['to'][2] else '')
        warn = '' if w['placed'] else ' title="図面上に位置が見つかりません(要確認)"'
        dot = f'<span class="cdot" style="background:{w["col"] or "#8a93a0"}"></span>'
        trows += (f'<tr data-w="{w["i"]}"{warn} class="{"nopos" if not w["placed"] else ""}">'
                  f'<td class="chk"><input type="checkbox" class="cf" data-w="{w["i"]}"></td>'
                  f'<td class="num">{w["i"]+1}</td><td>{dot}{_esc(w["g"])}</td>'
                  f'<td>{_esc(w["type"])}{_esc(w["size"])}</td>'
                  f'<td>{_esc(fp)}</td><td>{_esc(tp)}</td>'
                  f'<td class="num">{_esc(w["len"])}</td>'
                  f'<td class="pos">{"" if w["placed"] else "位置?"}</td></tr>')

    # 図面(SVG＋オーバーレイ)
    draw = ''
    for si, sh in enumerate(sheets):
        name, r = sh[0], sh[1]
        draw += (f'<div class="sheet"><div class="sh">{_esc(name)}</div>'
                 f'<div class="svgbox"><svg viewBox="0 0 {r["w"]} {r["h"]}" preserveAspectRatio="xMidYMid meet">'
                 f'<g class="dwg">{r["body"]}</g>'
                 f'<g class="ov-term">{"".join(term_ov[si])}</g>'
                 f'<g class="ov">{"".join(overlays[si])}</g></svg></div></div>')

    total = len(wires)
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_esc(seiban)} 確認図面</title>
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;background:#eef1f5;color:#16233c;font-family:"Noto Sans JP","Yu Gothic",sans-serif;font-size:12.5px}}
 header{{background:#1f4fb0;color:#fff;padding:8px 14px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;position:sticky;top:0;z-index:6}}
 header .ttl{{font-size:15px;font-weight:700}} header a{{color:#cfe0ff}}
 .cov{{font-weight:700}} .cov b{{font-size:15px}}
 .main{{display:flex;gap:10px;align-items:flex-start;padding:10px;height:calc(100vh - 48px)}}
 .left{{flex:0 0 46%;max-width:46%;display:flex;flex-direction:column;height:100%}}
 .right{{flex:1;height:100%;overflow:auto}}
 .bar{{display:flex;gap:10px;align-items:center;margin-bottom:6px;flex-wrap:wrap}}
 .listbox{{overflow:auto;background:#fff;border:1px solid #ccd3de;border-radius:8px;flex:1}}
 table{{border-collapse:collapse;width:100%}} th,td{{border-bottom:1px solid #eef1f5;padding:4px 6px;text-align:left;white-space:nowrap}}
 th{{position:sticky;top:0;background:#eef1f6;z-index:1}} td.num{{text-align:right}} td.chk{{text-align:center}}
 tbody tr{{cursor:pointer}} tbody tr:hover{{background:#f2f6ff}}
 tr.sel{{background:#fff3df !important;outline:2px solid #e0a800;outline-offset:-2px}}
 tr.done{{background:#eaf7ee;color:#2b6b43}} tr.nopos{{color:#b24}}
 .cdot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle}}
 .sheet{{background:#fff;border:1px solid #ccd3de;border-radius:8px;margin-bottom:10px}}
 .sheet .sh{{padding:5px 8px;font-weight:700;border-bottom:1px solid #eef1f5}}
 .svgbox{{overflow:auto}} svg{{width:100%;height:auto;min-width:900px;background:#fff}}
 .dwg line,.dwg path,.dwg circle{{fill:none;stroke:#334;stroke-width:0.4;vector-effect:non-scaling-stroke}}
 .dwg .wm{{stroke:#c0392b}} .dwg .we{{stroke:#1a9a3a}} .dwg .wc{{stroke:#666}} .dwg .gm{{stroke:#aab4c2}}
 .dwg .sol{{fill:#334;stroke:none}} .dwg text{{fill:#333;font-family:sans-serif}}
 /* ハイライトは「配線ライン」が主役。既定は非表示、選択=太い橙、確認=緑の太線を図面に残す */
 .ov .wln{{fill:none;stroke-width:3;vector-effect:non-scaling-stroke;display:none;stroke-linecap:round;stroke-linejoin:round}}
 .ov .wln.done{{display:inline;stroke:#13a53a;stroke-width:4.5;opacity:.95}}
 .ov .wln.sel{{display:inline;stroke:#ff3b00;stroke-width:8;opacity:1}}
 /* 端点は補助的に小さく表示 */
 .ov .mk{{fill:#8a93a0;fill-opacity:.25;stroke:none}}
 .ov .mk.done{{fill:#13a53a;fill-opacity:.7}} .ov .mk.sel{{fill:#ff5a00;fill-opacity:.9}}
 .ov-term .tl{{fill:#6a1b9a;font-size:6px;font-family:sans-serif;paint-order:stroke;
   stroke:#fff;stroke-width:1.4px;stroke-linejoin:round}}
 body:not(.showterm) .ov-term{{display:none}}
 .btn{{font:inherit;border:1px solid #ccd3de;background:#fff;border-radius:7px;padding:4px 10px;cursor:pointer}}
</style></head>
<body>
<header><span class="ttl">確認図面 {_esc(seiban)}</span>
 <span class="cov">確認済 <b id="cdone">0</b> / {total} 本<span id="cnopos"></span></span>
 <label><input type="checkbox" id="only"> 未確認のみ</label>
 <label><input type="checkbox" id="showterm"> 端子番号を表示</label>
 <button class="btn" id="clear">確認をクリア</button>
 <span style="flex:1"></span><a href="/">← メニュー</a></header>
<div class="main">
 <div class="left"><div class="bar muted">行をクリック→図面でハイライト。チェックで確認済(緑)。</div>
  <div class="listbox"><table><thead><tr><th>✓</th><th>#</th><th>号線</th><th>種/ｻｲｽﾞ</th><th>From</th><th>To</th><th>測長</th><th></th></tr></thead>
  <tbody id="tb">{trows}</tbody></table></div></div>
 <div class="right">{draw}</div>
</div>
<script>
var SEIBAN={json.dumps(seiban, ensure_ascii=False)}, KEY='harnesschk:'+SEIBAN, TOTAL={total};
var done={{}};
try{{done=JSON.parse(localStorage.getItem(KEY)||'{{}}')||{{}};}}catch(e){{done={{}};}}
function mkAll(w){{return document.querySelectorAll('.ov [data-w="'+w+'"]');}}
function rowOf(w){{return document.querySelector('#tb tr[data-w="'+w+'"]');}}
function applyDone(w){{var on=!!done[w];var r=rowOf(w);if(r){{r.classList.toggle('done',on);var c=r.querySelector('.cf');if(c)c.checked=on;}}
  mkAll(w).forEach(function(el){{el.classList.toggle('done',on);}});}}
function recount(){{var n=Object.keys(done).filter(function(k){{return done[k];}}).length;document.getElementById('cdone').textContent=n;}}
var sel=null;
function select(w){{
  if(sel!==null){{mkAll(sel).forEach(function(el){{el.classList.remove('sel');}});var pr=rowOf(sel);if(pr)pr.classList.remove('sel');}}
  sel=w;var els=mkAll(w);els.forEach(function(el){{el.classList.add('sel');}});
  var r=rowOf(w);if(r)r.classList.add('sel');
  if(els[0]){{els[0].scrollIntoView({{block:'center',inline:'center',behavior:'smooth'}});}}
}}
document.getElementById('tb').addEventListener('click',function(e){{
  var tr=e.target.closest('tr[data-w]');if(!tr)return;var w=tr.getAttribute('data-w');
  if(e.target.classList.contains('cf')){{done[w]=e.target.checked;if(!done[w])delete done[w];
    try{{localStorage.setItem(KEY,JSON.stringify(done));}}catch(_){{}}
    applyDone(w);recount();return;}}
  select(w);
}});
document.getElementById('showterm').addEventListener('change',function(){{
  document.body.classList.toggle('showterm',this.checked);}});
document.getElementById('only').addEventListener('change',function(){{
  var on=this.checked;document.querySelectorAll('#tb tr[data-w]').forEach(function(tr){{
    var w=tr.getAttribute('data-w');tr.style.display=(on&&done[w])?'none':'';}});
}});
document.getElementById('clear').addEventListener('click',function(){{
  if(!confirm('確認状態を全てクリアします。よろしいですか？'))return;
  done={{}};try{{localStorage.removeItem(KEY);}}catch(_){{}}
  document.querySelectorAll('#tb tr[data-w]').forEach(function(tr){{applyDone(tr.getAttribute('data-w'));}});
  recount();
}});
// 初期反映
document.querySelectorAll('#tb tr[data-w]').forEach(function(tr){{applyDone(tr.getAttribute('data-w'));}});
recount();
var np={no_pos};if(np>0)document.getElementById('cnopos').textContent='（位置未特定 '+np+'本）';
</script></body></html>'''


def write(routed, seq_paths, skel_paths, path, seiban=''):
    html = build_coverage_html(routed, seq_paths, skel_paths, seiban=seiban)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path
