# -*- coding: utf-8 -*-
"""生成ハーネスの確認レビューUI(表)＋確定・出力＋学習。

流れ(茂泉様のご要望):
  1) 生成データを表で確認できる形に整える(号線/種別/サイズ/圧着/From-To/ロケータ/測長 等)。
  2) 曖昧箇所を『要確認』として色分け・確認を促す。ご指定の4項目:
       圧着端子サイズ未確定 / ロケータ未取得 / TB仮番号(端子未記入) / 電気QC(同一端子に複数号線)。
  3) 各行を確認 or その場で修正できる。
  4) 問題が無くなれば『確定・出力＋学習』ボタンで、モデルと同一フォーマットの Excel/txt を出力する
     (既存システムが取り込みシールシートへ印刷)。確定した修正内容は学習に反映され、
     次回以降の自動生成が賢くなる(確定コーパス→learned.json を回帰ゼロでマージ)。
  ※ ラベルの直接印刷は第二弾の印刷機能として一旦保留(生成コードは温存)。
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
                    '分類': '【生成時整合性】端子台の回路番号/号線 未記入',
                    '該当': f"TB ↔ {b['device']}{(':' + b['terminal']) if b.get('terminal') else ''}",
                    '号線': w.get('gousen', '') or '(空)',
                    '解決案': '結線図で、端子台に繋がる機器に回路番号(DEVICE1)または号線を記入してください。'
                              '未記入のため台番号を一意に確定できません。',
                }
    dfx = list(defects or []) + list(tb_gaps.values())
    cross = _crossings_data(routed)
    meta = {
        'seiban': seiban,
        'count': len(rows),
        'total_length': routed.get('total_length', ''),
        'duct_type': routed.get('duct_type', ''),
        'need_confirm': sum(1 for r in rows if r['flags']),
        'defects': dfx,
        'cross_count': len(cross['items']),
        'cross_need': cross['need'],
    }
    return {'meta': meta, 'rows': rows, 'crossings': cross}


def _crossings_data(routed):
    """盤間渡り(R-F/L-F=制御 / 渡り用TB=太物)を確認UI用に整形。
    各渡りを 送り面→受け面 の対で示し、二重検証の確度(◎/○/△)と不一致を添える。
    device→面 が取れない図面では空(既存UIに影響なし=回帰ゼロ)。"""
    cross = routed.get('crossings') or {}
    by_g = {}
    for r in cross.get('rows', []):
        by_g.setdefault(r['号線'], []).append(r)

    def _fk(f):
        import re as _re
        m = _re.match(r'^(\d+)', str(f))
        return (int(m.group(1)) if m else 10 ** 9, str(f))

    items = []
    for g, send, recv in list(cross.get('control', [])) + list(cross.get('power', [])):
        rs = by_g.get(g, [])
        faces = sorted({r['面'] for r in rs}, key=_fk)      # 跨ぐ全面(通過面含む)
        core = next((r['端子'] for r in rs), '')
        nb = max(0, len(faces) - 1)                          # 盤境界(コネクタ)数
        items.append({
            'gousen': g,
            'type': cross.get('conf') and (rs[0]['型'] if rs else ''),
            'conf': cross.get('conf', {}).get(g, '○'),
            'send': send, 'recv': recv,
            'path': '→'.join('面' + f for f in faces),       # 面1→面2→面3
            'core': core, 'boundaries': nb,
            'relay': faces[1:-1] if len(faces) > 2 else [],   # 中継(通過)面
        })
    items.sort(key=lambda x: (x['type'] or '', x['gousen']))
    return {'faces': cross.get('faces', 0), 'items': items,
            'conflicts': [list(c) for c in cross.get('conflicts', [])],
            'need': sum(1 for x in items if x['conf'] == '△')}


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
  /* 盤間渡り */
  .xwarn{background:var(--warn-bg);border:1px solid var(--warn-line);border-radius:7px;padding:7px 10px;margin:6px 0;color:#7a5c00}
  .xnote{background:#eef4fb;border:1px solid #c7dbf0;border-radius:7px;padding:7px 10px;margin:6px 0;color:#355}
  .cf{font-weight:700;border-radius:10px;padding:1px 8px;font-size:12px}
  .cf.ok{background:#e9f7ee;color:var(--ok);border:1px solid #bfe6cc}
  .cf.mid{background:#eef3fe;color:#1652c9;border:1px solid #bcd3f7}
  .cf.bad{background:#fdecea;color:var(--bad);border:1px solid #f1c9c2}
  .muted{color:var(--muted)}
</style></head><body>
<header>
  <h1>ハーネス生成データ 確認 <span id="sb" class="muted"></span></h1>
  <div class="kpi">
    <span>電線 <b id="k-count">0</b></span>
    <span>総配線長 <b id="k-len">0</b></span>
    <span>ダクト <b id="k-duct">-</b></span>
    <span>設計不備 <b id="k-def" class="need">0</b></span>
    <span>要確認 <b id="k-need" class="need">0</b></span>
    <span>盤間渡り <b id="k-cross">0</b><b id="k-cross-need" class="need"></b></span>
  </div>
  <div class="sp"></div>
  <button class="primary" id="btn-out" disabled>③ 確定・出力＋学習 ▶</button>
</header>
<div class="wrap">
  <div class="steps">
    <span class="s on" id="st1">① 設計不備の解消</span><span class="arw">→</span>
    <span class="s" id="st2">② ハーネス確認</span><span class="arw">→</span>
    <span class="s" id="st3">③ 確定・出力＋学習</span>
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
      <span class="hint">ハーネスデータの確認。黄色＝要確認を修正/確認し、問題なければ「確定・出力＋学習」。
        修正内容は確定時に学習され、次回以降の自動生成に反映されます。</span>
    </div>
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
    <div class="panel" id="crossbox" hidden style="margin-top:12px"></div>
    <div class="panel" id="donebox" hidden style="margin-top:12px"></div>
  </section>
</div>

<script>
var DATA = /*__DATA__*/null;
var rows = DATA.rows, meta = DATA.meta;
var ORIG = JSON.parse(JSON.stringify(rows));   // 生成時の値(人手修正の差分検出用)
function corrections(){
  var out=[];
  rows.forEach(function(r,i){
    var o=ORIG[i]; if(!o) return;
    ['from','to'].forEach(function(s){
      if((r[s].crimp||'')!==((o[s]||{}).crimp||'')){
        out.push({field:'crimp',size:(r.size||''),device:(r[s].device||''),
                  from:((o[s]||{}).crimp||''),to:(r[s].crimp||'')});
      }
    });
  });
  return out;
}
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

/* ---------- 盤間渡り(R-F/L-F・渡り用TB)＋二重検証の確度 ---------- */
function confBadge(c){ var cls=c==='◎'?'ok':(c==='△'?'bad':'mid'); return '<span class="cf '+cls+'">'+esc(c)+'</span>'; }
function renderCrossings(){
  var box=$('crossbox'); if(!box) return;
  var cx=DATA.crossings||{items:[],conflicts:[],faces:0,need:0};
  $('k-cross').textContent=cx.items.length;
  $('k-cross-need').textContent = cx.need? (' / △'+cx.need) : '';
  if(!cx.items.length){ box.hidden=true; return; }
  box.hidden=false;
  var confl=cx.conflicts||[], banner='';
  if(cx.need||confl.length){
    banner='<div class="xwarn">⚠ 要確認 '+cx.need+' 件'
      +(confl.length?('（うち型の不一致 '+confl.length+' 件: '
        +confl.map(function(c){return esc(c[0])+'（端子信号='+esc(c[1])+'／由来図面='+esc(c[2])+'）';}).join('、')+'）'):'')
      +' — △は人が型（制御=R-F/L-F か 太物=渡り用TB）を確定してください。</div>';
  }
  var body=cx.items.map(function(x){
    var relay = (x.relay&&x.relay.length)? ' <span class="tag">中継 面'+x.relay.join('・面')+'</span>' : '';
    var conn = (x.type==='制御')? 'R-F(P)／L-F(R)' : '直接渡り(太物)';
    return '<tr'+(x.conf==='△'?' class="flag"':'')+'>'
      +'<td>'+esc(x.gousen)+'</td><td>'+esc(x.type)+'</td>'
      +'<td style="text-align:center">'+confBadge(x.conf)+'</td>'
      +'<td>'+esc(x.path)+relay+'</td>'
      +'<td style="text-align:center">'+esc(x.boundaries)+'</td>'
      +'<td>'+esc(conn)+' <span class="muted">'+esc(x.core)+'</span></td></tr>';
  }).join('');
  box.innerHTML='<h2>盤間渡り（R-F/L-F=制御[≤2.0sq] ／ 直接渡り=太物[>2.0sq]）'
    +'<span class="hint"> 面数 '+esc(cx.faces)+' ／ 渡り '+cx.items.length+' 件</span></h2>'
    +'<p class="hint">現場ルール(電線サイズ基準): <b>2.0sq以下＝コネクタ(R-F/L-F)</b>（シーケンス図の線は全てここ）、'
    +'<b>2.0sq超＝直接1本渡り</b>（動力フィーダ≥8sq=太物／たまに渡りTB）。コネクタは送り面=R-F(P)・受け面=L-F(R)。'
    +'3面以上は各盤境界にコネクタを置き、中間面は受け＋再送で中継（コア番号は信号ごとに一貫）。'
    +'確度 ◎=端子信号と由来図面が一致／○=端子信号のみ／△=要確認。</p>'
    +'<div class="xnote">⏚ <b>アース(接地・主に5.5sq)</b>は 2.0sq超なので <b>直接渡り（コネクタ不使用）</b>＝各盤の接地端子(ET)を盤間で直接ボンディング。'
    +'ハーネス工程が標準で施工するため、この表（コネクタ/太物）には出ません（色E/緑で区別・計上対象外）。</div>'+banner
    +'<table><thead><tr><th>号線</th><th>型</th><th>確度</th><th>渡り(面の経路)</th>'
    +'<th>境界(コネクタ)数</th><th>コネクタ:コア</th></tr></thead><tbody>'+body+'</tbody></table>';
}

/* ---------- 工程遷移 ---------- */
function goPhase(p){
  phase=p;
  $('phase1').hidden = p!==1; $('phase2').hidden = p!==2;
  $('st1').className='s '+(p>1?'done':'on'); $('st2').className='s '+(p===2?'on':(p>2?'done':''));
  if(p===2){ render(); renderCrossings(); }
  recalc();
}
$('to2').addEventListener('click',function(){ goPhase(2); });
$('back1').addEventListener('click',function(){ goPhase(1); });

/* ---------- ③ 確定・出力＋学習(モデル同一形式Excel/txtを出力し、修正を学習) ---------- */
$('btn-out').addEventListener('click',function(){
  if($('btn-out').disabled) return;
  var done=$('donebox');
  $('btn-out').disabled=true;
  done.hidden=false;
  done.innerHTML='<b>確定処理中…</b> モデル形式Excelを出力し、修正内容を学習しています。';
  fetch('/confirm/'+encodeURIComponent(meta.seiban||''),{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({rows:rows,corrections:corrections()})
  }).then(function(res){return res.json().then(function(j){return {ok:res.ok,j:j};});})
  .then(function(o){
    var j=o.j||{};
    if(!o.ok || !j.ok){ throw new Error((j&&j.error)||'確定に失敗しました'); }
    $('st3').className='s done';
    var L=j.learned||{}, ch=(L['変更点']||[]);
    var chHtml='';
    if(ch.length){ chHtml='<div class="hint" style="margin-top:6px">学習した修正（圧着）: '
      +ch.slice(0,20).map(function(c){return esc(c.key)+'：'+esc(c.before||'(空)')+'→'+esc(c.after);}).join(' ／ ')
      +(ch.length>20?(' 他'+(ch.length-20)+'件'):'')+'</div>'; }
    done.innerHTML='<b class="ok">✔ 確定しました（'+esc(j.wires)+'本）。</b> '
      +'モデル同一フォーマットのExcelを出力しました。既存システムに取り込むとシールシートへ印刷できます。'
      +'<div class="bar" style="margin-top:8px">'
      +'<a class="primary" style="text-decoration:none;display:inline-block" href="'+esc(j.download.xlsx)+'">ハーネスデータ Excel をダウンロード</a>'
      +'<a class="ghost" style="text-decoration:none;display:inline-block;padding:7px 12px;border:1px solid var(--line);border-radius:7px" href="'+esc(j.download.txt)+'">TXT（社内形式）</a>'
      +'</div>'
      +'<div class="hint" style="margin-top:6px">人手修正 '+esc(j.corrections||0)+' 件を受付。'
      +'学習に反映：圧着(サイズ|機器) '+esc(L['修正学習(サイズ|機器)']||0)+' 件'
      +' ／ (機器)新規 '+esc(L['追加(機器)']||0)+' 件。'
      +(((j.corrections||0)===0)?'（修正なし＝生成結果をそのまま確定）':'')+'</div>'
      +chHtml;
  }).catch(function(err){
    done.innerHTML='<b style="color:var(--bad)">確定に失敗しました。</b> '+esc(err.message||err)
      +'<div class="hint">※ この画面はWebアプリ経由で開いてください（ファイル単体では確定できません）。</div>';
    $('btn-out').disabled=false;
  });
});

renderDefects();
renderCrossings();   // 盤間渡りの件数KPIを初期表示(表はphase2で表示)
goPhase(1);
</script></body></html>"""
