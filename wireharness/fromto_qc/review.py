# -*- coding: utf-8 -*-
"""生成ハーネスの確認レビューUI(表)＋固定ラベルシールへの出力。

流れ(茂泉様のご要望):
  1) 生成データを表で確認できる形に整える(号線/種別/サイズ/圧着/From-To/ロケータ/測長 等)。
  2) 曖昧箇所を『要確認』として色分け・確認を促す。ご指定の4項目:
       圧着端子サイズ未確定 / ロケータ未取得 / TB仮番号(端子未記入) / 電気QC(同一端子に複数号線)。
  3) 各行を確認 or その場で修正できる。
  4) 問題が無くなれば『シート出力』ボタンで、固定規格のラベルシール台紙に合わせて印刷。
     ラベルは面付け・寸法・余白・ピッチを指定でき、絶対配置で台紙にピッタリ合わせる。
     各マスに収まるようフォントは自動縮小(二分探索)。
"""
import os
import json
from .geometry import norm
from . import harness_learn as HL


def _crimp_known(size, device):
    t = HL.load().get('crimp_table', {})
    return f'{size}|{norm(device)}' in t


def build_review_data(routed, addr_map=None, ec=None, seiban=''):
    """route_and_length の結果 → レビュー表用の行データ(要確認フラグ付き)。"""
    am = addr_map or {}
    shorts = set()
    for s in (ec or {}).get('shorts', []):
        dev, _, term = s['terminal'].partition(':')
        shorts.add((norm(dev), norm(term)))

    def dev(e):
        return e['device'] + ('-' + e['no'] if e.get('no') else '')

    def endpoint(e, size):
        d = e.get('device', '')
        no = e.get('no', '')
        term = e.get('terminal', '')
        loc = am.get(norm(dev(e)), '') or am.get(norm(d), '')
        crimp = HL.crimp_of(size, d)
        known = _crimp_known(size, d)
        flags = []
        if not loc:
            flags.append('ロケータ未取得')
        # 圧着未確定: 圧着端子が要る太物(2sq以上)で学習テーブルに無い場合のみ。
        # 1.25制御線は圧着無し(空欄)が正常なので警告しない(過検出を避ける)。
        try:
            big = float(str(size)) >= 2.0
        except ValueError:
            big = False
        if not known and big:
            flags.append('圧着未確定')
        if '仮' in (d + no) or term in ('', '?'):
            flags.append('TB仮番号')
        if (norm(d), norm(term)) in shorts:
            flags.append('電気QC')
        return {'place': e.get('place', ''), 'device': d, 'no': no, 'terminal': term,
                'loc': loc, 'crimp': crimp, 'crimp_known': known, 'flags': flags}

    rows = []
    for i, w in enumerate(routed['wires']):
        size = w.get('size', '')
        fr = endpoint(w['from'], size)
        to = endpoint(w['to'], size)
        flags = sorted(set(fr['flags'] + to['flags']))
        rows.append({
            'i': i + 1,
            'gousen': w.get('gousen', ''),
            'color': w.get('color', ''),
            'kind': w.get('kind', ''),
            'type': HL.wire_type(w.get('kind'), w.get('gousen'), w.get('color')),
            'size': size,
            'from': fr, 'to': to,
            'length': w.get('length', ''),
            'route': len(w.get('route', []) or []),
            'flags': flags,
        })
    meta = {
        'seiban': seiban,
        'count': len(rows),
        'total_length': routed.get('total_length', ''),
        'duct_type': routed.get('duct_type', ''),
        'need_confirm': sum(1 for r in rows if r['flags']),
    }
    return {'meta': meta, 'rows': rows}


def to_review_html(routed, path, seiban='', addr_map=None, ec=None):
    """レビューUI(表＋要確認＋編集＋固定ラベル出力)の自己完結HTMLを書き出す。"""
    data = build_review_data(routed, addr_map=addr_map, ec=ec, seiban=seiban)
    html = _TEMPLATE.replace('/*__DATA__*/null',
                             json.dumps(data, ensure_ascii=False))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path


