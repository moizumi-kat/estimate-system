# -*- coding: utf-8 -*-
"""工程ごとの『図面上で確認できる』確認UI(自己完結HTML)を生成する。

社内資産(図面)はローカルに閉じるため、画像は base64 で HTML に内蔵し、1ファイルで渡せる。
  stage3_fromto(seq_paths, skel_paths, seiban, out_html):
      ハーネス工程 From-To の確認UI。結線検査図(ネット色分け)＋From-To一覧(号線ごと)＋要確認。
"""
import os
import io
import glob
import json as _json
import html as _html
import base64
import collections

from . import export, conn_view, harness_produce


def _img_b64(png_path, max_w=1800):
    """PNGを max_w 幅に縮小して data URI を返す。"""
    from PIL import Image
    im = Image.open(png_path)
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.save(buf, format='PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _fromto_rows(seq_paths, skel_paths, seiban, tmp_dir, term_override=None):
    """export_fromto を実行し、From-To行(号線/種別/From/To)のリストを返す。"""
    os.makedirs(tmp_dir, exist_ok=True)
    export.export_fromto(seq_paths, skel_paths=skel_paths, seiban=seiban,
                         out_dir=tmp_dir, with_watari=True, term_override=term_override)
    csvs = glob.glob(os.path.join(tmp_dir, f'{seiban}_fromto.csv'))
    rows = []
    if csvs:
        import csv as _csv
        with open(csvs[0], encoding='cp932', errors='replace', newline='') as f:
            rd = _csv.reader(f)
            next(rd, None)   # ヘッダ
            for r in rd:
                if len(r) >= 8:
                    rows.append(r)   # [号線,種別,From機,From番,From端,To機,To番,To端]
    return rows


def stage3_fromto(seq_paths, skel_paths, seiban, out_html, tmp_dir=None, term_override=None,
                  save_url=None, rows=None):
    """ハーネス工程 From-To の確認UI(自己完結HTML)を out_html に書く。戻り: 統計 dict。
    term_override: 2人目(端子番号割付)の確定端子で From-To の端子を補正する。
    save_url: 指定するとUIの「保存」はそのURLへJSON POST(サーバ保存)。未指定はCSVダウンロード。
    rows: 事前計算済み From-To 行([号線,種別,From機,From番,From端,To機,To番,To端])を渡すと、
          内部の再計算(_fromto_rows)を行わずそれを使う(完全版=相番号置換済みの行を描画するため)。
          既定 None=従来どおり内部計算(回帰ゼロ)。"""
    tmp_dir = tmp_dir or os.path.join(os.path.dirname(os.path.abspath(out_html)), '_tmp')
    if rows is None:
        rows = _fromto_rows(seq_paths, skel_paths, seiban, tmp_dir, term_override=term_override)
    # 結線検査図(Fシートごと)
    imgs = []
    for p in seq_paths:
        name = os.path.splitext(os.path.basename(p))[0]
        png = os.path.join(tmp_dir, f'conn_{name}.png')
        try:
            st = conn_view.render(p, png)
            imgs.append((name, _img_b64(png), st))
        except Exception as e:
            imgs.append((name, None, {'error': str(e)}))
    # 号線ごとに集計
    by_g = collections.OrderedDict()
    for r in rows:
        by_g.setdefault(r[0], []).append(r)
    kind_of = {g: (rs[0][1] if rs else '') for g, rs in by_g.items()}
    n_ctrl = sum(1 for g in by_g if kind_of[g] == 'ctrl')
    n_main = sum(1 for g in by_g if kind_of[g] == 'main')
    stats = {'gousen': len(by_g), 'wires': len(rows), 'ctrl': n_ctrl, 'main': n_main,
             'sheets': len(seq_paths)}
    # 呼び出し側が From-To データをCSV化できるよう、生の行も返す
    # ([号線,種別,From機,From番,From端,To機,To番,To端]・UIのstats数値には影響しない追加キー)。
    stats['rows'] = rows

    def esc(x):
        return _html.escape(str(x))

    # From-To表
    trs = []
    for g, rs in by_g.items():
        kcls = 'ctrl' if kind_of[g] == 'ctrl' else 'main'
        for i, r in enumerate(rs):
            g_cell = (f'<td class="g {kcls}" rowspan="{len(rs)}">{esc(g)}'
                      f'<span class="kind">{esc(kind_of[g])}</span></td>') if i == 0 else ''
            frm = f"{esc(r[2])}{esc(r[3])}" + (f" :{esc(r[4])}" if r[4] else '')
            to = f"{esc(r[5])}{esc(r[6])}" + (f" :{esc(r[7])}" if r[7] else '')
            trs.append(f'<tr data-g="{esc(g)}" data-k="{esc(kind_of[g])}">{g_cell}'
                       f'<td><input class=ef value="{frm}"></td><td class="ar">→</td>'
                       f'<td><input class=et value="{to}"></td>'
                       f'<td class=ce><input type=checkbox class=ex></td></tr>')
    img_html = ''.join(
        f'<div class="sheet"><div class="sh">{esc(n)}'
        + (f' <span class="st">nets={s.get("nets","?")} 浮端子={s.get("floating","?")} '
           f'ギャップ={s.get("trace_gap","?")}</span>' if 'error' not in s else
           f' <span class="err">描画失敗: {esc(s["error"])}</span>') + '</div>'
        + (f'<img src="{b}">' if b else '') + '</div>'
        for n, b, s in imgs)

    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(seiban)} ハーネス工程 From-To 確認</title><style>
