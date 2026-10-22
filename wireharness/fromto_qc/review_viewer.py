# -*- coding: utf-8 -*-
"""FromToData を図面上で確認するためのレビュー画面(自己完結HTML)を生成する。

目的(ユーザー要望 2026-10-22):
  各From-To行が『図面のどこからどこへの配線か』を、図面を見ながらハイライト線で確認したい。

仕組み:
  ・シーケンス図(DXF)の線分(LINE/LWPOLYLINE)を薄いグレーで背景描画(=図面)。
  ・edge_trace.trace_edges(..., with_pos=True) の pos_edges は、同一シート内の端点物理座標
    (機器,端子,x,y)×2 を持つ。これを配線ごとのハイライト線として重ねる。
  ・右の一覧で行をクリック/ホバーすると、その配線の端点と線が図面上で強調される。
  図面データは社内資産のため claude.ai に公開せず、ローカルで開く1枚のHTMLに全データを埋め込む。

使い方:
  python3 -m fromto_qc.review_viewer <シートDXF> [出力HTML]
"""
import json
import os
import sys

import ezdxf

from . import edge_trace


def _segments(msp):
    """図面の線分を [[x1,y1,x2,y2], ...] で返す(背景描画用)。"""
    segs = []
    for e in msp.query('LINE'):
        segs.append([e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y])
    for e in msp.query('LWPOLYLINE'):
        pts = [(p[0], p[1]) for p in e.get_points()]
        closed = e.closed
        for i in range(len(pts) - 1):
            segs.append([pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]])
        if closed and len(pts) > 2:
            segs.append([pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]])
    return segs


def _device_labels(msp):
    """機器INSERTの (DEVICE[-DEVICE1], x, y) を返す(図面の向き確認の目印用)。"""
    out = []
    for ins in msp.query('INSERT'):
        if not ins.attribs:
            continue
        a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
        dev = a.get('DEVICE', '')
        if not dev:
            continue
        num = a.get('DEVICE1', '')
        label = f'{dev}-{num}' if num else dev
        out.append([label, round(ins.dxf.insert.x, 1), round(ins.dxf.insert.y, 1)])
    return out


def extract(sheet_path):
    """1シートの図面geometryと配線(pos_edges)を抽出して dict で返す。"""
    doc = ezdxf.readfile(sheet_path)
    msp = doc.modelspace()
    segs = _segments(msp)
    _, _, pos = edge_trace.trace_edges(sheet_path, with_pos=True)
    edges = []
    for (da, ta, xa, ya), (db, tb, xb, yb) in pos:
        edges.append({
            'from': f'{da}:{ta}', 'to': f'{db}:{tb}',
            'ax': xa, 'ay': ya, 'bx': xb, 'by': yb,
        })
    # bbox(線分・端点・機器を含む)
    xs, ys = [], []
    for s in segs:
        xs += [s[0], s[2]]; ys += [s[1], s[3]]
    for e in edges:
        xs += [e['ax'], e['bx']]; ys += [e['ay'], e['by']]
    if not xs:
        xs, ys = [0, 1], [0, 1]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    return {
        'sheet': os.path.basename(sheet_path),
        'segments': segs,
        'devices': _device_labels(msp),
        'edges': edges,
        'bbox': bbox,
    }


def build_html(data):
    """抽出データ → 自己完結HTML文字列。"""
    payload = json.dumps(data, ensure_ascii=False)
    return _TEMPLATE.replace('/*__DATA__*/', payload)


def write_html(sheet_path, out_path=None):
    data = extract(sheet_path)
    if out_path is None:
        out_path = os.path.splitext(sheet_path)[0] + '_確認.html'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(build_html(data))
    return out_path, len(data['edges'])


