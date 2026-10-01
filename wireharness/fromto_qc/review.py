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
import re
import json
from .geometry import norm
from .layout import DOOR_BASE
from . import harness_learn as HL


def _crimp_known(size, device):
    t = HL.load().get('crimp_table', {})
    return f'{size}|{norm(device)}' in t


def _is_door(device):
    """扉付け機器(計器・表示灯・スイッチ等)か。内部配置図(取付板)には載らず扉面に付く。"""
    base = ''.join(ch for ch in str(device).split('-')[0] if not ch.isdigit())
    return device in DOOR_BASE or base in DOOR_BASE


def _is_boundary(device):
    """盤外/盤間の境界中継コネクタ(R-F(P)/L-F(R)/F(P)/F(R))。盤内に位置は無い(=盤外)。"""
    d = str(device)
    return 'F(P)' in d or 'F(R)' in d or d.startswith('R-F') or d.startswith('L-F') or '盤外' in d


def build_review_data(routed, addr_map=None, ec=None, seiban='', defects=None):
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
        # TB端子番号は製造アサイン(茂泉様/手本準拠)。仮Nは表示せず空欄にし、製造が付番/確認する。
        if re.match(r'^仮\d', str(no)):
            no = ''
        loc = am.get(norm(dev(e)), '') or am.get(norm(d), '')
        place = e.get('place', '')
        # 扉付け機器→扉、盤外境界(R-F(P)/L-F(R))→盤外(いずれも盤内に位置が無いのが正常)。
        if not loc and _is_door(d):
            loc = '扉'
            place = place or '扉'
        elif not loc and _is_boundary(d):
            loc = '盤外'
            place = place or '盤外'
        # 過電流継電器 51 は配置図に単独では無く、電子式で同回路の 52/MCCB(遮断器・接触器)に統合。
        # モデルの物理実体に合わせ、同回路(番号一致)の 52→MCCB→ELCB のロケータを継承する。
        elif not loc and no and re.match(r'^51([A-Z]|$)', norm(d)):
            for host in ('52', 'MCCB', 'ELCB'):
                loc = am.get(norm(host + '-' + no)) or am.get(norm(host + no)) or ''
                if loc:
                    break
        # 圧着端子サイズ: 学習で分かる所は自動補完。モデルでも空欄が正常に多いため、
        # 不明時は空欄のままとし『要確認』にはしない(非ブロッキング。過検出を避ける)。
        crimp = HL.crimp_of(size, d)
        flags = []
        # TB台番号は 幾何付番→回路番号→号線名 で自動付番済み。ここまでで台番号が付かないTBは、
        # 接続先機器に回路番号(DEVICE1)も号線も無い＝設計図面の不備(rows側で設計指摘に回す)。
        # 注) 同一端子が複数号線に現れるのは『分岐』(茂泉様)。等電位を保ったままその端子から
        #     新しい号線が分岐する正常な結線であり、短絡ではない。よって要確認にはしない。
        # ロケータは照合の補助情報(ハーネス配線の正誤ではない)。図面にロケータが無い製番もある。
        # 位置が引ければ付与、引けなければ空欄のままとし『要確認』にはしない(非ブロッキング)。
        _ = shorts   # electrical_check の shorts は分岐点(正常)なので要確認にしない(内部QCのみ)
        return {'place': place, 'device': d, 'no': no, 'terminal': term,
                'loc': loc, 'crimp': crimp, 'flags': flags}

    rows = []
    tb_gaps = {}   # 台番号が確定できないTB(=回路番号も号線も無い) → 設計指摘にまとめる
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
        # 台番号が付かないTB = 接続先機器に回路番号も号線も無い → 設計指摘(前工程へ)
        for a, b in ((fr, to), (to, fr)):
            if norm(a['device']) == 'TB' and not a['no']:
                key = (b['device'], b.get('terminal', ''))
                tb_gaps[key] = {
                    '分類': '端子台の回路番号/号線 未記入',
                    '該当': f"TB ↔ {b['device']}{(':' + b['terminal']) if b.get('terminal') else ''}",
                    '号線': w.get('gousen', '') or '(空)',
                    '解決案': '結線図で、端子台に繋がる機器に回路番号(DEVICE1)または号線を記入してください。'
                              '未記入のため台番号を一意に確定できません。',
                }
    dfx = list(defects or []) + list(tb_gaps.values())
    meta = {
        'seiban': seiban,
        'count': len(rows),
        'total_length': routed.get('total_length', ''),
        'duct_type': routed.get('duct_type', ''),
        'need_confirm': sum(1 for r in rows if r['flags']),
        'defects': dfx,
    }
    return {'meta': meta, 'rows': rows}