body{{font-family:"IPAGothic","Yu Gothic",sans-serif;margin:0;background:#f4f4f5;color:#1a1a1a;font-size:14px}}
.page{{max-width:1400px;margin:14px auto;background:#fff;padding:18px 22px;box-shadow:0 1px 6px rgba(0,0,0,.15);border-radius:6px}}
h1{{font-size:19px;border-bottom:3px solid #1a1a1a;padding-bottom:8px}}
.stat{{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}}
.stat div{{background:#eef2f7;border:1px solid #cdd7e3;border-radius:6px;padding:6px 12px}}
.stat b{{font-size:17px}}
.cols{{display:flex;gap:16px;flex-wrap:wrap}}
.left{{flex:1;min-width:360px;max-height:1600px;overflow:auto}}
.right{{flex:1.4;min-width:420px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #dcdcdc;padding:3px 7px;text-align:left}}
th{{background:#efefef;position:sticky;top:0}}
.g{{font-weight:bold;vertical-align:top;white-space:nowrap}}
.g.ctrl{{background:#eef7ee}} .g.main{{background:#fdf0e6}}
.kind{{display:block;font-size:10px;color:#777;font-weight:normal}}
.ar{{text-align:center;color:#999}}
.sheet{{margin-bottom:14px}} .sh{{font-weight:bold;margin:4px 0}}
.st{{font-size:12px;color:#555;font-weight:normal}} .err{{color:#c00}}
.sheet img{{width:100%;border:1px solid #ddd;border-radius:4px}}
.note{{font-size:12px;color:#555;margin-top:6px}}
.left input{{border:1px solid #ddd;border-radius:3px;padding:2px 4px;font-size:12.5px;width:92%}}
.ce{{text-align:center}} tr.del input{{text-decoration:line-through;color:#999;background:#f6f6f6}}
.dl{{background:#1565c0;color:#fff;border:none;border-radius:5px;padding:5px 14px;cursor:pointer;margin-left:10px;font-size:13px}}
</style></head><body><div class="page">
<h1>{esc(seiban)}　ハーネス工程 From-To 確認・編集
<button class="dl" onclick="save()">{'保存' if save_url else 'From-To CSV出力（修正反映）'}</button></h1>
<div class="stat">
 <div>号線 <b>{stats['gousen']}</b></div>
 <div>配線(From-To) <b>{stats['wires']}</b></div>
 <div>制御 <b>{n_ctrl}</b></div>
 <div>主回路 <b>{n_main}</b></div>
 <div>シート <b>{stats['sheets']}</b></div>
</div>
<div class="cols">
 <div class="left"><table><tr><th>号線</th><th>From</th><th></th><th>To</th><th>除外</th></tr>
 {''.join(trs)}</table></div>
 <div class="right">
  <div class="note">右＝結線検査図。<b>同じ色＝1つの繋がったネット</b>。<b>From/To は直接編集でき、誤りは「除外」で外せます。</b>修正後「From-To CSV出力」で保存。</div>
  {img_html}
 </div>
</div>
<p class="note">※ 社内資産につき取り扱い注意。From-Toは図面(制御シート)から自動生成。</p>
<script>
const SEIBAN={_json.dumps(seiban, ensure_ascii=False)};
const SAVE_URL={_json.dumps(save_url, ensure_ascii=False)};
document.querySelectorAll('tr[data-g] .ex').forEach(c=>c.onchange=e=>e.target.closest('tr').classList.toggle('del',e.target.checked));
function buildRows(){{let out=[['号線','種別','From','To']];
 document.querySelectorAll('tr[data-g]').forEach(tr=>{{if(tr.querySelector('.ex').checked)return;
  out.push([tr.dataset.g,tr.dataset.k,tr.querySelector('.ef').value,tr.querySelector('.et').value]);}});
 return out;}}
function save(){{const out=buildRows();
 if(SAVE_URL){{const btn=document.querySelector('button.dl');if(btn){{btn.disabled=true;btn.textContent='保存中…';}}
  fetch(SAVE_URL,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{seiban:SEIBAN,kind:'fromto',rows:out}})}})
   .then(r=>r.json()).then(j=>{{if(btn){{btn.disabled=false;btn.textContent='保存';}}alert(j.ok?('サーバに保存しました。'+(j.message||'')):'保存に失敗: '+(j.error||''));}})
   .catch(e=>{{if(btn){{btn.disabled=false;btn.textContent='保存';}}alert('保存に失敗: '+e);}});
 }}else{{const csv='\\ufeff'+out.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\\r\\n');
  const b=new Blob([csv],{{type:'text/csv'}});const a=document.createElement('a');
  a.href=URL.createObjectURL(b);a.download=SEIBAN+'_ハーネス工程FromTo.csv';a.click();}}}}
</script>
</div></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)
    return stats


def _conn_imgs(seq_paths, tmp_dir):
    """各Fシートの結線検査図を (name, data-uri, stats) で返す。"""
    os.makedirs(tmp_dir, exist_ok=True)
    out = []
    for p in seq_paths:
        name = os.path.splitext(os.path.basename(p))[0]
        png = os.path.join(tmp_dir, f'conn_{name}.png')
        try:
            st = conn_view.render(p, png)
            out.append((name, _img_b64(png), st))
        except Exception as e:
            out.append((name, None, {'error': str(e)}))
    return out


def stage2_terminals(seq_paths, skel_paths, seiban, out_html, tmp_dir=None):
    """端子番号割付(端子番号付与)の確認UI(自己完結HTML)を out_html に書く。
    端子台(TB)の端子番号は制御線では未記入(製造工程=端子番号割付が付番)。システムは位置で個別化し
    仮番号を割当て、接続号線とともに提示する。図面(結線検査図)で位置も確認できる。戻り: 統計。"""
    tmp_dir = tmp_dir or os.path.join(os.path.dirname(os.path.abspath(out_html)), '_tmp')
    d = harness_produce.terminal_number_defects(seq_paths, skel_paths=skel_paths, seiban=seiban)
    items = d['items']
    imgs = _conn_imgs(seq_paths, tmp_dir)
    stats = {'tb': len(items), 'kakunin': len(items), 'sheets': len(seq_paths)}

    def esc(x):
        return _html.escape(str(x))

    trs = []
    for it in items:
        gs = '／'.join(str(g) for g in it['gousen']) or '(なし)'
        pos = f"({it['position']['x']:.0f}, {it['position']['y']:.0f})"
        trs.append(
            f'<tr data-pv="{esc(it["provisional"])}"><td class="pv">{esc(it["provisional"])}</td>'
            f'<td><input class=eg value="{esc(gs)}"></td>'
            f'<td><input class=tn placeholder="端子番号を入力"></td>'
            f'<td class=ce><input type=checkbox class=ex></td>'
            f'<td class="pos">{esc(pos)}</td></tr>')
    if not trs:
        trs = ['<tr><td colspan="5" class="none">未記入の端子台はありません</td></tr>']
    img_html = ''.join(
        f'<div class="sheet"><div class="sh">{esc(n)}'
        + ('' if 'error' in s else f' <span class="st">端子/ネット={s.get("nets","?")}</span>')
        + '</div>' + (f'<img src="{b}">' if b else '') + '</div>'
        for n, b, s in imgs)

    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(seiban)} 端子番号割付 端子番号付与 確認</title><style>
body{{font-family:"IPAGothic","Yu Gothic",sans-serif;margin:0;background:#f4f4f5;color:#1a1a1a;font-size:14px}}
.page{{max-width:1400px;margin:14px auto;background:#fff;padding:18px 22px;box-shadow:0 1px 6px rgba(0,0,0,.15);border-radius:6px}}
h1{{font-size:19px;border-bottom:3px solid #1a1a1a;padding-bottom:8px}}
.stat{{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}}
.stat div{{background:#eef2f7;border:1px solid #cdd7e3;border-radius:6px;padding:6px 12px}} .stat b{{font-size:17px}}
.stat .warn{{background:#fff4e5;border-color:#f0c48a}}
.cols{{display:flex;gap:16px;flex-wrap:wrap}}
.left{{flex:1;min-width:360px;max-height:1600px;overflow:auto}} .right{{flex:1.4;min-width:420px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #dcdcdc;padding:4px 8px;text-align:left}}
th{{background:#efefef;position:sticky;top:0}}
.pv{{font-weight:bold;background:#fff7ef;white-space:nowrap}} .need{{color:#c2410c;font-weight:bold;white-space:nowrap}}
.pos{{color:#777;font-size:12px}} .none{{color:#999}}
.sheet{{margin-bottom:14px}} .sh{{font-weight:bold;margin:4px 0}} .st{{font-size:12px;color:#555;font-weight:normal}}
.sheet img{{width:100%;border:1px solid #ddd;border-radius:4px}} .note{{font-size:12px;color:#555;margin:6px 0}}
.left input{{border:1px solid #ddd;border-radius:3px;padding:2px 4px;font-size:12.5px;width:92%}}
.left input.tn{{background:#fffbe9}} .ce{{text-align:center}} tr.del input{{text-decoration:line-through;color:#999;background:#f6f6f6}}
.dl{{background:#1565c0;color:#fff;border:none;border-radius:5px;padding:5px 14px;cursor:pointer;margin-left:10px;font-size:13px}}
</style></head><body><div class="page">
<h1>{esc(seiban)}　端子番号割付 端子番号付与 確認・編集
<button class="dl" onclick="exportCSV()">端子番号CSV出力（修正反映）</button></h1>
<div class="stat">
 <div>端子台(未記入) <b>{stats['tb']}</b></div>
 <div class="warn">要付番 <b>{stats['kakunin']}</b></div>
 <div>シート <b>{stats['sheets']}</b></div>
</div>
<div class="note">制御線の端子台(TB)の端子番号は設計図で未記入＝<b>製造工程(端子番号割付)で付番</b>します。システムは位置で個別化し仮番号を割当て、接続号線を提示します。下表の仮番号に正式な台番号・端子番号を付けてください。</div>
<div class="cols">
 <div class="left"><table><tr><th>仮番号</th><th>接続号線</th><th>端子番号</th><th>除外</th><th>位置</th></tr>
 {''.join(trs)}</table></div>
 <div class="right">
  <div class="note">右＝結線検査図。端子台の位置・接続を図面上で確認できます。<b>黄欄に端子番号を入力、接続号線も編集でき、誤りは「除外」。</b></div>
  {img_html}
 </div>
</div>
<p class="note">※ 社内資産につき取り扱い注意。{esc(d.get('note',''))}</p>
<script>
document.querySelectorAll('tr[data-pv] .ex').forEach(c=>c.onchange=e=>e.target.closest('tr').classList.toggle('del',e.target.checked));
function exportCSV(){{let out=[['仮番号','接続号線','端子番号']];
 document.querySelectorAll('tr[data-pv]').forEach(tr=>{{if(tr.querySelector('.ex').checked)return;
  out.push([tr.dataset.pv,tr.querySelector('.eg').value,tr.querySelector('.tn').value]);}});
 const csv='\\ufeff'+out.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\\r\\n');
 const b=new Blob([csv],{{type:'text/csv'}});const a=document.createElement('a');
 a.href=URL.createObjectURL(b);a.download='{esc(seiban)}_端子番号割付端子番号.csv';a.click();}}
</script>
</div></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)
    return stats


def stage1_mainwire(skel_paths, seiban, out_html, tmp_dir=None):
    """太物工程(太物経路・≥8sq)の確認UI(自己完結HTML)を out_html に書く。
    主回路(スケルトンEシート)から太物の From-To(回路・相)を生成し、相色で一覧化。
    主回路図(結線検査図)で経路を確認できる。実配線長(⑤太物経路)は別入力のため、
    データがあれば後続で付加する前提の雛形。戻り: 統計。"""
    from . import main_circuit
    tmp_dir = tmp_dir or os.path.join(os.path.dirname(os.path.abspath(out_html)), '_tmp')
    wires = main_circuit.generate(skel_paths, seiban=seiban)
    imgs = _conn_imgs(skel_paths, tmp_dir)
    by_c = collections.OrderedDict()
    for w in wires:
        by_c.setdefault(w.get('circuit', '') or '(共通)', []).append(w)
    n_kakutei = sum(1 for w in wires if w.get('confidence') == '確定')
    stats = {'wires': len(wires), 'circuits': len(by_c),
             'kakutei': n_kakutei, 'sheets': len(skel_paths)}
    PHASE = {'U': '#d33', 'V': '#888', 'W': '#36c', 'E': '#2a2',
             'R': '#d33', 'S': '#888', 'T': '#36c', 'N': '#555'}

    def esc(x):
        return _html.escape(str(x))
    trs = []
    for c, ws in by_c.items():
        for i, w in enumerate(ws):
            c_cell = f'<td class="c" rowspan="{len(ws)}">{esc(c)}</td>' if i == 0 else ''
            ph = w.get('phase', '')
            col = PHASE.get(ph, '#333')
            conf = w.get('confidence', '')
            ccls = 'ok' if conf == '確定' else 'maybe'
            trs.append(
                f'<tr>{c_cell}<td><span class="ph" style="background:{col}">{esc(ph)}</span></td>'
                f'<td>{esc(w.get("from",""))}</td><td class="ar">→</td>'
                f'<td>{esc(w.get("to",""))}</td>'
                f'<td class="{ccls}">{esc(conf)}</td></tr>')
    if not trs:
        trs = ['<tr><td colspan="5" class="none">太物(主回路)の接続は検出されませんでした</td></tr>']
    img_html = ''.join(
        f'<div class="sheet"><div class="sh">{esc(n)}'
        + ('' if 'error' in s else f' <span class="st">ネット={s.get("nets","?")}</span>')
        + '</div>' + (f'<img src="{b}">' if b else '') + '</div>'
        for n, b, s in imgs)

    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(seiban)} 太物工程 太物経路 確認</title><style>
body{{font-family:"IPAGothic","Yu Gothic",sans-serif;margin:0;background:#f4f4f5;color:#1a1a1a;font-size:14px}}
.page{{max-width:1400px;margin:14px auto;background:#fff;padding:18px 22px;box-shadow:0 1px 6px rgba(0,0,0,.15);border-radius:6px}}
h1{{font-size:19px;border-bottom:3px solid #1a1a1a;padding-bottom:8px}}
.stat{{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}} .stat div{{background:#eef2f7;border:1px solid #cdd7e3;border-radius:6px;padding:6px 12px}} .stat b{{font-size:17px}}
.cols{{display:flex;gap:16px;flex-wrap:wrap}} .left{{flex:1;min-width:380px;max-height:1600px;overflow:auto}} .right{{flex:1.4;min-width:420px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #dcdcdc;padding:4px 8px;text-align:left}}
th{{background:#efefef;position:sticky;top:0}} .c{{font-weight:bold;vertical-align:top;background:#f3f6fb;white-space:nowrap}}
.ph{{display:inline-block;min-width:16px;text-align:center;color:#fff;border-radius:3px;padding:0 5px;font-weight:bold}}
.ar{{text-align:center;color:#999}} .ok{{color:#2a7a2a}} .maybe{{color:#c2410c}} .none{{color:#999}}
.sheet{{margin-bottom:14px}} .sh{{font-weight:bold;margin:4px 0}} .st{{font-size:12px;color:#555;font-weight:normal}}
.sheet img{{width:100%;border:1px solid #ddd;border-radius:4px}} .note{{font-size:12px;color:#555;margin:6px 0}}
</style></head><body><div class="page">
<h1>{esc(seiban)}　太物工程 太物経路（≥8sq）確認UI</h1>
<div class="stat">
 <div>太物配線 <b>{stats['wires']}</b></div>
 <div>回路 <b>{stats['circuits']}</b></div>
 <div>確定 <b>{n_kakutei}</b></div>
 <div>シート <b>{stats['sheets']}</b></div>
</div>
<div class="note">主回路(スケルトン)から太物(≥8sq)の From-To を相別に抽出。相色＝U/R赤・V/S灰・W/T青・E緑。
<b>実配線長・経路ジオメトリ(⑤太物経路データ)は別入力</b>で、供給されれば各配線に実長を付加できます。</div>
<div class="cols">
 <div class="left"><table><tr><th>回路</th><th>相</th><th>From</th><th></th><th>To</th><th>確度</th></tr>
 {''.join(trs)}</table></div>
 <div class="right">
  <div class="note">右＝主回路の結線検査図。太物の経路を図面上で確認できます。</div>
  {img_html}
 </div>
</div>
<p class="note">※ 社内資産につき取り扱い注意。太物(≥8sq)はハーネス工程From-To(≤5.5sq)とは別工程。</p>
</div></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)
    return stats
