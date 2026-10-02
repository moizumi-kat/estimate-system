# -*- coding: utf-8 -*-
"""端子番号の付与・確認・修正プロトタイプ（新手順の②③④を1画面で確認）。

新しい手順(茂泉様):
  ① 図面を読み込む
  ② 端子番号を自動割り振り＋図面に記載
       ・図面のTERMINAL属性がある接続点  = 真値(図面)
       ・属性が無い接続点                = 学習ルール(terminal_vocab)から候補を提示
  ③ 人が確認し、誤りは修正・抜けは追加記載（編集UI）
  ④ 確定した端子番号を使って From-To Data を作る

本モジュールは②③④を1枚のHTMLで体験できるプロトタイプを生成する。
  ・左 : 端子番号テーブル(接続点ごと)。端子を編集でき、候補ボタンで学習値を入れられる。
  ・右 : 図面SVG。端子番号を各接続点に記載。編集すると図面ラベルが即時更新。
  ・下 : From-To プレビュー。端子を編集すると、その端子を参照する From-To 行が即時更新。
         （＝確定端子で From-To を作る、という④の流れを目で確認できる）
後日この確定テーブルをサーバ保存し、route_and_length へ端子上書きとして渡してWeb化する。
"""
import os
import json
import html as _html

from . import dxf_svg, tracer, harness_learn as _hl


def _esc(s):
    return _html.escape(str(s if s is not None else ''))


def _sym(dev, no):
    return f'{dev}-{no}' if no else str(dev)


def _collect_points(path):
    """1シート → 接続点リスト。各点 {sym, base, wx, wy, term, src, cand}。
    src: '図面'(属性あり) / '未'(属性なし=候補提示)。同一(sym,位置)は集約。"""
    pts = {}
    try:
        nodes = tracer.trace_nodes(path)
    except Exception:
        return []
    for g, nd in nodes.items():
        for mem in nd.get('members', []):
            try:
                d, t, x, y = mem
            except Exception:
                continue
            sym = str(d)
            tt = str(t).strip()
            key = (sym, round(x, 1), round(y, 1))
            cell = pts.get(key)
            if cell is None:
                cell = {'sym': sym, 'base': _hl._dev_base(sym), 'wx': x, 'wy': y,
                        'term': '', 'src': '未'}
                pts[key] = cell
            if tt and tt != '?' and not cell['term']:
                cell['term'] = tt
                cell['src'] = '図面'
    out = list(pts.values())
    for c in out:
        c['cand'] = sorted(_hl.terminal_vocab(c['sym']))
        # 候補が1つだけなら候補を初期値として提示(紫=候補)。複数は人が選ぶ(未のまま)。
        if c['src'] == '未' and len(c['cand']) == 1:
            c['term'] = c['cand'][0]
            c['src'] = '候補'
    return out


