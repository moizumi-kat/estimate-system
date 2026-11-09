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

try:
    from . import harness_learn as _hl
except Exception:
    _hl = None

_POWER = {'赤': '#e23b3b', '白': '#9aa0a6', '青': '#2554d8', '緑': '#1a9a3a'}


def _term_ok(dev, term):
    """学習済み端子ルールで、その機器種別にその端子が実在するか。
    ルール未学習(語彙が空)の機器種別は判定不能=Noneを返し、誤って要確認にしない(誤答ゼロ)。"""
    if _hl is None:
        return None
    try:
        vocab = _hl.terminal_vocab(dev)
    except Exception:
        return None
    if not vocab:
        return None
    return str(term).strip() in vocab


def _esc(s):
    return _html.escape(str(s if s is not None else ''))


def _sym(dev, no):
    return f'{dev}-{no}' if no else str(dev)


def _geom_dn(ep):
    """図面上の位置照合に使う (device, no)。
    ロケーター置換済みの端点は 'actual'(実機器=図面の記号)で引く。無ければ表示値。"""
    a = ep.get('actual')
    if a:
        d, _, n = str(a).rpartition('-')
        return (d, n) if d else (str(a), '')
    return ep.get('device', ''), ep.get('no', '')


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
    # 成分ごとの線分(ラベルの有無に関わらず全成分)。無名成分(主回路の遮断器間配線など)も
    # ハイライトできるよう、号線ラベルが無ければ成分IDをキーにする。
    comp_segs = {}
    for i in range(len(segs)):
        comp_segs.setdefault(find(i), []).append(segs[i])
    for comp in set(comp_segs) | set(comp_terms):
        gkey = comp2g.get(comp) or f'C@{comp}'
        for (d, t, x, y) in comp_terms.get(comp, ()):
            by_dt.setdefault((str(d), str(t)), (x, y))
            by_d.setdefault(str(d), (x, y))
            ep_to_g.setdefault((str(d), str(t)), gkey)
            ep_to_g.setdefault(str(d), gkey)
        if comp_segs.get(comp):
            g_segs[gkey] = comp_segs[comp]
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

    wires = []            # list行データ(JS/表示用)
    overlays = [[] for _ in sheets]   # シートごとのSVG断片
    # 端子番号ラベル: 「その線が機器のどの端子に接続されるか」を From-To データから
    # 各接続点に記載する。同一接続点(同一端子)は1ラベルに集約し、接続する線番号を
    # data-w(空白区切り)で保持(線を選ぶとその端子が強調される)。
    # 位置キー → {'t':端子, 'dev':機器, 'ok':ルール判定(True/False/None), 'ws':set(線番)}
    term_pos = [dict() for _ in sheets]
    no_pos = 0
    for i, w in enumerate(routed.get('wires', [])):
        frm, to = w['from'], w['to']
        gousen = w.get('gousen', '')
        col = _POWER.get(w.get('color'), '')
        placed = False
        for si, sh in enumerate(sheets):
            r, dt, d, ep2g, gseg = sh[1], sh[2], sh[3], sh[4], sh[5]
            xmin, ymax = r['xmin'], r['ymax']
            mr = max(4.0, round(max(r['w'], r['h']) / 300.0, 1))
            # この線(From-To)が属する成分キー → その成分の実配線線分をハイライト対象にする
            fdev, fno = _geom_dn(frm)
            tdev, tno = _geom_dn(to)
            cg = (_endpoint_g(ep2g, fdev, fno, frm.get('terminal', '')) or
                  _endpoint_g(ep2g, tdev, tno, to.get('terminal', '')))
            segs = gseg.get(cg, []) if cg else []
            fp = _endpoint_xy(dt, d, fdev, fno, frm.get('terminal', ''))
            tp = _endpoint_xy(dt, d, tdev, tno, to.get('terminal', ''))
            if not segs and not (fp or tp):
                continue
            placed = True
            for (p1, p2) in segs:
                x1, y1 = round(p1[0] - xmin, 1), round(ymax - p1[1], 1)
                x2, y2 = round(p2[0] - xmin, 1), round(ymax - p2[1], 1)
                overlays[si].append(
                    f'<line class="wln" data-w="{i}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>')
            # 端点マーカー＋端子番号(どちらの端点がどの端子か)
            for p_, ep in ((fp, frm), (tp, to)):
                if not p_:
                    continue
                X, Y = round(p_[0] - xmin, 1), round(ymax - p_[1], 1)
                overlays[si].append(
                    f'<circle class="mk" data-w="{i}" cx="{X}" cy="{Y}" r="{mr}"/>')
                term = str(ep.get('terminal', '') or '').strip()
                if not term or term == '?':
                    continue
                dev = _sym(ep.get('device', ''), ep.get('no', ''))
                key = (X, Y, term)
                cell = term_pos[si].get(key)
                if cell is None:
                    cell = {'t': term, 'dev': dev, 'ok': _term_ok(ep.get('device', ''), term),
                            'ws': set()}
                    term_pos[si][key] = cell
                cell['ws'].add(i)
        if not placed:
            no_pos += 1
        wires.append({
            'i': i, 'g': gousen, 'col': col,
            'type': (w.get('color') or '') + (w.get('kind') or ''), 'size': w.get('size', ''),
            'frm': (frm.get('place', ''), _sym(frm.get('device', ''), frm.get('no', '')), frm.get('terminal', '')),
            'to': (to.get('place', ''), _sym(to.get('device', ''), to.get('no', '')), to.get('terminal', '')),
            'len': w.get('length', ''), 'placed': placed,
        })

    # 端子番号ラベルをSVG断片に変換(接続点ごとに1個)。
    # 表示する番号は From-To(図面トレース)の実接続端子=真値なので、ここでは矛盾指摘(△)は行わない。
    # (学習ルール terminal_vocab は、図面に端子記載が無い箇所を補完する用途で別途使用する。
    #  機器記号の流儀差 例: OL⟺51=熱動継電器 を吸収しないと誤検出になるため、検図側で安全に扱う)
    term_ov = [[] for _ in sheets]
    warn_terms = 0
    for si in range(len(sheets)):
        for (X, Y, term), cell in term_pos[si].items():
            ws = ' '.join(str(x) for x in sorted(cell['ws']))
            term_ov[si].append(
                f'<text class="tl" data-w="{ws}" x="{X + 2}" y="{Y - 2}">{_esc(term)}</text>')

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
                 f'<g class="ov">{"".join(overlays[si])}</g>'
                 f'<g class="ov-term">{"".join(term_ov[si])}</g></svg></div></div>')

    total = len(wires)
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_esc(seiban)} 確認図面</title>
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;background:#eef1f5;color:#16233c;font-family:"Noto Sans JP","Yu Gothic",sans-serif;font-size:12.5px}}
 header{{background:#1f4fb0;color:#fff;padding:8px 14px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;position:sticky;top:0;z-index:6}}
 header .ttl{{font-size:15px;font-weight:700}} header a{{color:#cfe0ff}}
 .cov{{font-weight:700}} .cov b{{font-size:15px}}
 .wt{{background:#8a3b00;color:#ffd9b0;padding:2px 8px;border-radius:10px;font-weight:700}} .wt b{{color:#fff}}
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
 .ov-term .tl{{fill:#6a1b9a;font-size:7px;font-weight:700;font-family:sans-serif;paint-order:stroke;
   stroke:#fff;stroke-width:1.8px;stroke-linejoin:round}}
 .ov-term .tl.warn{{fill:#d35400}}            /* ルール外端子=要確認(△) */
 .ov-term .tl.sel{{fill:#ff3b00;font-size:10px;stroke-width:2px}}   /* 選択線の端子を強調 */
 .ov-term .tl.done{{fill:#13a53a}}
 /* 端子番号: 「表示」OFF時は選択線の端子だけ出す。ONで全接続点に常時表示 */
 body:not(.showterm) .ov-term .tl:not(.sel){{display:none}}
 .btn{{font:inherit;border:1px solid #ccd3de;background:#fff;border-radius:7px;padding:4px 10px;cursor:pointer}}
</style></head>
<body>
<header><span class="ttl">確認図面 {_esc(seiban)}</span>
 <span class="cov">確認済 <b id="cdone">0</b> / {total} 本<span id="cnopos"></span></span>
 <label><input type="checkbox" id="only"> 未確認のみ</label>
 <label><input type="checkbox" id="showterm" checked> 端子番号を表示</label>
 {('<span class="wt">△ ルール外端子 <b>' + str(warn_terms) + '</b> 箇所（要確認）</span>') if warn_terms else ''}
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
function tlAll(w){{return document.querySelectorAll('.ov-term .tl[data-w~="'+w+'"]');}}
function rowOf(w){{return document.querySelector('#tb tr[data-w="'+w+'"]');}}
function applyDone(w){{var on=!!done[w];var r=rowOf(w);if(r){{r.classList.toggle('done',on);var c=r.querySelector('.cf');if(c)c.checked=on;}}
  mkAll(w).forEach(function(el){{el.classList.toggle('done',on);}});
  tlAll(w).forEach(function(el){{el.classList.toggle('done',on);}});}}
function recount(){{var n=Object.keys(done).filter(function(k){{return done[k];}}).length;document.getElementById('cdone').textContent=n;}}
var sel=null;
function select(w){{
  if(sel!==null){{mkAll(sel).forEach(function(el){{el.classList.remove('sel');}});
    tlAll(sel).forEach(function(el){{el.classList.remove('sel');}});
    var pr=rowOf(sel);if(pr)pr.classList.remove('sel');}}
  sel=w;var els=mkAll(w);els.forEach(function(el){{el.classList.add('sel');}});
  tlAll(w).forEach(function(el){{el.classList.add('sel');}});
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
document.body.classList.toggle('showterm',document.getElementById('showterm').checked);
document.querySelectorAll('#tb tr[data-w]').forEach(function(tr){{applyDone(tr.getAttribute('data-w'));}});
recount();
var np={no_pos};if(np>0)document.getElementById('cnopos').textContent='（位置未特定 '+np+'本）';
</script></body></html>'''


def write(routed, seq_paths, skel_paths, path, seiban=''):
    html = build_coverage_html(routed, seq_paths, skel_paths, seiban=seiban)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path
