# -*- coding: utf-8 -*-
"""図面検図システム（現場用・単独アプリ）

工場現場で使う図面不具合チェック。営業が使う積算コード選定システム(app.py)とは
別プロセス・別アドレス・別ログインで動かす（部署が違うため入口ごと分ける）。
検図ロジックは wireharness.fromto_qc をそのまま利用する。

起動:
  開発        python kenzu_app.py            # PORT 省略時 8001
  本番(EC2)   gunicorn kenzu_app:app --workers 3 --timeout 300 --bind 127.0.0.1:8001

環境変数:
  KENZU_PASSWORD   画面ログイン用パスワード（未設定なら認証オフ＝ローカル試用のみ）
  KENZU_SECRET     セッション署名鍵（未設定なら起動毎にランダム）
  ANTHROPIC_API_KEY / GEMINI_API_KEY  R6(SPD警報)のAI補助を使う時だけ。無ければR6は自動スキップ。
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
<meta name=viewport content="width=device-width,initial-scale=1"><title>図面検図システム ログイン</title>
<style>body{font-family:'Yu Gothic',Meiryo,sans-serif;background:#f7f5ef;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.box{background:#fff;border:1px solid #d6d1c4;border-radius:10px;padding:32px;width:320px}
h1{font-size:16px;color:#1e3a28;margin:0 0 18px}input{width:100%;padding:10px;border:1px solid #d6d1c4;
border-radius:6px;font-size:14px;box-sizing:border-box}button{width:100%;margin-top:12px;padding:11px;
background:#1e3a28;color:#fff;border:0;border-radius:6px;font-size:14px;font-weight:700;cursor:pointer}
.e{color:#c0504d;font-size:12px;margin-top:10px}</style></head>
<body><form class=box method=post action=/login>
<h1>図面検図システム</h1>
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


def _kenzu_run(seq, skel, layout, table, ai=False):
    """系統別モデルに対し R1-R7＋H1-H5 を実行。ルール構成は run_check と一致。"""
    from wireharness.fromto_qc import defect_check as dc
    from wireharness.fromto_qc import harness_qc
    physical = list(skel) + ([layout] if layout else [])
    findings = []
    if seq and layout:
        findings += dc.rule_R1_layout_missing(seq + skel, layout)
    for m in skel:
        findings += dc.rule_R2_capacity(m)
    for m in skel:
        findings += dc.rule_R3_neutral(m)
    if seq and physical:
        findings += dc.rule_R4_orphan_contact(seq, physical)
    for m in seq + skel + ([layout] if layout else []):
        findings += dc.rule_R7_earth(m)
    if table:
        findings += dc.rule_R5_confirmation_table(table)
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
        layout = buckets['layout']
        table = buckets['table']
        findings = _kenzu_run(seq, skel, layout, table, ai)
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
        # 検図専用ストアに実行を保存し、各指摘に安定ID(fid)を付けて返す
        # （設計のフィードバック紐付け用）。保存失敗でも検図結果は返す。
        run_id = None
        try:
            meta = {'files': [c['file'] for c in classification],
                    'classification': classification, 'ai': ai, 'summary': counts}
            run_id, uniq = kenzu_store.save_run(meta, uniq)
        except Exception:
            import traceback
            traceback.print_exc()
        return jsonify(ok=True, run_id=run_id, count=len(uniq), summary=counts,
                       classification=classification, findings=uniq,
                       ai=ai, warnings=warnings)
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


@app.route('/')
@login_required
def index():
    return Response(KENZU_HTML, mimetype='text/html')


KENZU_HTML = """<!DOCTYPE html><html lang=ja><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>図面検図システム</title>
<style>
body{font-family:'Yu Gothic',Meiryo,sans-serif;background:#f7f5ef;margin:0;color:#26332b}
header{background:#1e3a28;color:#f4f1ea;padding:14px 22px;display:flex;align-items:baseline;gap:12px}
header h1{font-size:17px;margin:0;font-weight:700;letter-spacing:.04em}
header .ver{font-size:11px;opacity:.7}
.wrap{max-width:960px;margin:0 auto;padding:20px 16px 60px}
h2{font-size:16px;color:#1e3a28;margin:0 0 4px}.sub{color:#6b7a70;font-size:13px;margin:0 0 18px}
.card{background:#fff;border:1px solid #d9d3c5;border-radius:10px;padding:18px;margin-bottom:16px}
.drop{border:2px dashed #b9c2b8;border-radius:10px;padding:26px;text-align:center;color:#5c6b60;
 background:#fbfaf6;cursor:pointer}.drop.hi{background:#eef4ea;border-color:#1e3a28}
input[type=file]{display:none}
.btn{background:#1e3a28;color:#fff;border:0;border-radius:7px;padding:11px 20px;font-size:14px;
 font-weight:700;cursor:pointer}.btn:disabled{opacity:.5;cursor:default}
label.chk{font-size:13px;color:#4a584f;margin-left:14px;user-select:none}
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
</style></head><body>
<header><h1>図面検図システム</h1><span class="ver">現場用</span></header>
<div class=wrap>
<h2>図面検図（設計次工程チェック）</h2>
<p class=sub>DXF（複数可・ZIP可）をアップロードすると、系統を自動判定して不具合候補を指摘します。
判断は設計。指摘は「こう直したら？」の申し送り材料です。</p>
<div class=card>
 <label class=drop id=drop for=fi>ここに DXF / ZIP をドロップ、またはクリックして選択
  <input type=file id=fi multiple accept=".dxf,.zip"></label>
 <div class=files id=files></div>
 <button class=btn id=go disabled>検図する</button>
 <label class=chk><input type=checkbox id=ai> AI補助(R6/SPD警報)も実行</label>
 <span class=muted id=st></span>
 <span style="float:right"><a href="#" id=statslink class=muted>▼ 集計(バージョンアップ用)</a></span>
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
    print(f'図面検図システム(現場用)起動: http://localhost:{port}'
          f'  (AIキー {"OK" if _aikey else "未設定→R6スキップ"})')
    app.run(host='0.0.0.0', port=port, debug=False)