_TEMPLATE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>配線確認</title>
<style>
  :root{--bg:#ffffff;--fg:#1a1a1a;--line:#c8c8c8;--panel:#f4f4f6;--border:#dddde2;
        --hl:#e4002b;--hldev:#0066cc;--okbg:#e8f5e9;--sel:#fff3cd;}
  @media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
        --bg:#14151a;--fg:#e8e8ea;--line:#3a3b42;--panel:#1d1e25;--border:#2c2d35;
        --hl:#ff5a76;--hldev:#5aaaff;--okbg:#1f3a24;--sel:#4a3f1a;}}
  :root[data-theme="dark"]{--bg:#14151a;--fg:#e8e8ea;--line:#3a3b42;--panel:#1d1e25;
        --border:#2c2d35;--hl:#ff5a76;--hldev:#5aaaff;--okbg:#1f3a24;--sel:#4a3f1a;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 system-ui,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif;}
  header{padding:8px 14px;border-bottom:1px solid var(--border);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
  header b{font-size:14px}
  .wrap{display:flex;height:calc(100vh - 46px)}
  .view{flex:1 1 60%;min-width:0;position:relative;overflow:hidden;background:var(--bg)}
  svg{width:100%;height:100%;cursor:grab}
  svg.drag{cursor:grabbing}
  .seg{stroke:var(--line);stroke-width:1;vector-effect:non-scaling-stroke}
  .hl{stroke:var(--hl);stroke-width:3;vector-effect:non-scaling-stroke;stroke-linecap:round}
  .hlpt{fill:none;stroke:var(--hl);stroke-width:2.5;vector-effect:non-scaling-stroke}
  .devlbl{fill:var(--hldev);font-size:11px;font-weight:600}
  .devdot{fill:var(--hldev)}
  .list{flex:0 0 40%;max-width:520px;overflow:auto;border-left:1px solid var(--border);background:var(--panel)}
  table{border-collapse:collapse;width:100%}
  th,td{padding:5px 8px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
  th{position:sticky;top:0;background:var(--panel);z-index:1}
  tr.row{cursor:pointer}
  tr.row:hover{background:var(--sel)}
  tr.sel{background:var(--sel);outline:2px solid var(--hl)}
  tr.ok td.c{background:var(--okbg)}
  .c{width:34px;text-align:center;cursor:pointer;font-weight:700}
  .hint{opacity:.7;font-size:12px}
  button{font:inherit;padding:3px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--fg);cursor:pointer}
</style>
</head>
<body>
<header>
  <b id="title">配線確認</b>
  <span class="hint">行をクリック→図面上で配線をハイライト / 左端セルで OK 記録 / ホイールで拡大・ドラッグで移動</span>
  <button id="reset">全体表示</button>
  <span class="hint" id="count"></span>
</header>
<div class="wrap">
  <div class="view">
    <svg id="svg"><g id="scene">
      <g id="segs"></g><g id="hl"></g><g id="devs"></g>
    </g></svg>
  </div>
  <div class="list">
    <table><thead><tr><th class="c">✓</th><th>#</th><th>From</th><th>To</th></tr></thead>
    <tbody id="tb"></tbody></table>
  </div>
</div>
<script>
const DATA = /*__DATA__*/;
const [minX,minY,maxX,maxY] = DATA.bbox;
const W = Math.max(1,maxX-minX), H = Math.max(1,maxY-minY), PAD = 0.04*Math.max(W,H);
// DXFはy上向き。SVGはy下向き→ y' = (maxY - y) で反転。
const fy = y => (maxY - y);
const svg = document.getElementById('svg');
const scene = document.getElementById('scene');
svg.setAttribute('viewBox', `${minX-PAD} ${fy(maxY)-PAD} ${W+2*PAD} ${H+2*PAD}`);
const baseVB = [minX-PAD, fy(maxY)-PAD, W+2*PAD, H+2*PAD];

// 背景(図面)
const gseg = document.getElementById('segs');
let sb = '';
for(const s of DATA.segments){ sb += `<line class="seg" x1="${s[0]}" y1="${fy(s[1])}" x2="${s[2]}" y2="${fy(s[3])}"/>`; }
gseg.innerHTML = sb;

// 一覧
const tb = document.getElementById('tb');
DATA.edges.forEach((e,i)=>{
  const tr=document.createElement('tr'); tr.className='row'; tr.dataset.i=i;
  tr.innerHTML=`<td class="c" data-ok="1">·</td><td>${i+1}</td><td>${e.from}</td><td>${e.to}</td>`;
  tb.appendChild(tr);
});
document.getElementById('count').textContent = `配線 ${DATA.edges.length} 本 / 図面要素 ${DATA.segments.length}`;
document.getElementById('title').textContent = '配線確認 — ' + DATA.sheet;

const ghl=document.getElementById('hl'), gdev=document.getElementById('devs');
function highlight(i){
  const e=DATA.edges[i];
  ghl.innerHTML=`<line class="hl" x1="${e.ax}" y1="${fy(e.ay)}" x2="${e.bx}" y2="${fy(e.by)}"/>`
    +`<circle class="hlpt" cx="${e.ax}" cy="${fy(e.ay)}" r="18"/>`
    +`<circle class="hlpt" cx="${e.bx}" cy="${fy(e.by)}" r="18"/>`;
  gdev.innerHTML=`<circle class="devdot" cx="${e.ax}" cy="${fy(e.ay)}" r="4"/>`
    +`<text class="devlbl" x="${e.ax+22}" y="${fy(e.ay)}">${e.from}</text>`
    +`<circle class="devdot" cx="${e.bx}" cy="${fy(e.by)}" r="4"/>`
    +`<text class="devlbl" x="${e.bx+22}" y="${fy(e.by)}">${e.to}</text>`;
  document.querySelectorAll('tr.sel').forEach(t=>t.classList.remove('sel'));
  const tr=tb.querySelector(`tr[data-i="${i}"]`); if(tr){tr.classList.add('sel'); tr.scrollIntoView({block:'nearest'});}
}
tb.addEventListener('click',ev=>{
  const tr=ev.target.closest('tr.row'); if(!tr)return;
  if(ev.target.classList.contains('c')){ tr.classList.toggle('ok');
    ev.target.textContent = tr.classList.contains('ok')?'OK':'·'; return; }
  highlight(+tr.dataset.i);
});

// ズーム・パン
let vb=[...baseVB];
function apply(){ svg.setAttribute('viewBox', vb.join(' ')); }
svg.addEventListener('wheel',ev=>{ev.preventDefault();
  const r=svg.getBoundingClientRect();
  const mx=vb[0]+(ev.clientX-r.left)/r.width*vb[2];
  const my=vb[1]+(ev.clientY-r.top)/r.height*vb[3];
  const k=ev.deltaY<0?0.85:1.18;
  vb[0]=mx-(mx-vb[0])*k; vb[1]=my-(my-vb[1])*k; vb[2]*=k; vb[3]*=k; apply();
},{passive:false});
let drag=null;
svg.addEventListener('pointerdown',ev=>{drag=[ev.clientX,ev.clientY,vb[0],vb[1]];svg.classList.add('drag');svg.setPointerCapture(ev.pointerId);});
svg.addEventListener('pointermove',ev=>{if(!drag)return;const r=svg.getBoundingClientRect();
  vb[0]=drag[2]-(ev.clientX-drag[0])/r.width*vb[2]; vb[1]=drag[3]-(ev.clientY-drag[1])/r.height*vb[3]; apply();});
svg.addEventListener('pointerup',()=>{drag=null;svg.classList.remove('drag');});
document.getElementById('reset').addEventListener('click',()=>{vb=[...baseVB];apply();});
if(DATA.edges.length) highlight(0);
</script>
</body>
</html>
"""


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    out, n = write_html(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(f'生成: {out} (配線 {n} 本)')
