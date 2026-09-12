# -*- coding: utf-8 -*-
"""工業電気検図システム（現場用・単独アプリ）

工場現場で使う図面不具合チェック。営業が使う積算コード選定システム(app.py)とは
別プロセス・別アドレス・別ログインで動かす（部署が違うため入口ごと分ける）。
検図ロジックは wireharness.fromto_qc をそのまま利用する。

起動:
  開発        python kenzu_app.py            # PORT 省略時 8001
  本番(EC2)   gunicorn kenzu_app:app --workers 3 --timeout 300 --bind 127.0.0.1:8001

環境変数:
  KENZU_PASSWORD              画面ログイン用パスワード（未設定なら認証オフ＝ローカル試用のみ）
  KENZU_SECRET               セッション署名鍵（未設定なら起動毎にランダム）
  KENZU_ANTHROPIC_API_KEY    R6(SPD警報)のAI補助用。積算とは別キー。無ければR6は自動スキップ。
  KENZU_GEMINI_API_KEY       同上(Gemini)。ANTHROPIC が無い時のフォールバック。
  KENZU_DATA_DIR             検図結果・フィードバックの保存先(積算db.jsonと別。既定 <repo>/kenzu_data)
"""
import os
import io
import zipfile
import tempfile
import hmac
import secrets
import functools
from flask import Flask, request, jsonify, Response, session, redirect
import kenzu_store

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 45 * 1024 * 1024  # 45MB（DXF/ZIP用）
app.secret_key = os.environ.get('KENZU_SECRET', secrets.token_hex(16))
KENZU_PASSWORD = os.environ.get('KENZU_PASSWORD', '')

# --- APIキーの分離 ---
# 検図は積算とは別のキーを使う。KENZU_ 接頭辞のキーが設定されていれば、この
# プロセス内でだけ ANTHROPIC_API_KEY / GEMINI_API_KEY に反映する（vision.py が読む）。
# 別プロセス(別 EnvironmentFile)なので、同一EC2でも積算のキーとは混ざらない。
for _src, _dst in (('KENZU_ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY'),
                   ('KENZU_GEMINI_API_KEY', 'GEMINI_API_KEY')):
    if os.environ.get(_src):
        os.environ[_dst] = os.environ[_src]


def login_required(f):
    @functools.wraps(f)
    def w(*a, **k):
        if not KENZU_PASSWORD:            # 未設定時は素通り（ローカル用）
            return f(*a, **k)
        if session.get('auth'):
            return f(*a, **k)
        if request.path.startswith('/api/'):
            return jsonify(error='未認証'), 401
        return redirect('/login')
    return w