_TEMPLATE = r"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ハーネス生成データ 確認</title>
<style>
  :root{
    --bg:#f6f7f9; --panel:#fff; --ink:#1a1d21; --line:#d8dde3; --muted:#6b7280;
    --accent:#2563eb; --warn-bg:#fff4d6; --warn-line:#e0a800; --ok:#0a8a3a; --bad:#c0392b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:"Noto Sans JP","Yu Gothic",sans-serif;font-size:13px}
  header{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:1px solid var(--line);
         padding:10px 14px;display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center}
  header h1{font-size:15px;margin:0 12px 0 0}
  .kpi{display:flex;gap:14px;flex-wrap:wrap}
  .kpi b{font-size:15px}
  .kpi .need{color:var(--bad)}
  .sp{flex:1}
  button{font:inherit;border:1px solid var(--line);background:#fff;border-radius:7px;
         padding:7px 12px;cursor:pointer}
  button.primary{background:var(--accent);color:#fff;border-color:var(--accent);font-weight:700}
  button.primary:disabled{background:#9db4e8;border-color:#9db4e8;cursor:not-allowed}
  button.ghost{background:#fff}
  .wrap{padding:12px 14px 60px}
  .bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
  label.chk{display:inline-flex;gap:5px;align-items:center;color:var(--muted)}
  table{border-collapse:collapse;width:100%;background:var(--panel)}
  th,td{border:1px solid var(--line);padding:3px 5px;text-align:left;white-space:nowrap}
  th{position:sticky;top:52px;background:#eef1f5;z-index:2;font-weight:700}
  tr.flag{background:#fffdf5}
  td.warn{background:var(--warn-bg);outline:1.5px solid var(--warn-line);outline-offset:-1.5px}
  tr.done{opacity:.55}
  tr.done td.warn{background:#eef7ee;outline-color:var(--ok)}
  td input{font:inherit;border:1px solid transparent;background:transparent;width:100%;min-width:36px;padding:1px 2px}
  td input:focus{border-color:var(--accent);background:#fff;outline:none}
  td.num{text-align:right}
  .tag{display:inline-block;border:1px solid var(--warn-line);background:var(--warn-bg);
       color:#8a6d00;border-radius:4px;padding:0 4px;margin:1px;font-size:11px}
  .loc{border:1px solid #000;border-radius:3px;padding:0 3px;font-weight:700}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:12px;margin-bottom:12px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px 14px}
  .grid label{display:flex;flex-direction:column;gap:2px;color:var(--muted);font-size:12px}
  .grid input,.grid select{font:inherit;padding:4px 6px;border:1px solid var(--line);border-radius:6px}
  .hint{color:var(--muted);font-size:12px}
  details summary{cursor:pointer;font-weight:700;margin-bottom:6px}
</style></head><body>
<header>
  <h1>ハーネス生成データ 確認 <span id="sb" class="muted"></span></h1>
  <div class="kpi">
    <span>電線 <b id="k-count">0</b></span>
    <span>総配線長 <b id="k-len">0</b></span>
    <span>ダクト <b id="k-duct">-</b></span>
    <span>要確認 <b id="k-need" class="need">0</b></span>
    <span>未確認残 <b id="k-left" class="need">0</b></span>
  </div>
  <div class="sp"></div>
  <button class="primary" id="btn-out" disabled>シート出力 ▶</button>
</header>
<div class="wrap">

  <details class="panel" id="geo">
    <summary>ラベルシール規格（固定台紙に合わせる）</summary>
    <div class="grid">
      <label>プリセット
        <select id="g-preset">
          <option value="custom">カスタム（数値指定）</option>
          <option value="65">エーワン 65面 (38.1×21.2)</option>
          <option value="44">エーワン 44面 (48.3×25.4)</option>
        </select></label>
      <label>用紙<select id="g-page"><option>A4</option><option>A3</option><option>Letter</option></select></label>
      <label>列数<input id="g-cols" type="number" step="1" value="5"></label>
      <label>段数<input id="g-rows" type="number" step="1" value="13"></label>
      <label>ラベル幅 mm<input id="g-lw" type="number" step="0.1" value="38.1"></label>
      <label>ラベル高 mm<input id="g-lh" type="number" step="0.1" value="21.2"></label>
      <label>上余白 mm<input id="g-mt" type="number" step="0.1" value="10.7"></label>
      <label>左余白 mm<input id="g-ml" type="number" step="0.1" value="6.4"></label>
      <label>横ピッチ mm<input id="g-px" type="number" step="0.1" value="40.6"></label>
      <label>縦ピッチ mm<input id="g-py" type="number" step="0.1" value="21.2"></label>
    </div>
    <p class="hint">※ 固定台紙の型番/寸法に合わせて数値を入れてください。ピッチはラベル同士の中心間隔（余白＋ラベル＋間隙）。</p>
  </details>

  <div class="bar">
    <label class="chk"><input type="checkbox" id="only-need"> 要確認のみ表示</label>
    <button class="ghost" id="btn-allok">表示中を全て確認済みに</button>
    <span class="hint">黄色セル＝要確認。値を修正するか、行末「確認」をチェックしてください。全て解消で「シート出力」が有効になります。</span>
  </div>

  <table id="tbl"><thead><tr>
    <th>#</th><th>号線</th><th>種別</th><th>ｻｲｽﾞ</th>
    <th>From場所</th><th>From機器</th><th>番号</th><th>端子</th><th>ﾛｹｰﾀ</th><th>圧着</th>
    <th>To場所</th><th>To機器</th><th>番号</th><th>端子</th><th>ﾛｹｰﾀ</th><th>圧着</th>
    <th>測長</th><th>要確認</th><th>確認</th>
  </tr></thead><tbody id="tb"></tbody></table>
</div>

<script>
var DATA = /*__DATA__*/null;
var rows = DATA.rows, meta = DATA.meta;
document.getElementById('sb').textContent = meta.seiban || '';
document.getElementById('k-count').textContent = meta.count;
document.getElementById('k-len').textContent = meta.total_length;
document.getElementById('k-duct').textContent = meta.duct_type || '-';
document.getElementById('k-need').textContent = meta.need_confirm;

function esc(s){return (''+ (s==null?'':s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function inp(val, cls, oninput){var v=esc(val); return '<input class="'+cls+'" value="'+v+'"'+(oninput?' data-k="'+oninput+'"':'')+'>';}

function endCells(r, side){
  var e=r[side];
  function td(field, warnType){
    var warn = e.flags.indexOf(warnType)>=0 ? ' class="warn"' : '';
    return '<td'+warn+'>'+inp(e[field], side+'-'+field)+'</td>';
  }
  // 場所/機器/番号 は読み取り主体だが編集可。端子/ロケータ/圧着 は確認対象。
  return '<td>'+inp(e.place, side+'-place')+'</td>'
       + '<td>'+inp(e.device, side+'-device')+'</td>'
       + '<td>'+inp(e.no, side+'-no')+'</td>'
       + td('terminal','TB仮番号')
       + td('loc','ロケータ未取得')
       + td('crimp','圧着未確定');
}

function render(){
  var onlyNeed=document.getElementById('only-need').checked;
  var tb=document.getElementById('tb'); tb.innerHTML='';
  rows.forEach(function(r,idx){
    if(onlyNeed && !(r.flags.length && !r._done)) return;
    var tr=document.createElement('tr');
    tr.className=(r.flags.length?'flag ':'')+(r._done?'done':'');
    var qcWarn = r.flags.indexOf('電気QC')>=0 ? ' class="warn"' : '';
    tr.innerHTML='<td class="num">'+r.i+'</td>'
      +'<td>'+esc(r.gousen||r.color)+'</td>'
      +'<td>'+inp(r.type,'w-type')+'</td>'
      +'<td>'+inp(r.size,'w-size')+'</td>'
      +endCells(r,'from')
      +endCells(r,'to')
      +'<td class="num">'+esc(r.length)+'</td>'
      +'<td'+qcWarn+'>'+r.flags.map(function(f){return '<span class="tag">'+f+'</span>';}).join('')+'</td>'
      +'<td style="text-align:center">'+(r.flags.length?'<input type="checkbox" class="done"'+(r._done?' checked':'')+'>':'—')+'</td>';
    // bind inputs
    tr.querySelectorAll('input[data-k]').forEach(function(el){
      el.addEventListener('input',function(){
        var k=el.getAttribute('data-k').split('-'); // side-field or w-field
        if(k[0]==='w'){ r[k[1]===''?'':k[1]]=el.value; if(k[1]==='type')r.type=el.value; if(k[1]==='size')r.size=el.value; }
        else { r[k[0]][k[1]]=el.value; }
      });
    });
    var dc=tr.querySelector('input.done');
    if(dc) dc.addEventListener('change',function(){ r._done=dc.checked; recalc(); render(); });
    tb.appendChild(tr);
  });
  recalc();
}

function recalc(){
  var left=rows.filter(function(r){return r.flags.length && !r._done;}).length;
  document.getElementById('k-left').textContent=left;
  document.getElementById('btn-out').disabled = left>0;
}

document.getElementById('only-need').addEventListener('change',render);
document.getElementById('btn-allok').addEventListener('click',function(){
  var onlyNeed=document.getElementById('only-need').checked;
  rows.forEach(function(r){ if(r.flags.length){ if(!onlyNeed || !r._done) r._done=true; }});
  render();
});

// ---- プリセット ----
var PRESET={ '65':{cols:5,rows:13,lw:38.1,lh:21.2,mt:10.7,ml:6.4,px:40.6,py:21.2},
             '44':{cols:4,rows:11,lw:48.3,lh:25.4,mt:21.2,ml:8.0,px:49.5,py:25.4} };
document.getElementById('g-preset').addEventListener('change',function(){
  var p=PRESET[this.value]; if(!p)return;
  for(var k in p){ document.getElementById('g-'+k).value=p[k]; }
});

function geo(){
  var g=function(id){return parseFloat(document.getElementById('g-'+id).value)||0;};
  return {page:document.getElementById('g-page').value,cols:g('cols'),rows:g('rows'),
          lw:g('lw'),lh:g('lh'),mt:g('mt'),ml:g('ml'),px:g('px'),py:g('py')};
}

// ---- ラベルシート出力(固定台紙・絶対配置・フォント自動調整) ----
function labelHTML(r){
  function ep(e){
    var t=e.terminal?(':'+esc(e.terminal)):'';
    var pl=e.place?('['+esc(e.place)+']'):'';
    var lc=e.loc?(' <span class="loc">'+esc(e.loc)+'</span>'):'';
    var cr=e.crimp?(' <span class="cr">'+esc(e.crimp)+'</span>'):'';
    return pl+esc(e.device)+(e.no?('-'+esc(e.no)):'')+t+lc+cr;
  }
  return '<div class="fit"><div class="hd"><b>'+esc(r.gousen||r.color)+'</b>'
    +'<span class="sz">'+esc(r.type)+esc(r.size)+'</span></div>'
    +'<div class="ep"><i>F</i>'+ep(r.from)+'</div>'
    +'<div class="ep"><i>T</i>'+ep(r.to)+'</div></div>';
}

document.getElementById('btn-out').addEventListener('click',function(){
  var G=geo(), per=Math.max(1,G.cols*G.rows);
  var pages='', n=rows.length;
  for(var i=0;i<n;i+=per){
    var cells='';
    for(var j=0;j<per;j++){
      var r=rows[i+j]; if(!r) break;
      var col=j%G.cols, row=Math.floor(j/G.cols);
      var x=(G.ml+col*G.px).toFixed(2), y=(G.mt+row*G.py).toFixed(2);
      cells+='<div class="cell" style="left:'+x+'mm;top:'+y+'mm;width:'+G.lw+'mm;height:'+G.lh+'mm">'+labelHTML(r)+'</div>';
    }
    pages+='<div class="page">'+cells+'</div>';
  }
  var css='@page{size:'+G.page+';margin:0}*{box-sizing:border-box}'
    +'body{margin:0;font-family:"Noto Sans JP","Yu Gothic",sans-serif;color:#000}'
    +'.page{position:relative;width:'+(G.page==="A4"?210:G.page==="A3"?297:216)+'mm;'
    +'height:'+(G.page==="A4"?297:G.page==="A3"?420:279)+'mm;page-break-after:always}'
    +'.cell{position:absolute;overflow:hidden;padding:0.6mm;display:flex;align-items:center}'
    +'.fit{width:100%;line-height:1.13}'
    +'.hd{display:flex;justify-content:space-between;align-items:baseline;border-bottom:0.15mm solid #000;margin-bottom:0.3mm}'
    +'.hd b{font-weight:700}.sz{opacity:.85}'
    +'.ep{overflow-wrap:anywhere}.ep i{display:inline-block;width:1.05em;font-style:normal;font-weight:700;opacity:.7}'
    +'.loc{border:0.15mm solid #000;border-radius:0.6mm;padding:0 0.4mm;font-weight:700;white-space:nowrap}'
    +'.cr{border:0.15mm dashed #666;border-radius:0.6mm;padding:0 0.3mm}'
    +'@media screen{body{background:#eee;padding:8px}.page{background:#fff;margin:0 auto 10px;box-shadow:0 1px 6px rgba(0,0,0,.3);outline:1px solid #ccc}.cell{outline:0.2mm dashed #bbb}}';
  var doc='<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>'+esc(meta.seiban)+' ラベル</title>'
    +'<style>'+css+'</style></head><body>'+pages
    +'<scr'+'ipt>function fit(el,box){var lo=3,hi=12;for(var k=0;k<14;k++){var m=(lo+hi)/2;el.style.fontSize=m+"pt";'
    +'if(el.scrollWidth<=box.clientWidth&&el.scrollHeight<=box.clientHeight)lo=m;else hi=m;}el.style.fontSize=lo+"pt";}'
    +'document.querySelectorAll(".cell .fit").forEach(function(f){fit(f,f.parentNode);});'
    +'setTimeout(function(){window.print&&window.print();},400);</scr'+'ipt></body></html>';
  var w=window.open('','_blank'); w.document.write(doc); w.document.close();
});

render();
</script></body></html>"""