def build_html(seq_paths, skel_paths, seiban='', routed=None):
    """端子番号の付与・確認プロトタイプHTMLを生成して返す。"""
    sheet_paths = list(skel_paths or []) + list(seq_paths or [])
    sheets = []        # {name, r, pts(list with id/X/Y)}
    gkey_to_id = {}    # (sym, round(wx), round(wy)) -> point id（From-To紐付け用, 世界座標）
    pid = 0
    for si, p in enumerate(sheet_paths):
        try:
            r = dxf_svg.render([p])
        except Exception:
            continue
        pts = _collect_points(p)
        xmin, ymax = r['xmin'], r['ymax']
        for c in pts:
            c['id'] = f'pt{pid}'
            c['X'] = round(c['wx'] - xmin, 1)
            c['Y'] = round(ymax - c['wy'], 1)
            gkey_to_id[(c['sym'], round(c['wx'], 1), round(c['wy'], 1))] = c['id']
            pid += 1
        sheets.append({'name': os.path.basename(p), 'r': r, 'pts': pts})

    # 図面ラベル＋未確認マーカー
    draw = ''
    pt_js = {}       # id -> {sym, base, cand, src}
    term_js = {}     # id -> 現在の端子
    for si, sh in enumerate(sheets):
        r = sh['r']
        labels, marks = [], []
        for c in sh['pts']:
            pt_js[c['id']] = {'sym': c['sym'], 'base': c['base'], 'cand': c['cand'], 'src': c['src']}
            term_js[c['id']] = c['term']
            cls = {'図面': 'ok', '候補': 'cand', '未': 'none'}.get(c['src'], 'none')
            labels.append(
                f'<text id="lab-{c["id"]}" class="tl {cls}" data-pt="{c["id"]}" '
                f'x="{c["X"] + 2}" y="{c["Y"] - 2}">{_esc(c["term"])}</text>')
            # 未確定(未/候補)の点は空いている所が分かるよう小さな四角マーカー
            marks.append(
                f'<rect id="mk-{c["id"]}" class="pm {cls}" data-pt="{c["id"]}" '
                f'x="{c["X"] - 4}" y="{c["Y"] - 4}" width="8" height="8" rx="1.5"/>')
        draw += (f'<div class="sheet"><div class="sh">{_esc(sh["name"])}</div>'
                 f'<div class="svgbox"><svg viewBox="0 0 {r["w"]} {r["h"]}" preserveAspectRatio="xMidYMid meet">'
                 f'<g class="dwg">{r["body"]}</g>'
                 f'<g class="pmk">{"".join(marks)}</g>'
                 f'<g class="ov-term">{"".join(labels)}</g></svg></div></div>')

    # 端子番号テーブル(機器ごと)
    by_dev = {}
    for sh in sheets:
        for c in sh['pts']:
            by_dev.setdefault(c['sym'], []).append(c)
    rows = ''
    for sym in sorted(by_dev):
        cs = by_dev[sym]
        rows += f'<tr class="dh"><td colspan="4">{_esc(sym)} <span class="bse">[{_esc(cs[0]["base"])}]</span></td></tr>'
        for c in cs:
            chips = ''.join(f'<button class="chip" data-pt="{c["id"]}" data-v="{_esc(v)}">{_esc(v)}</button>'
                            for v in c['cand'][:14])
            rows += (f'<tr data-pt="{c["id"]}" class="pr">'
                     f'<td class="src"><span class="b {("图" if c["src"]=="図面" else "c" if c["src"]=="候補" else "n")}">{_esc(c["src"])}</span></td>'
                     f'<td class="ti"><input class="ei" data-pt="{c["id"]}" value="{_esc(c["term"])}"></td>'
                     f'<td class="cd">{chips}</td>'
                     f'<td class="jp"><button class="go" data-pt="{c["id"]}">図面</button></td></tr>')

    # From-To プレビュー(確定端子で作る④の流れ)。各端点を接続点IDに紐付け。
    ft_rows = ''
    if routed:
        for i, w in enumerate(routed.get('wires', [])):
            frm, to = w['from'], w['to']
            fsym = _sym(frm.get('device', ''), frm.get('no', ''))
            tsym = _sym(to.get('device', ''), to.get('no', ''))
            route = w.get('route') or []
            fp = w.get('from_pos') or (route[0] if route else None)
            tp = w.get('to_pos') or (route[-1] if route else None)
            fid = gkey_to_id.get((fsym, round(fp[0], 1), round(fp[1], 1))) if fp else None
            tid = gkey_to_id.get((tsym, round(tp[0], 1), round(tp[1], 1))) if tp else None
            fpf = f'data-pt="{fid}"' if fid else ''
            tpf = f'data-pt="{tid}"' if tid else ''
            ft_rows += (
                f'<tr><td class="num">{i+1}</td><td>{_esc(w.get("gousen",""))}</td>'
                f'<td>{_esc((frm.get("color") or "")+(w.get("kind") or ""))}{_esc(w.get("size",""))}</td>'
                f'<td>{_esc(fsym)}:<span class="ftt" {fpf}>{_esc(frm.get("terminal",""))}</span></td>'
                f'<td>{_esc(tsym)}:<span class="ftt" {tpf}>{_esc(to.get("terminal",""))}</span></td>'
                f'<td class="num">{_esc(w.get("length",""))}</td></tr>')

    total_pts = sum(len(sh['pts']) for sh in sheets)
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_esc(seiban)} 端子番号付与</title>
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;background:#eef1f5;color:#16233c;font-family:"Noto Sans JP","Yu Gothic",sans-serif;font-size:12.5px}}
 header{{background:#1f4fb0;color:#fff;padding:8px 14px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;position:sticky;top:0;z-index:6}}
 header .ttl{{font-size:15px;font-weight:700}} header a{{color:#cfe0ff}}
 .cnt{{font-weight:700}} .cnt b{{font-size:15px}} .cnt .u{{color:#ffd9b0}}
 .steps{{font-size:11px;opacity:.9}}
 .main{{display:flex;gap:10px;align-items:flex-start;padding:10px}}
 .left{{flex:0 0 42%;max-width:42%;display:flex;flex-direction:column;gap:8px}}
 .right{{flex:1}}
 .card{{background:#fff;border:1px solid #ccd3de;border-radius:8px}}
 .ch{{padding:6px 10px;font-weight:700;border-bottom:1px solid #eef1f5;display:flex;gap:8px;align-items:center}}
 .listbox{{max-height:46vh;overflow:auto}}
 table{{border-collapse:collapse;width:100%}} th,td{{border-bottom:1px solid #eef1f5;padding:3px 6px;text-align:left}}
 tr.dh td{{background:#eef1f6;font-weight:700;position:sticky;top:0}} .bse{{color:#6a7686;font-weight:400;font-size:11px}}
 tr.pr:hover{{background:#f2f6ff}} td.num{{text-align:right}}
 .ei{{width:72px;font:inherit;border:1px solid #ccd3de;border-radius:5px;padding:2px 5px}}
 .pr.sel{{background:#fff3df}} .pr.sel .ei{{border-color:#e0a800;background:#fffdf5}}
 .chip{{font:inherit;font-size:11px;border:1px solid #d9c4ef;background:#f7f0ff;color:#6a1b9a;border-radius:9px;padding:1px 7px;margin:1px;cursor:pointer}}
 .chip:hover{{background:#6a1b9a;color:#fff}}
 .go{{font:inherit;font-size:11px;border:1px solid #ccd3de;background:#fff;border-radius:6px;padding:1px 7px;cursor:pointer}}
 .b{{font-size:10.5px;padding:1px 6px;border-radius:9px;font-weight:700}}
 .b.图{{background:#e6f4ea;color:#1a7a3a}} .b.c{{background:#f3e9ff;color:#6a1b9a}} .b.n{{background:#ffe8d4;color:#9a4a00}}
 .svgbox{{overflow:auto}} svg{{width:100%;height:auto;min-width:900px;background:#fff}}
 .dwg line,.dwg path,.dwg circle{{fill:none;stroke:#334;stroke-width:0.4;vector-effect:non-scaling-stroke}}
 .dwg .wm{{stroke:#c0392b}} .dwg .we{{stroke:#1a9a3a}} .dwg .wc{{stroke:#666}} .dwg .gm{{stroke:#aab4c2}}
 .dwg .sol{{fill:#334;stroke:none}} .dwg text{{fill:#333;font-family:sans-serif}}
 .ov-term .tl{{font-size:10px;font-weight:800;font-family:sans-serif;paint-order:stroke;stroke:#fff;stroke-width:2.6px;stroke-linejoin:round;cursor:pointer}}
 .ov-term .tl.ok{{fill:#117a35}} .ov-term .tl.cand{{fill:#7a1fd0}} .ov-term .tl.none{{fill:#d35400}}
 .ov-term .tl.sel{{fill:#ff2d00;font-size:13px;stroke-width:3px}}
 .pmk .pm{{fill:#fff;fill-opacity:.65;stroke-width:1.6px;vector-effect:non-scaling-stroke;cursor:pointer}}
 .pmk .pm.none{{stroke:#e0820f}} .pmk .pm.cand{{stroke:#8a3bd0}} .pmk .pm.ok{{display:none}}
 .pmk .pm.sel{{stroke:#ff2d00;stroke-width:2.6px;display:block}}
 #dedit{{position:fixed;z-index:50;display:none;width:84px;font:inherit;font-weight:700;
   border:2px solid #e0a800;border-radius:6px;padding:3px 6px;background:#fffdf5;box-shadow:0 3px 12px rgba(0,0,0,.25)}}
 #dhint{{position:fixed;z-index:50;display:none;background:#16233c;color:#fff;font-size:11px;
   padding:3px 7px;border-radius:6px;transform:translateY(-120%)}}
 .sheet{{margin-bottom:10px}} .sheet .sh{{padding:5px 8px;font-weight:700;border-bottom:1px solid #eef1f5}}
 .ftt.hot{{color:#1f4fb0;font-weight:700}}
 .note{{font-size:11px;color:#55607a;padding:4px 10px}}
</style></head>
<body>
<header><span class="ttl">端子番号の付与・確認 {_esc(seiban)}</span>
 <span class="cnt">確定 <b id="cok">0</b> / {total_pts} 点 ・ <span class="u">未確定 <b id="cun">0</b></span></span>
 <span class="steps">②自動付与 → ③確認・修正・追加 → ④確定端子で From-To</span>
 <span style="flex:1"></span>
 <button class="go" id="fill">候補を一括適用(単一候補のみ)</button></header>
<div class="main">
 <div class="left">
  <div class="card"><div class="ch">端子番号テーブル（図=図面の真値 / 候補=学習ルール / 未=要入力）</div>
   <div class="listbox"><table><tbody>{rows}</tbody></table></div></div>
  <div class="card"><div class="ch">④ From-To プレビュー（確定端子で生成）</div>
   <div class="note">端子を直すと、その端子を使う From-To 行が即時更新されます（＝確定端子で From-To を作る流れ）。</div>
   <div class="listbox" style="max-height:30vh"><table>
    <thead><tr><th>#</th><th>号線</th><th>種/ｻｲｽﾞ</th><th>From</th><th>To</th><th>測長</th></tr></thead>
    <tbody>{ft_rows or '<tr><td colspan=6 class=note>From-To未生成</td></tr>'}</tbody></table></div></div>
 </div>
 <div class="right card"><div class="ch">図面（端子番号をクリックで直接編集。□=未確定の接続点もクリックで入力）</div>{draw}</div>
</div>
<input id="dedit" autocomplete="off"><div id="dhint"></div>
<script>
var POINTS={json.dumps(pt_js, ensure_ascii=False)};
var TERMS={json.dumps(term_js, ensure_ascii=False)};
function srcClass(pid){{var v=(TERMS[pid]||'').trim();if(!v)return 'none';
  return POINTS[pid].src==='図面'?'ok':'cand';}}
function renderPoint(pid){{
  var v=(TERMS[pid]||'').trim();var cls=srcClass(pid);
  var lab=document.getElementById('lab-'+pid);
  if(lab){{lab.textContent=v;lab.setAttribute('class','tl '+cls+(lab.classList.contains('sel')?' sel':''));}}
  var mk=document.getElementById('mk-'+pid);
  if(mk){{mk.setAttribute('class','pm '+cls+(mk.classList.contains('sel')?' sel':''));}}
  document.querySelectorAll('.ftt[data-pt="'+pid+'"]').forEach(function(s){{s.textContent=v;s.classList.add('hot');}});
  // テーブル行の出所バッジ
  var tr=document.querySelector('tr.pr[data-pt="'+pid+'"]');
  if(tr){{var b=tr.querySelector('.b');if(b){{
    if(!v){{b.textContent='未';b.className='b n';}}
    else if(POINTS[pid].src==='図面'){{b.textContent='図';b.className='b 图';}}
    else{{b.textContent='候補';b.className='b c';}}}}}}
}}
function setTerm(pid,val){{TERMS[pid]=val;renderPoint(pid);counts();}}
function counts(){{var ok=0,un=0;for(var k in TERMS){{((TERMS[k]||'').trim()?ok++:un++);}}
  document.getElementById('cok').textContent=ok;document.getElementById('cun').textContent=un;}}
// 図面上で端子番号を直接編集する小さな入力欄
var ded=document.getElementById('dedit'), dhint=document.getElementById('dhint'), edPid=null;
function closeEditor(apply){{
  if(edPid!==null&&apply){{setTerm(edPid,ded.value.trim());var inp=document.querySelector('.ei[data-pt="'+edPid+'"]');if(inp)inp.value=ded.value.trim();}}
  ded.style.display='none';dhint.style.display='none';edPid=null;
}}
function openEditor(pid){{
  selPoint(pid);edPid=pid;
  var el=document.getElementById('lab-'+pid)||document.getElementById('mk-'+pid);
  var rc=el.getBoundingClientRect();
  ded.value=(TERMS[pid]||'');
  ded.style.left=Math.min(window.innerWidth-96,rc.left)+'px';
  ded.style.top=(rc.bottom+4)+'px';ded.style.display='block';
  var P=POINTS[pid];
  dhint.textContent=P.sym+(P.cand&&P.cand.length?('  候補: '+P.cand.slice(0,10).join(' / ')):'  候補なし(手入力)');
  dhint.style.left=ded.style.left;dhint.style.top=ded.style.top;dhint.style.display='block';
  ded.focus();ded.select();
}}
ded.addEventListener('keydown',function(e){{if(e.key==='Enter'){{closeEditor(true);}}else if(e.key==='Escape'){{closeEditor(false);}}}});
ded.addEventListener('blur',function(){{closeEditor(true);}});
var sel=null;
function selPoint(pid){{
  if(sel){{document.querySelectorAll('[data-pt="'+sel+'"]').forEach(function(e){{e.classList.remove('sel');}});}}
  sel=pid;
  var lab=document.getElementById('lab-'+pid);if(lab)lab.classList.add('sel');
  var mk=document.getElementById('mk-'+pid);if(mk)mk.classList.add('sel');
  var tr=document.querySelector('tr.pr[data-pt="'+pid+'"]');if(tr){{tr.classList.add('sel');}}
  if(lab)lab.scrollIntoView({{block:'center',inline:'center',behavior:'smooth'}});
}}
document.addEventListener('input',function(e){{if(e.target.classList.contains('ei')){{
  setTerm(e.target.getAttribute('data-pt'),e.target.value);}}}});
document.addEventListener('click',function(e){{
  var t=e.target;
  if(t===ded)return;
  // 図面上の端子番号/マーカーを直接クリック → その場で編集
  if(t.classList&&(t.classList.contains('tl')||t.classList.contains('pm'))&&t.getAttribute('data-pt')){{
    openEditor(t.getAttribute('data-pt'));return;}}
  if(t.classList.contains('chip')){{var pid=t.getAttribute('data-pt');var v=t.getAttribute('data-v');
    TERMS[pid]=v;var inp=document.querySelector('.ei[data-pt="'+pid+'"]');if(inp)inp.value=v;
    renderPoint(pid);counts();selPoint(pid);return;}}
  if(t.classList.contains('go')&&t.id!=='fill'){{selPoint(t.getAttribute('data-pt'));return;}}
  var pr=t.closest('tr.pr');if(pr){{selPoint(pr.getAttribute('data-pt'));}}
}});
document.getElementById('fill').addEventListener('click',function(){{
  var n=0;for(var k in POINTS){{if(POINTS[k].src==='候補'&&POINTS[k].cand.length===1&&!(TERMS[k]||'').trim()){{
    TERMS[k]=POINTS[k].cand[0];var inp=document.querySelector('.ei[data-pt="'+k+'"]');if(inp)inp.value=TERMS[k];
    renderPoint(k);n++;}}}}
  counts();
}});
// 初期表示反映
for(var k in TERMS){{renderPoint(k);}}
counts();
</script></body></html>'''


def write(seq_paths, skel_paths, path, seiban='', routed=None):
    html = build_html(seq_paths, skel_paths, seiban=seiban, routed=routed)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path