def to_review_html(routed, path, seiban='', addr_map=None, ec=None, defects=None):
    """レビューUI(表＋要確認＋編集＋固定ラベル出力)の自己完結HTMLを書き出す。
    defects: design_feedback の items（前工程へ提示する図面不備）。ハーネス確定前に上部提示する。"""
    data = build_review_data(routed, addr_map=addr_map, ec=ec, seiban=seiban, defects=defects)
    html = _TEMPLATE.replace('/*__DATA__*/null',
                             json.dumps(data, ensure_ascii=False))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path


_TEMPLATE = r"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ハーネス生成データ 確認</title>
<style>
  :root{--bg:#f6f7f9;--panel:#fff;--ink:#1a1d21;--line:#d8dde3;--muted:#6b7280;
    --accent:#2563eb;--warn-bg:#fff4d6;--warn-line:#e0a800;--ok:#0a8a3a;--bad:#c0392b}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans JP","Yu Gothic",sans-serif;font-size:13px}
  header{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:1px solid var(--line);
         padding:10px 14px;display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center}
  header h1{font-size:15px;margin:0 12px 0 0}
  .kpi{display:flex;gap:14px;flex-wrap:wrap}.kpi b{font-size:15px}.kpi .need{color:var(--bad)}
  .sp{flex:1}
  button{font:inherit;border:1px solid var(--line);background:#fff;border-radius:7px;padding:7px 12px;cursor:pointer}
  button.primary{background:var(--accent);color:#fff;border-color:var(--accent);font-weight:700}
  button.primary:disabled{background:#9db4e8;border-color:#9db4e8;cursor:not-allowed}
  button.ghost{background:#fff}
  .wrap{padding:12px 14px 60px}
  .steps{display:flex;gap:8px;align-items:center;margin:2px 0 14px;font-weight:700;color:var(--muted)}
  .steps .s{padding:5px 12px;border-radius:20px;border:1px solid var(--line);background:#fff}
  .steps .s.on{background:var(--accent);color:#fff;border-color:var(--accent)}
  .steps .s.done{background:#e9f7ee;color:var(--ok);border-color:#bfe6cc}
  .steps .arw{color:#aab}
  .bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0}
  label.chk{display:inline-flex;gap:5px;align-items:center;color:var(--muted)}
  table{border-collapse:collapse;width:100%;background:var(--panel)}
  th,td{border:1px solid var(--line);padding:3px 5px;text-align:left;white-space:nowrap}
  th{position:sticky;top:52px;background:#eef1f5;z-index:2;font-weight:700}
  tr.flag{background:#fffdf5}
  td.warn{background:var(--warn-bg);outline:1.5px solid var(--warn-line);outline-offset:-1.5px}
  tr.done{opacity:.55}tr.done td.warn{background:#eef7ee;outline-color:var(--ok)}
  td input{font:inherit;border:1px solid transparent;background:transparent;width:100%;min-width:36px;padding:1px 2px}
  td input:focus{border-color:var(--accent);background:#fff;outline:none}
  td.num{text-align:right}
  .tag{display:inline-block;border:1px solid var(--warn-line);background:var(--warn-bg);
       color:#8a6d00;border-radius:4px;padding:0 4px;margin:1px;font-size:11px}
  .loc{border:1px solid #000;border-radius:3px;padding:0 3px;font-weight:700}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px;margin-bottom:12px}
  .panel h2{font-size:15px;margin:0 0 4px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px 14px}
  .grid label{display:flex;flex-direction:column;gap:2px;color:var(--muted);font-size:12px}
  .grid input,.grid select{font:inherit;padding:4px 6px;border:1px solid var(--line);border-radius:6px}
  .hint{color:var(--muted);font-size:12px}
  details summary{cursor:pointer;font-weight:700;margin-bottom:6px}
  /* Phase1 設計不備 */
  .def{border:1px solid #f1c9c2;border-radius:8px;margin:8px 0;overflow:hidden}
  .def.ok{border-color:#bfe6cc}
  .def .dh{display:flex;gap:10px;align-items:baseline;padding:8px 10px;background:#fdecea}
  .def.ok .dh{background:#e9f7ee}
  .def .dh b{font-size:13.5px}.def .dh .g{color:#8a1c12}
  .def .body{padding:8px 10px;display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center}
  .def .fix{color:#555;flex:1;min-width:240px}
  .opt{display:inline-flex;gap:5px;align-items:center;border:1px solid var(--line);border-radius:7px;padding:4px 9px;cursor:pointer}
  .opt.sel{border-color:var(--accent);background:#eef3fe;font-weight:700}
  .def .note{font:inherit;padding:4px 6px;border:1px solid var(--line);border-radius:6px;min-width:160px}
  .badge{font-size:11px;border-radius:10px;padding:1px 8px;font-weight:700}
  .badge.back{background:#fff0e6;color:#a5480a;border:1px solid #f2c39c}
  .badge.mfg{background:#e8f1ff;color:#1652c9;border:1px solid #bcd3f7}
</style></head><body>
<header>
  <h1>ハーネス生成データ 確認 <span id="sb" class="muted"></span></h1>
  <div class="kpi">
    <span>電線 <b id="k-count">0</b></span>
    <span>総配線長 <b id="k-len">0</b></span>
    <span>ダクト <b id="k-duct">-</b></span>
    <span>設計不備 <b id="k-def" class="need">0</b></span>
    <span>要確認 <b id="k-need" class="need">0</b></span>
  </div>
  <div class="sp"></div>
  <button class="primary" id="btn-out" disabled>③ シート出力 ▶</button>
</header>
<div class="wrap">
  <div class="steps">
    <span class="s on" id="st1">① 設計不備の解消</span><span class="arw">→</span>
    <span class="s" id="st2">② ハーネス確認</span><span class="arw">→</span>
    <span class="s" id="st3">③ シート出力</span>
  </div>

  <section id="phase1" class="panel">
    <h2>① 設計からの図面不備 — 指摘と修正案</h2>
    <p class="hint">原則は<b>設計へ戻す</b>（修正案を提示）。ただし待ち時間短縮のため<b>製造で手直し</b>も選べます。
      各不備の対応を決めると「②へ進む」が有効になります。</p>
    <div id="defbox"></div>
    <div class="bar">
      <button class="primary" id="to2" disabled>② ハーネスシート作成へ進む ▶</button>
      <span id="def-left" class="hint"></span>
    </div>
  </section>

  <section id="phase2" hidden>
    <div class="bar">
      <button class="ghost" id="back1">◀ ① 設計不備へ戻る</button>
      <span class="hint">ハーネスデータの確認。黄色＝要確認を修正/確認し、問題なければ「シート出力」。</span>
    </div>
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
      <p class="hint">※ 固定台紙の型番/寸法に合わせて数値を入れてください。ピッチはラベル中心間隔。</p>
    </details>
    <div class="bar">
      <label class="chk"><input type="checkbox" id="only-need"> 要確認のみ表示</label>
      <button class="ghost" id="btn-allok">表示中を全て確認済みに</button>
      <span>未確認残 <b id="k-left" class="need">0</b></span>
    </div>
    <table id="tbl"><thead><tr>
      <th>#</th><th>号線</th><th>種別</th><th>ｻｲｽﾞ</th>
      <th>From場所</th><th>From機器</th><th>番号</th><th>端子</th><th>ﾛｹｰﾀ</th><th>圧着</th>
      <th>To場所</th><th>To機器</th><th>番号</th><th>端子</th><th>ﾛｹｰﾀ</th><th>圧着</th>
      <th>測長</th><th>要確認</th><th>確認</th>
    </tr></thead><tbody id="tb"></tbody></table>
  </section>
</div>

<script>
var DATA = /*__DATA__*/null;
var rows = DATA.rows, meta = DATA.meta;
var defects = (meta.defects||[]).map(function(d,i){return {i:i,d:d,act:''};}); // act: '' | 'back' | 'mfg'
var phase = 1;
function $(id){return document.getElementById(id);}
$('sb').textContent = meta.seiban || '';
$('k-count').textContent = meta.count;
$('k-len').textContent = meta.total_length;
$('k-duct').textContent = meta.duct_type || '-';
$('k-def').textContent = defects.length;
$('k-need').textContent = meta.need_confirm;
function esc(s){return (''+ (s==null?'':s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

/* ---------- ① 設計不備 ---------- */
function renderDefects(){
  var box=$('defbox');
  if(!defects.length){ box.innerHTML='<div class="def ok"><div class="dh"><b>✔ 図面の設計不備は検出されませんでした</b>'
      +'<span class="hint">そのままハーネスシート作成へ進めます。</span></div></div>'; $('to2').disabled=false;
      $('def-left').textContent=''; return; }
  box.innerHTML=defects.map(function(o){
    var it=o.d;
    return '<div class="def" data-i="'+o.i+'"><div class="dh"><b>'+esc(it['分類'])+'</b>'
      +'<span class="g">該当: '+esc(it['該当'])+(it['号線']?(' ／ 号線: '+esc(it['号線'])):'')+'</span></div>'
      +'<div class="body"><span class="fix">修正案: '+esc(it['解決案'])+'</span>'
      +'<span class="opt'+(o.act==='back'?' sel':'')+'" data-act="back">設計へ戻す</span>'
      +'<span class="opt'+(o.act==='mfg'?' sel':'')+'" data-act="mfg">製造で手直し</span>'
      +'<input class="note" placeholder="メモ/修正内容(任意)" value="'+esc(o.note||'')+'">'
      +'<span class="stat"></span></div></div>';
  }).join('');
  box.querySelectorAll('.def').forEach(function(card){
    var o=defects[+card.getAttribute('data-i')];
    card.querySelectorAll('.opt').forEach(function(op){
      op.addEventListener('click',function(){ o.act=op.getAttribute('data-act'); renderDefects(); recalcDef(); });
    });
    var nt=card.querySelector('.note'); if(nt) nt.addEventListener('input',function(){o.note=nt.value;});
    var st=card.querySelector('.stat');
    if(o.act==='back') st.innerHTML='<span class="badge back">設計へ戻す</span>';
    else if(o.act==='mfg') st.innerHTML='<span class="badge mfg">製造で手直し</span>';
  });
  recalcDef();
}
function recalcDef(){
  var left=defects.filter(function(o){return !o.act;}).length;
  $('to2').disabled = left>0;
  $('def-left').textContent = left>0 ? ('未対応 '+left+' 件 — 各不備に「設計へ戻す」か「製造で手直し」を選んでください') : '全件の対応を設定しました。②へ進めます。';
  $('k-def').textContent = left;  // 残不備
}

/* ---------- ② ハーネス確認 表 ---------- */
function inp(val, cls){return '<input class="'+cls+'" value="'+esc(val)+'" data-k="'+cls+'">';}
function endCells(r, side){
  var e=r[side];
  function td(field, warnType){
    var warn = e.flags.indexOf(warnType)>=0 ? ' class="warn"' : '';
    return '<td'+warn+'><input value="'+esc(e[field])+'" data-side="'+side+'" data-f="'+field+'"></td>';
  }
  return '<td><input value="'+esc(e.place)+'" data-side="'+side+'" data-f="place"></td>'
       + '<td><input value="'+esc(e.device)+'" data-side="'+side+'" data-f="device"></td>'
       + td('no','TB端子(製造アサイン)')
       + td('terminal','TB端子(製造アサイン)')
       + td('loc','ロケータ未取得')
       + td('crimp','圧着未確定');
}
function render(){
  var onlyNeed=$('only-need').checked, tb=$('tb'); tb.innerHTML='';
  rows.forEach(function(r){
    if(onlyNeed && !(r.flags.length && !r._done)) return;
    var tr=document.createElement('tr');
    tr.className=(r.flags.length?'flag ':'')+(r._done?'done':'');
    var qc=r.flags.indexOf('電気QC')>=0?' class="warn"':'';
    tr.innerHTML='<td class="num">'+r.i+'</td>'
      +'<td>'+esc(r.gousen||r.color)+'</td>'
      +'<td><input value="'+esc(r.type)+'" data-w="type"></td>'
      +'<td><input value="'+esc(r.size)+'" data-w="size"></td>'
      +endCells(r,'from')+endCells(r,'to')
      +'<td class="num">'+esc(r.length)+'</td>'
      +'<td'+qc+'>'+r.flags.map(function(f){return '<span class="tag">'+f+'</span>';}).join('')+'</td>'
      +'<td style="text-align:center">'+(r.flags.length?'<input type="checkbox" class="done"'+(r._done?' checked':'')+'>':'—')+'</td>';
    tr.querySelectorAll('input[data-side]').forEach(function(el){
      el.addEventListener('input',function(){ r[el.getAttribute('data-side')][el.getAttribute('data-f')]=el.value; });
    });
    tr.querySelectorAll('input[data-w]').forEach(function(el){
      el.addEventListener('input',function(){ r[el.getAttribute('data-w')]=el.value; });
    });
    var dc=tr.querySelector('input.done');
    if(dc) dc.addEventListener('change',function(){ r._done=dc.checked; recalc(); render(); });
    tb.appendChild(tr);
  });
  recalc();
}
function recalc(){
  var left=rows.filter(function(r){return r.flags.length && !r._done;}).length;
  $('k-left').textContent=left; $('k-need').textContent=left;
  $('btn-out').disabled = !(phase===2 && left===0);
}
$('only-need').addEventListener('change',render);
$('btn-allok').addEventListener('click',function(){
  var onlyNeed=$('only-need').checked;
  rows.forEach(function(r){ if(r.flags.length && (!onlyNeed || !r._done)) r._done=true; });
  render();
});

/* ---------- 工程遷移 ---------- */
function goPhase(p){
  phase=p;
  $('phase1').hidden = p!==1; $('phase2').hidden = p!==2;
  $('st1').className='s '+(p>1?'done':'on'); $('st2').className='s '+(p===2?'on':(p>2?'done':''));
  if(p===2) render();
  recalc();
}
$('to2').addEventListener('click',function(){ goPhase(2); });
$('back1').addEventListener('click',function(){ goPhase(1); });

/* ---------- プリセット/寸法 ---------- */
var PRESET={ '65':{cols:5,rows:13,lw:38.1,lh:21.2,mt:10.7,ml:6.4,px:40.6,py:21.2},
             '44':{cols:4,rows:11,lw:48.3,lh:25.4,mt:21.2,ml:8.0,px:49.5,py:25.4} };
$('g-preset').addEventListener('change',function(){var p=PRESET[this.value];if(!p)return;for(var k in p){$('g-'+k).value=p[k];}});
function geo(){var g=function(id){return parseFloat($('g-'+id).value)||0;};
  return {page:$('g-page').value,cols:g('cols'),rows:g('rows'),lw:g('lw'),lh:g('lh'),mt:g('mt'),ml:g('ml'),px:g('px'),py:g('py')};}

/* ---------- ③ ラベル出力(固定台紙・絶対配置・フォント自動調整) ---------- */
function labelHTML(r){
  function ep(e){var t=e.terminal?(':'+esc(e.terminal)):'';var pl=e.place?('['+esc(e.place)+']'):'';
    var lc=e.loc?(' <span class="loc">'+esc(e.loc)+'</span>'):'';var cr=e.crimp?(' <span class="cr">'+esc(e.crimp)+'</span>'):'';
    return pl+esc(e.device)+(e.no?('-'+esc(e.no)):'')+t+lc+cr;}
  return '<div class="fit"><div class="hd"><b>'+esc(r.gousen||r.color)+'</b><span class="sz">'+esc(r.type)+esc(r.size)+'</span></div>'
    +'<div class="ep"><i>F</i>'+ep(r.from)+'</div><div class="ep"><i>T</i>'+ep(r.to)+'</div></div>';
}
$('btn-out').addEventListener('click',function(){
  var G=geo(), per=Math.max(1,G.cols*G.rows), pages='', n=rows.length;
  for(var i=0;i<n;i+=per){var cells='';
    for(var j=0;j<per;j++){var r=rows[i+j]; if(!r)break;
      var col=j%G.cols,row=Math.floor(j/G.cols);
      var x=(G.ml+col*G.px).toFixed(2),y=(G.mt+row*G.py).toFixed(2);
      cells+='<div class="cell" style="left:'+x+'mm;top:'+y+'mm;width:'+G.lw+'mm;height:'+G.lh+'mm">'+labelHTML(r)+'</div>';}
    pages+='<div class="page">'+cells+'</div>';}
  var css='@page{size:'+G.page+';margin:0}*{box-sizing:border-box}body{margin:0;font-family:"Noto Sans JP","Yu Gothic",sans-serif;color:#000}'
    +'.page{position:relative;width:'+(G.page==="A4"?210:G.page==="A3"?297:216)+'mm;height:'+(G.page==="A4"?297:G.page==="A3"?420:279)+'mm;page-break-after:always}'
    +'.cell{position:absolute;overflow:hidden;padding:0.6mm;display:flex;align-items:center}.fit{width:100%;line-height:1.13}'
    +'.hd{display:flex;justify-content:space-between;align-items:baseline;border-bottom:0.15mm solid #000;margin-bottom:0.3mm}.hd b{font-weight:700}.sz{opacity:.85}'
    +'.ep{overflow-wrap:anywhere}.ep i{display:inline-block;width:1.05em;font-style:normal;font-weight:700;opacity:.7}'
    +'.loc{border:0.15mm solid #000;border-radius:0.6mm;padding:0 0.4mm;font-weight:700;white-space:nowrap}.cr{border:0.15mm dashed #666;border-radius:0.6mm;padding:0 0.3mm}'
    +'@media screen{body{background:#eee;padding:8px}.page{background:#fff;margin:0 auto 10px;box-shadow:0 1px 6px rgba(0,0,0,.3);outline:1px solid #ccc}.cell{outline:0.2mm dashed #bbb}}';
  var doc='<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>'+esc(meta.seiban)+' ラベル</title><style>'+css+'</style></head><body>'+pages
    +'<scr'+'ipt>function fit(el,box){var lo=3,hi=12;for(var k=0;k<14;k++){var m=(lo+hi)/2;el.style.fontSize=m+"pt";'
    +'if(el.scrollWidth<=box.clientWidth&&el.scrollHeight<=box.clientHeight)lo=m;else hi=m;}el.style.fontSize=lo+"pt";}'
    +'document.querySelectorAll(".cell .fit").forEach(function(f){fit(f,f.parentNode);});setTimeout(function(){window.print&&window.print();},400);</scr'+'ipt></body></html>';
  var w=window.open('','_blank'); w.document.write(doc); w.document.close();
});

renderDefects();
goPhase(1);
</script></body></html>"""