LOGIN_HTML = """<!DOCTYPE html><html lang=ja><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>工業電気検図システム ログイン</title>
<style>body{font-family:'Yu Gothic',Meiryo,sans-serif;background:#f7f5ef;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.box{background:#fff;border:1px solid #d6d1c4;border-radius:10px;padding:32px;width:320px}
h1{font-size:16px;color:#1e3a28;margin:0 0 18px}input{width:100%;padding:10px;border:1px solid #d6d1c4;
border-radius:6px;font-size:14px;box-sizing:border-box}button{width:100%;margin-top:12px;padding:11px;
background:#1e3a28;color:#fff;border:0;border-radius:6px;font-size:14px;font-weight:700;cursor:pointer}
.e{color:#c0504d;font-size:12px;margin-top:10px}</style></head>
<body><form class=box method=post action=/login>
<h1>工業電気検図システム</h1>
<input type=password name=pw placeholder=パスワード autofocus>
<button>ログイン</button>
{ERR}</form></body></html>"""


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not KENZU_PASSWORD:
        return redirect('/')
    if request.method == 'POST':
        if hmac.compare_digest(request.form.get('pw', ''), KENZU_PASSWORD):
            session['auth'] = True
            return redirect('/')
        return Response(LOGIN_HTML.replace('{ERR}', '<div class=e>パスワードが違います</div>'),
                        mimetype='text/html')
    return Response(LOGIN_HTML.replace('{ERR}', ''), mimetype='text/html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ===== 検図エンジン呼び出し =====
def _kenzu_models(files):
    """アップロードされた FileStorage 群→[(filename, DrawingModel)]。
    ZIP は展開。DXF 以外は警告に回す。一時ファイル経由(ezdxf はパス読込)。"""
    from wireharness.fromto_qc.geometry import DrawingModel
    models = []
    warnings = []
    tmps = []

    def _one(fname, raw):
        try:
            with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as tf:
                tf.write(raw)
                path = tf.name
            tmps.append(path)
            models.append((fname, DrawingModel(path)))
        except Exception as e:
            warnings.append(f'{fname}: DXF読込失敗 {e}')
    for f in files:
        if not f or not f.filename:
            continue
        fname = f.filename
        raw = f.read()
        low = fname.lower()
        if low.endswith('.zip'):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for inner in z.namelist():
                        if inner.lower().endswith('.dxf') and not inner.endswith('/'):
                            _one(os.path.basename(inner), z.read(inner))
            except Exception as e:
                warnings.append(f'{fname}: ZIP展開失敗 {e}')
        elif low.endswith('.dxf'):
            _one(fname, raw)
        else:
            warnings.append(f'{fname}: 非対応形式(検図はDXF/ZIPのみ)')
    return models, warnings, tmps


def _kenzu_run(seq, skel, layouts, tables, ai=False):
    """系統別モデルに R1-R7＋H1-H5 を実行。列盤(複数面)対応で layouts/tables は複数可。"""
    from wireharness.fromto_qc import defect_check as dc
    from wireharness.fromto_qc import harness_qc
    layouts = [x for x in (layouts or []) if x]
    tables = [x for x in (tables or []) if x]
    physical = list(skel) + layouts
    findings = []
    if seq and layouts:
        findings += dc.rule_R1_layout_missing(seq + skel, layouts)
    for m in skel:
        findings += dc.rule_R2_capacity(m)
    for m in skel:
        findings += dc.rule_R3_neutral(m)
    if seq and physical:
        findings += dc.rule_R4_orphan_contact(seq, physical)
    for m in seq + skel + layouts:
        findings += dc.rule_R7_earth(m)
    for t in tables:
        findings += dc.rule_R5_confirmation_table(t)
    if ai:
        for m in seq:
            findings += dc.rule_R6_spd_gemini(m)
    for m in seq + skel:
        for iss in harness_qc.check(m):
            findings.append({'rule': iss['id'], 'severity': iss['severity'], 'confidence': 'med',
                             '場所': str(iss.get('xy')), '問題': iss['message'],
                             '提案': '号線の付与/結線/端子台中継を確認してください。',
                             '根拠': 'ハーネス化検図(harness_qc)'})
    return findings


_HARNESS_H = ('H1', 'H2', 'H3', 'H4', 'H5')  # ハーネス化(号線)チェック＝生成の可否ゲート


def _harness_fromto(seq, skel, layouts):
    """検図済みモデルから機器to機器 From-To を決定論生成。
    戻り: {'rows':[{号線,from,to,size,len,stat}], 'groups':[...], 'summary':{...}} / 生成不可なら None。"""
    if not (seq or skel):
        return None
    from wireharness.fromto_qc import harness as H
    seq_paths = [m.path for m in seq]
    skel_paths = [m.path for m in skel]
    layout_path = (layouts[0].path if layouts else
                   (seq_paths[0] if seq_paths else (skel_paths[0] if skel_paths else None)))
    fused = H.fuse(seq_paths, skel_paths, layout_path)
    rows, groups = [], []
    for sid, net in fused['nets'].items():
        size = net.get('wire_size', '')
        if net.get('wires'):
            for w in net['wires']:
                a, b = w[0], w[1]
                length = w[2] if len(w) > 2 else 0
                rows.append({'号線': sid, 'from': a, 'to': b, 'size': size,
                             'len': int(round(length)), 'stat': '確定(位置あり)'})
        elif len(net.get('devices', [])) >= 2:
            groups.append({'号線': sid, 'devs': list(net['devices']), 'size': size,
                           'stat': '連結のみ(測長なし)'})
    return {'rows': rows, 'groups': groups, 'summary': H.summary(fused)}


@app.route('/api/health')
def health():
    return jsonify(ok=True, app='kenzu',
                   key=bool(os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('GEMINI_API_KEY')))


@app.route('/api/kenzu', methods=['POST'])
@login_required
def api_kenzu():
    from wireharness.fromto_qc import sheet_classify as sc
    files = request.files.getlist('file') or ([request.files['file']] if 'file' in request.files else [])
    if not files:
        return jsonify(error='ファイルがありません(DXF/ZIP)'), 400
    ai = str(request.form.get('ai', '')).lower() in ('1', 'true', 'on', 'yes')
    models, warnings, tmps = _kenzu_models(files)
    try:
        if not models:
            return jsonify(error='読み込めるDXFがありません', warnings=warnings), 400
        buckets = sc.classify_files(models)
        seq = buckets['seq']
        skel = buckets['skel']
        layouts = buckets['layout_all']
        tables = buckets['table_all']
        findings = _kenzu_run(seq, skel, layouts, tables, ai)
        # 二重指摘の除去(同一 rule/場所/問題)
        seen = set()
        uniq = []
        for f in findings:
            k = (f.get('rule'), f.get('場所'), f.get('問題'))
            if k in seen:
                continue
            seen.add(k)
            uniq.append(f)
        classification = [{'file': fn, 'role': role} for fn, role in buckets['map']]
        counts = {}
        for f in uniq:
            counts[f.get('rule', '?')] = counts.get(f.get('rule', '?'), 0) + 1
        # ハーネス From-To 生成（検図の後段）。H1-H5(ハーネス化)指摘が無ければ「検図OK」。
        harness = None
        try:
            hp = _harness_fromto(seq, skel, layouts)
            if hp is not None:
                h_issues = sum(1 for f in uniq if f.get('rule') in _HARNESS_H)
                hp['ok'] = (h_issues == 0)
                hp['h_issues'] = h_issues
                harness = hp
        except Exception:
            import traceback
            traceback.print_exc()
        # 検図専用ストアに実行を保存し、各指摘に安定ID(fid)を付けて返す
        # （設計のフィードバック紐付け用）。保存失敗でも検図結果は返す。
        run_id = None
        try:
            meta = {'files': [c['file'] for c in classification],
                    'classification': classification, 'ai': ai, 'summary': counts,
                    'harness': harness}
            run_id, uniq = kenzu_store.save_run(meta, uniq)
        except Exception:
            import traceback
            traceback.print_exc()
        return jsonify(ok=True, run_id=run_id, count=len(uniq), summary=counts,
                       classification=classification, findings=uniq,
                       harness=harness, ai=ai, warnings=warnings)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(error=str(e), warnings=warnings), 500
    finally:
        for p in tmps:
            try:
                os.unlink(p)
            except Exception:
                pass


@app.route('/api/feedback', methods=['POST'])
@login_required
def api_feedback():
    """設計の判定を検図専用ストアに記録する。
    body(JSON): {run_id, fid, disposition:'fixed'|'false_positive'|'missed', rule?, note?}
    見逃し(missed)は fid 不要・rule/note で記述。"""
    d = request.get_json(silent=True) or {}
    disp = d.get('disposition')
    if disp not in kenzu_store.DISPOSITIONS:
        return jsonify(error=f"disposition は {kenzu_store.DISPOSITIONS} のいずれか"), 400
    try:
        rec = kenzu_store.add_feedback(
            run_id=d.get('run_id'), disposition=disp, rule=d.get('rule'),
            fid=d.get('fid'), note=d.get('note', ''),
            user=(session.get('user') or ''))
        return jsonify(ok=True, record=rec)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route('/api/stats')
@login_required
def api_stats():
    """バージョンアップ判断用の集計（ルール別 是正/誤検知/見逃し、適合率など）。"""
    return jsonify(kenzu_store.stats())


@app.route('/api/harness.csv')
@login_required
def api_harness_csv():
    """保存済み run の ハーネス From-To を CSV でダウンロード（iPhone対応=GET）。"""
    import io as _io
    import csv as _csv
    run_id = request.args.get('run', '')
    rec = kenzu_store.get_run(run_id) if run_id else None
    h = ((rec or {}).get('meta') or {}).get('harness') if rec else None
    if not h:
        return Response('From-Toデータがありません（先に検図を実行してください）',
                        status=404, mimetype='text/plain; charset=utf-8')
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(['号線', 'From機器', 'To機器', '電線サイズ', '配線長(mm)', '状態'])
    for r in h.get('rows', []):
        w.writerow([r.get('号線', ''), r.get('from', ''), r.get('to', ''),
                    r.get('size', ''), r.get('len', ''), r.get('stat', '')])
    for g in h.get('groups', []):
        w.writerow([g.get('号線', ''), '｜'.join(g.get('devs', [])), '',
                    g.get('size', ''), '', g.get('stat', '')])
    data = '﻿' + buf.getvalue()   # BOM: Excel(日本語)で文字化けしない
    return Response(data, mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename=fromto_{run_id or "kenzu"}.csv'})


@app.route('/')
@login_required
def index():
    return Response(KENZU_HTML, mimetype='text/html')


KENZU_HTML = """<!DOCTYPE html><html lang=ja><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>工業電気検図システム</title>
<style>
*{box-sizing:border-box}
body{font-family:'Yu Gothic',Meiryo,sans-serif;background:#f7f5ef;margin:0;color:#26332b;-webkit-text-size-adjust:100%;overflow-x:hidden}
header{background:#1e3a28;color:#f4f1ea;padding:14px 18px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
header h1{font-size:17px;margin:0;font-weight:700;letter-spacing:.04em}
header .ver{font-size:11px;opacity:.7}
.wrap{max-width:960px;margin:0 auto;padding:20px 16px 60px}
h2{font-size:16px;color:#1e3a28;margin:0 0 4px}.sub{color:#6b7a70;font-size:13px;margin:0 0 14px}
.help{background:#f0f4ef;border:1px solid #d9e0d6;border-radius:8px;padding:12px 14px;margin:0 0 16px;font-size:13px;color:#3a4a3f;line-height:1.6}
.help-t{font-weight:700;color:#1e3a28;margin-bottom:6px}
.help ul{margin:0;padding-left:18px}.help li{margin:3px 0}
.card{background:#fff;border:1px solid #d9d3c5;border-radius:10px;padding:16px;margin-bottom:16px;overflow-x:auto}
.drop{display:block;width:100%;border:2px dashed #b9c2b8;border-radius:10px;padding:22px 14px;text-align:center;color:#5c6b60;
 background:#fbfaf6;cursor:pointer;font-size:14px}.drop.hi{background:#eef4ea;border-color:#1e3a28}
input[type=file]{display:none}
.actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:12px}
.btn{background:#1e3a28;color:#fff;border:0;border-radius:7px;padding:11px 20px;font-size:14px;
 font-weight:700;cursor:pointer}.btn:disabled{opacity:.5;cursor:default}
label.chk{font-size:13px;color:#4a584f;user-select:none;display:flex;align-items:center;gap:5px}
.files{font-size:13px;color:#4a584f;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #e2ddd0;padding:7px 9px;text-align:left;vertical-align:top}
th{background:#f0ede3;color:#3a4a3f;font-weight:700;white-space:nowrap}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:700}
.hi9{background:#f6d9d5;color:#8a2b22}.med{background:#f6eccf;color:#8a6a1e}.low{background:#e2e8e2;color:#4a584f}
.role{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;background:#e8efe6;color:#33513f;margin-right:4px}
.sum{font-size:14px;margin:6px 0 14px}.sum b{color:#1e3a28}
.muted{color:#94a094;font-size:12px}.err{color:#c0504d;font-size:13px}
.rule{font-weight:700;color:#1e3a28}
.btn2{display:inline-block;background:#1e3a28;color:#fff;text-decoration:none;border-radius:7px;padding:9px 16px;font-size:13px;font-weight:700}
.hok{border-left:5px solid #3a7d4f}.hng{border-left:5px solid #c79a2e}
</style></head><body>
<header><h1>工業電気検図システム</h1><span class="ver">現場用</span></header>
<div class=wrap>
<h2>図面検図（設計次工程チェック）</h2>
<p class=sub>系統を自動判定して不具合候補を指摘します。判断は設計。指摘は「こう直したら？」の申し送り材料です。</p>
<div class=help>
 <div class=help-t>アップロードする図面（DXF）</div>
 <ul>
  <li><b>対応する盤</b>：制御盤・分電盤（どちらも可）</li>
  <li><b>単位</b>：1盤分、または列盤（3面体など）は1列盤分を<b>一式</b>で（複数選択・ZIP可）</li>
  <li><b>必須の図面</b>：シーケンス図（制御回路）／スケルトン図（主回路）／内部配置図（機器の物理配置）</li>
  <li><b>任意</b>：社内確認表（あれば添付。確認表の仕様チェックが追加で走ります）</li>
  <li><b>注意</b>：シーケンス・スケルトンと内部配置図は<b>同じ範囲</b>をそろえる（面ごと／列盤ごと）。ファイル種類の指定は不要＝自動で振り分けます</li>
 </ul>
</div>
<div class=card>
 <label class=drop id=drop for=fi>ここに DXF / ZIP をドロップ、またはクリックして選択
  <input type=file id=fi multiple accept=".dxf,.zip"></label>
 <div class=files id=files></div>
 <div class=actions>
  <button class=btn id=go disabled>検図する</button>
  <label class=chk><input type=checkbox id=ai> AI補助(R6/SPD警報)も実行</label>
  <span class=muted id=st></span>
  <a href="#" id=statslink class=muted style="margin-left:auto">▼ 集計</a>
 </div>
</div>
<div id=stats class=card hidden></div>
<div id=out></div>
<script>
const fi=document.getElementById('fi'),drop=document.getElementById('drop'),
 files=document.getElementById('files'),go=document.getElementById('go'),
 st=document.getElementById('st'),out=document.getElementById('out'),ai=document.getElementById('ai'),
 statsBox=document.getElementById('stats'),statslink=document.getElementById('statslink');
let picked=[];let runId=null;
function show(){files.textContent=picked.length?('選択: '+picked.map(f=>f.name).join(', ')):'';go.disabled=!picked.length;}
fi.onchange=e=>{picked=[...e.target.files];show();};
;['dragover','dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();
 drop.classList.toggle('hi',ev==='dragover');if(ev==='drop'){picked=[...e.dataTransfer.files];show();}}));
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const sevcls=s=>s==='high'?'hi9':(s==='low'?'low':'med');
go.onclick=async()=>{
 go.disabled=true;st.textContent='検図中…';out.innerHTML='';
 const fd=new FormData();picked.forEach(f=>fd.append('file',f));if(ai.checked)fd.append('ai','1');
 try{
  const r=await fetch('/api/kenzu',{method:'POST',body:fd});const d=await r.json();
  if(!r.ok){out.innerHTML='<div class=card><div class=err>'+esc(d.error||'エラー')+'</div></div>';st.textContent='';go.disabled=false;return;}
  runId=d.run_id||null;
  let h='<div class=card>';
  h+='<div class=sum>指摘 <b>'+d.count+'</b> 件　内訳: '+esc(JSON.stringify(d.summary))+'</div>';
  h+='<div class=muted>系統判定: '+d.classification.map(c=>'<span class=role>'+esc(c.role)+'</span>'+esc(c.file)).join(' ')+'</div>';
  if(d.warnings&&d.warnings.length)h+='<div class=err style="margin-top:8px">'+d.warnings.map(esc).join('<br>')+'</div>';
  h+='</div>';
  if(d.findings.length){
   h+='<div class=card><table><tr><th>ルール</th><th>重要度</th><th>場所</th><th>問題</th><th>提案</th><th>判定</th></tr>';
   for(const f of d.findings){
    const fid=esc(f.fid);
    h+='<tr><td><span class=rule>'+esc(f.rule)+'</span><br><span class=muted>'+esc(f.confidence||'')+'</span></td>'+
       '<td><span class="pill '+sevcls(f.severity)+'">'+esc(f.severity)+'</span></td>'+
       '<td>'+esc(f['場所'])+'</td><td>'+esc(f['問題'])+'</td><td>'+esc(f['提案'])+'</td>'+
       '<td class=fbcell data-fid="'+fid+'">'+
         '<button class=fbbtn data-d=fixed title="正しい指摘・設計を是正">是正</button> '+
         '<button class=fbbtn data-d=false_positive title="誤検知">誤検知</button>'+
       '</td></tr>';
   }
   h+='</table><div class=muted style="margin-top:8px">↑ 各指摘に設計の判定を記録できます（バージョンアップの集計に反映）。</div></div>';
  }else{h+='<div class=card>指摘はありませんでした。</div>';}
  // ハーネス From-To（検図の後段。H1-H5が無ければ「検図OK・生成」）
  if(d.harness){
   const H=d.harness,s=H.summary||{},csv='/api/harness.csv?run='+encodeURIComponent(runId||'');
   h+='<div class="card '+(H.ok?'hok':'hng')+'">';
   h+='<div class=help-t>ハーネス From-To '+(H.ok?'（検図OK → 生成）':'（未解消の指摘あり＝参考）')+'</div>';
   if(!H.ok)h+='<div class=err style="margin:2px 0 8px">ハーネス化の指摘が '+H.h_issues+' 件あります。解消後の生成を推奨します（下は参考の下書き）。</div>';
   h+='<div class=muted>号線 '+(s.nets||0)+' ／ 機器to機器つながり '+(s.conn||0)+' ／ 測長可 '+(s['測長可(物理位置あり)']||0)+'（'+(s.conn_pct||0)+'%）</div>';
   h+='<div style="margin:8px 0"><a class=btn2 href="'+csv+'">CSVをダウンロード</a></div>';
   if(H.rows&&H.rows.length){
    h+='<table><tr><th>号線</th><th>From</th><th>To</th><th>サイズ</th><th>長さmm</th></tr>';
    for(const r of H.rows.slice(0,300))h+='<tr><td>'+esc(r['号線'])+'</td><td>'+esc(r.from)+'</td><td>'+esc(r.to)+'</td><td>'+esc(r.size||'')+'</td><td>'+esc(r.len)+'</td></tr>';
    h+='</table>';
   }
   if(H.groups&&H.groups.length){
    h+='<div class=muted style="margin-top:8px">位置不明で測長できない号線（連結のみ）:</div>';
    h+='<table><tr><th>号線</th><th>機器（連結）</th><th>サイズ</th></tr>';
    for(const g of H.groups.slice(0,150))h+='<tr><td>'+esc(g['号線'])+'</td><td>'+esc((g.devs||[]).join(' ｜ '))+'</td><td>'+esc(g.size||'')+'</td></tr>';
    h+='</table>';
   }
   if((!H.rows||!H.rows.length)&&(!H.groups||!H.groups.length))h+='<div class=muted>生成できるFrom-Toがありませんでした（号線・属性が不足）。</div>';
   h+='</div>';
  }
  // 見逃し(ツールが出せなかった不具合)の登録
  h+='<div class=card><b>見逃しの登録</b>（ツールが検出できなかった不具合を記録）<br>'+
     '<input id=misrule placeholder="ルール/種別(例 R6, 新規)" style="width:180px;padding:6px;margin:8px 6px 0 0">'+
     '<input id=misnote placeholder="内容メモ" style="width:360px;padding:6px">'+
     ' <button class=btn id=misbtn style="padding:8px 14px">見逃しを登録</button>'+
     ' <span class=muted id=misst></span></div>';
  out.innerHTML=h;st.textContent='';
 }catch(e){out.innerHTML='<div class=card><div class=err>'+esc(e)+'</div></div>';st.textContent='';}
 go.disabled=false;
};
// 指摘への判定(是正/誤検知)
out.addEventListener('click',async e=>{
 const b=e.target.closest('.fbbtn');if(!b)return;
 const cell=b.closest('.fbcell');const fid=cell.getAttribute('data-fid');const disp=b.getAttribute('data-d');
 try{
  const r=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({run_id:runId,fid:fid,disposition:disp})});
  const d=await r.json();
  cell.innerHTML=r.ok?('<span class=muted>記録: '+(disp==='fixed'?'是正':'誤検知')+' ✓</span>')
                     :('<span class=err>'+esc(d.error||'失敗')+'</span>');
 }catch(err){cell.innerHTML='<span class=err>'+esc(err)+'</span>';}
});
// 見逃しの登録
out.addEventListener('click',async e=>{
 if(e.target.id!=='misbtn')return;
 const rule=document.getElementById('misrule').value.trim();
 const note=document.getElementById('misnote').value.trim();
 const misst=document.getElementById('misst');
 if(!rule&&!note){misst.textContent='ルールか内容を入力してください';return;}
 try{
  const r=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({run_id:runId,disposition:'missed',rule:rule,note:note})});
  const d=await r.json();
  misst.textContent=r.ok?'登録しました ✓':(d.error||'失敗');
  if(r.ok){document.getElementById('misrule').value='';document.getElementById('misnote').value='';}
 }catch(err){misst.textContent=String(err);}
});
// 集計(バージョンアップ用)
statslink.onclick=async e=>{
 e.preventDefault();
 if(!statsBox.hidden){statsBox.hidden=true;return;}
 try{
  const r=await fetch('/api/stats');const s=await r.json();
  let h='<b>集計</b>（保存先: <span class=muted>'+esc(s.data_dir)+'</span>）<br>'+
    '実行 '+s.runs+' 回 / 総指摘 '+s.findings_total+' 件 / フィードバック '+s.feedback_total+' 件'+
    ' / 見逃し '+s.missed_total+' 件<br>';
  h+='<table style="margin-top:8px"><tr><th>ルール</th><th>是正</th><th>誤検知</th><th>見逃し</th><th>適合率</th></tr>';
  const rules=Object.keys(s.by_rule).sort();
  if(rules.length===0)h+='<tr><td colspan=5 class=muted>まだフィードバックがありません</td></tr>';
  for(const k of rules){const c=s.by_rule[k];
   h+='<tr><td>'+esc(k)+'</td><td>'+c.fixed+'</td><td>'+c.false_positive+'</td><td>'+c.missed+
      '</td><td>'+(c.precision==null?'—':c.precision)+'</td></tr>';}
  h+='</table>';
  statsBox.innerHTML=h;statsBox.hidden=false;
 }catch(err){statsBox.innerHTML='<span class=err>'+esc(err)+'</span>';statsBox.hidden=false;}
};
</script></div></body></html>"""


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8001'))
    _aikey = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"))
    print(f'工業電気検図システム(現場用)起動: http://localhost:{port}'
          f'  (AIキー {"OK" if _aikey else "未設定→R6スキップ"})')
    app.run(host='0.0.0.0', port=port, debug=False)
