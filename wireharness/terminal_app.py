# -*- coding: utf-8 -*-
"""端子番号割付(2人目)Webアプリ。設計DXF → ロケーター生成・保存 → 端子番号割付 → 保存 → PDF印刷。

工程(茂泉様の指示):
  1. 設計からのDXFを選択・アップロード
  2. ロケーターファイルを生成し保存
  3. ロケーターを元に端子番号を割付(ブラウザ上の確認・編集UI)
  4. 保存(サーバ側: ①最終端子番号リスト ②学習用の当初値＋差分 → 蓄積・一括学習に使用)
  5. 印刷: 端子番号入りシーケンス図のPDF出力

起動: python wireharness/terminal_app.py  (本番: gunicorn -b 0.0.0.0:8100 wireharness.terminal_app:app)
環境変数:
  TERMINAL_WORK     作業/保存領域(未指定=wireharness/terminal_work)。EC2では永続ディスクを指定。
  HARNESS_STATE     学習コーパスの永続領域(terminal_feedback と共有)。
  TERMINAL_PASSWORD 設定するとログイン必須(未設定=認証オフ=ローカル試用)。
  TERMINAL_USER     任意。設定するとID照合も行う。
  TERMINAL_SECRET   セッション鍵(未設定は起動毎にランダム)。
  PORT              既定 8100。
"""
import os
import re
import sys
import csv
import glob
import html
import json
import hmac
import secrets
import logging
import traceback
from logging.handlers import RotatingFileHandler
from flask import Flask, request, redirect, send_file, Response, session, jsonify

# 直接起動(python wireharness/terminal_app.py)でも wireharness パッケージを解決できるよう、
# リポジトリ直下(このファイルの2つ上)を import パスに追加する(gunicorn 起動時は無害)。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wireharness.fromto_qc import stage_interactive as SI
from wireharness.fromto_qc import stage_views as SV
from wireharness.fromto_qc import locator_apply as LAP
from wireharness.fromto_qc import locator_assign as LA
from wireharness.fromto_qc import locator_number as LN
from wireharness.fromto_qc import terminal_feedback as TFB
from wireharness.fromto_qc import terminal_print as TP
from wireharness.fromto_qc import run_local as RL
from wireharness.fromto_qc import dwg_convert as DWG
from wireharness.fromto_qc import harness_produce as HP
from wireharness.fromto_qc import locator_map as LMAP
from wireharness.fromto_qc.geometry import norm as _norm

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('TERMINAL_WORK', os.path.join(HERE, 'terminal_work'))
os.makedirs(WORK, exist_ok=True)
PASSWORD = os.environ.get('TERMINAL_PASSWORD', '')
USER = os.environ.get('TERMINAL_USER', '')
# 3人目(ハーネス工程=From-To)を2人目アプリに組込(既定ON)。TERMINAL_FROMTO=0 で無効化。
ENABLE_FROMTO = os.environ.get('TERMINAL_FROMTO', '1') != '0'
# 1人目(太物工程)の表示(既定OFF・別途リリース)。TERMINAL_FUTOMONO=1 で有効化。
ENABLE_FUTOMONO = os.environ.get('TERMINAL_FUTOMONO', '') == '1'
# 後方互換: 旧 TERMINAL_STAGE13=1 なら両方ON。
if os.environ.get('TERMINAL_STAGE13', '') == '1':
    ENABLE_FROMTO = True
    ENABLE_FUTOMONO = True

app = Flask(__name__)
app.secret_key = os.environ.get('TERMINAL_SECRET', secrets.token_hex(16))
app.config['MAX_CONTENT_LENGTH'] = 300 * 1024 * 1024        # DXF一式(最大300MB)
# スマホで別アプリへ移動→戻ると初期画面(ログイン)に戻る問題対策:
# セッションクッキーを永続化(30日)。estimateアプリと同じ方針。Cloudflare経由HTTPS前提。
import datetime as _dt
app.config.update(
    PERMANENT_SESSION_LIFETIME=_dt.timedelta(days=30),
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
)

_h = RotatingFileHandler(os.path.join(WORK, 'terminal_app.log'),
                         maxBytes=1_000_000, backupCount=5, encoding='utf-8')
_h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
app.logger.addHandler(_h)
app.logger.setLevel(logging.INFO)

_SEIBAN_RE = re.compile(r'(\d-\d+-\d+)')


def _esc(s):
    return html.escape(str(s if s is not None else ''))


def _safe(seiban):
    return ''.join(c for c in str(seiban) if c.isalnum() or c in '-_') or 'seiban'


def _dir(seiban):
    d = os.path.join(WORK, _safe(seiban))
    return d, _safe(seiban)


def _classify_inputs(indir):
    """アップロード済みDXFを種類別に分類する: (seq, skel, layout, lct, dct)。
    1製番運用=このフォルダの全DXFがこの製番のものなので、製番の綴り(区切り文字の違い等)に
    依存せず、ファイル名末尾のシート記号で分類する(例 …-F001→シーケンス, …-D001-DCT→手本)。
    ・末尾の -F/E/D/G<数字> で判定(F=シーケンス, E=スケルトン, D/G=内部配置図)
    ・名前に LCT を含むものは相番号図(ダクト前の中間成果)として lct に入れる。
    ・名前に DCT を含む/「相番号」は機器相番号図(完成形=ダクト入り/手本)として dct に入れ、配置図から除外。
    図面の流れ: 配置図(D/G) →[相番号自動付与]→ LCT →[配線ダクト作図]→ DCT(完成形)。"""
    seq, skel, layout, lct, dct = [], [], [], [], []
    seen = set()
    for p in sorted(glob.glob(os.path.join(indir, '*.dxf')) + glob.glob(os.path.join(indir, '*.DXF'))):
        base = os.path.basename(p)
        key = base.lower()
        if key in seen:
            continue
        seen.add(key)
        up = base.upper()
        # 相番号図(ダクト前の中間 LCT)。完成形 DCT より先に判定。
        if 'LCT' in up:
            lct.append(p)
            continue
        # 機器相番号図(完成形=ダクト入り/手本)。記号DCT か 日本語「相番号」。
        if 'DCT' in up or '相番号' in base:
            dct.append(p)
            continue
        # 1) シート記号で分類: …-F/E/D/G<数字>(例 …-F001, …-D001)
        stem = up.rsplit('.', 1)[0]           # 拡張子除去
        token = stem.rsplit('-', 1)[-1]        # 末尾のシート記号
        lt = token[:1] if token else ''
        if lt == 'F' and token[1:2].isdigit():
            seq.append(p); continue
        if lt == 'E' and token[1:2].isdigit():
            skel.append(p); continue
        if lt in ('D', 'G') and token[1:2].isdigit():
            layout.append(p); continue
        # 2) 日本語の種類名で分類(例 …_シーケンス.dxf, …_外形図と内部配置図.dxf)
        if 'シーケンス' in base or 'ｼｰｹﾝｽ' in base:
            seq.append(p)
        elif 'スケルトン' in base or 'ｽｹﾙﾄﾝ' in base:
            skel.append(p)
        elif '配置' in base or '外形' in base:
            layout.append(p)
        # 上記いずれにも該当しない(H 等)はこの工程では未使用
    return sorted(seq), sorted(skel), sorted(layout), sorted(lct), sorted(dct)


# ---------- 認証 ----------
_LOGIN = '''<!doctype html><meta charset=utf-8><title>端子番号割付システム</title>
<style>body{font-family:sans-serif;background:#eef2f8;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}
.box{background:#fff;border:1px solid #ccd3de;border-radius:10px;padding:30px;width:320px}
h1{font-size:16px;color:#16233c;margin:0 0 18px}input{width:100%;padding:10px;margin-top:8px;border:1px solid #ccd3de;border-radius:7px;box-sizing:border-box}
button{width:100%;margin-top:14px;padding:11px;background:#1f4fb0;color:#fff;border:0;border-radius:8px;font-weight:700;cursor:pointer}.e{color:#c0392b;font-size:12px;margin-top:10px}</style>
<form class=box method=post action="/login"><h1>端子番号割付システム</h1>{USER}
<input type=password name=pw placeholder="パスワード" autofocus><button>ログイン</button>{ERR}</form>'''


def _login_page(err=''):
    u = '<input type=text name=user placeholder="ID" autofocus>' if USER else ''
    return Response(_LOGIN.replace('{USER}', u).replace(
        '{ERR}', f'<div class=e>{_esc(err)}</div>' if err else ''), mimetype='text/html')


@app.before_request
def _require_login():
    if not PASSWORD:
        return None
    p = request.path
    if p == '/login' or p == '/api/health' or p.startswith('/static'):
        return None
    if session.get('auth'):
        return None
    if p.endswith('/save') or p.startswith('/api/'):
        return Response('{"ok":false,"error":"未認証"}', status=401, mimetype='application/json')
    return redirect('/login')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not PASSWORD:
        return redirect('/')
    if request.method == 'POST':
        ok = hmac.compare_digest(request.form.get('pw', ''), PASSWORD) and (
            not USER or hmac.compare_digest(request.form.get('user', ''), USER))
        if ok:
            session.permanent = True        # 永続クッキー化(30日)=別アプリから戻っても再ログイン不要
            session['auth'] = True
            session['user'] = request.form.get('user', '') or 'user'
            return redirect('/')
        return _login_page('IDまたはパスワードが違います')
    return _login_page()


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/api/health')
def health():
    return jsonify(ok=True)


# ---------- 画面の共通枠 ----------
_CSS = '''body{font-family:IPAGothic,"Yu Gothic",sans-serif;margin:0;background:#f1f4f9;color:#1a2233}
.hd{background:#16233c;color:#fff;padding:12px 18px}.hd a{color:#cfe0ff;text-decoration:none;margin-left:14px;font-size:13px}
.hd h1{font-size:17px;margin:0;display:inline-block}
.wrap{max-width:900px;margin:18px auto;padding:0 14px}
.card{background:#fff;border:1px solid #d7deea;border-radius:10px;padding:18px 20px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 10px}
a.btn,button.btn{display:inline-block;background:#1f4fb0;color:#fff;border:0;border-radius:8px;padding:9px 16px;text-decoration:none;font-size:14px;cursor:pointer;font-weight:700}
a.btn.g{background:#5a6472}a.btn.o{background:#0a7d4b}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid #eef;padding:7px 8px;text-align:left}
.muted{color:#667;font-size:12.5px}.step{display:flex;gap:10px;margin:10px 0}.step .n{background:#1f4fb0;color:#fff;border-radius:50%;width:22px;height:22px;display:inline-flex;align-items:center;justify-content:center;font-size:12px;flex:0 0 22px}
.drop{border:2px dashed #9bb0d0;border-radius:10px;padding:18px;text-align:center;background:#f7faff}
.err{color:#b00020;white-space:pre-wrap;font-size:12.5px}.ok{color:#0a7d4b}'''


def _page(body, title='端子番号割付システム'):
    nav = ('<a href="/">ホーム</a>' + ('<a href="/logout">ログアウト</a>' if PASSWORD else ''))
    return Response(
        f'<!doctype html><html lang=ja><head><meta charset=utf-8>'
        f'<meta name=viewport content="width=device-width,initial-scale=1">'
        f'<title>{_esc(title)}</title><style>{_CSS}</style></head><body>'
        f'<div class=hd><h1>端子番号割付（2人目）</h1>{nav}</div>'
        f'<div class=wrap>{body}</div></body></html>', mimetype='text/html')


def _meta_path(seiban):
    d, _ = _dir(seiban)
    return os.path.join(d, 'meta.json')


def _load_meta(seiban):
    try:
        with open(_meta_path(seiban), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_meta(seiban, meta):
    d, _ = _dir(seiban)
    os.makedirs(d, exist_ok=True)
    with open(_meta_path(seiban), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


def _list_projects():
    rows = []
    for p in sorted(glob.glob(os.path.join(WORK, '*', 'meta.json'))):
        try:
            with open(p, encoding='utf-8') as f:
                m = json.load(f)
            rows.append(m)
        except Exception:
            continue
    rows.sort(key=lambda m: m.get('updated', ''), reverse=True)
    return rows


# ---------- 1. ホーム＝1製番のアップロード（一覧は持たない：一回に1製番を扱う） ----------
def _upload_body():
    return ('<div class=card><h2>① 図面をアップロード（1製番）</h2>'
            '<form method=post action="/create" enctype="multipart/form-data">'
            # 1) 先に図面を選ぶ
            '<div class=drop>設計図一式を選択してください<br>'
            '<b>必要な図面</b>：シーケンス（F＊）・スケルトン（E＊）・内部配置図（D＊）<br>'
            '<span class=muted>＋相番号図（＊-LCT）／機器相番号図（＊-DCT）が有ればロケーターに使用。'
            'LCTもDCTも無ければ配置図から相番号を自動付与します。'
            'DXF・DWG どちらでも可（DWGはサーバ内でDXFへ自動変換）</span><br>'
            '<input id=fileinput type=file name=files multiple accept=".dxf,.DXF,.dwg,.DWG" '
            'style="margin-top:10px"></div>'
            # 2) 選んだファイル名を表示(これを見て製番を入力)
            '<div id=filelist class=muted style="margin:10px 0"></div>'
            # 3) ファイル名を見ながら製番を入力
            '<p>製番（整番） <input id=seiban type=text name=seiban placeholder="例 4-21088_029" required '
            'style="padding:8px;border:1px solid #ccd;border-radius:6px">'
            ' <button type=button id=guessbtn class="btn g" style="display:none">ファイル名から候補</button></p>'
            '<p style="margin-top:12px"><button class="btn o" type=submit>アップロード ▶</button> '
            '<span class=muted>この後：②ロケーター生成→③端子番号割付→④保存</span></p>'
            '</form></div>'
            '<div class=card muted>一回に1製番を扱います。アップロード→②→③→④と進み、'
            '保存すると結果と修正差分がサーバに蓄積され学習に使われます。</div>'
            '<script>(function(){'
            'var fi=document.getElementById("fileinput"),fl=document.getElementById("filelist"),'
            'sb=document.getElementById("seiban"),gb=document.getElementById("guessbtn");'
            'function esc(s){return s.replace(/[<>&]/g,function(c){return {"<":"&lt;",">":"&gt;","&":"&amp;"}[c];});}'
            # ファイル名の共通接頭辞から製番候補を作る(末尾の -F001 等シート部を除く)
            'function guess(names){if(!names.length)return "";var p=names[0];'
            'for(var i=1;i<names.length;i++){var q=names[i];var j=0;while(j<p.length&&j<q.length&&p[j]===q[j])j++;p=p.slice(0,j);}'
            'p=p.replace(/\\.[^.]*$/,"");p=p.replace(/[-_\\s]*(F|E|D|G|H)?\\d*$/i,"");p=p.replace(/[-_\\s]+$/,"");return p;}'
            'fi.addEventListener("change",function(){'
            'var names=Array.from(fi.files).map(function(f){return f.name;}).sort();'
            'if(!names.length){fl.innerHTML="";gb.style.display="none";return;}'
            'fl.innerHTML="<b>選択されたファイル（"+names.length+"件）</b><ul style=\\"margin:6px 0 0;padding-left:18px\\">"'
            '+names.map(function(n){return "<li>"+esc(n)+"</li>";}).join("")+"</ul>";'
            'var g=guess(names);if(g){gb.textContent="候補: "+g;gb.style.display="";gb.onclick=function(){sb.value=g;sb.focus();};}'
            'else{gb.style.display="none";}'
            '});'
            '})();</script>')


@app.route('/')
def index():
    return _page(_upload_body(), '端子番号割付')


# /new は従来のリンク互換のためホームへ
@app.route('/new')
def new():
    return redirect('/')


@app.route('/create', methods=['POST'])
def create():
    seiban = (request.form.get('seiban') or '').strip()
    files = request.files.getlist('files')
    if not seiban or not files:
        return _page('<div class=card err>製番と設計DXFを指定してください。</div>'
                     '<div class=card><a class="btn g" href="/new">← 戻る</a></div>')
    d, safe = _dir(seiban)
    indir = os.path.join(d, 'in')
    outdir = os.path.join(d, 'out')
    os.makedirs(indir, exist_ok=True)
    os.makedirs(outdir, exist_ok=True)
    saved = []
    for f in files:
        if not (f.filename or '').lower().endswith(('.dxf', '.dwg')):
            continue
        p = os.path.join(indir, os.path.basename(f.filename))
        f.save(p)
        saved.append(p)
    if not saved:
        return _page('<div class=card err>DXF/DWGファイルがありません。</div>'
                     '<div class=card><a class="btn g" href="/new">← 戻る</a></div>')
    # DWG(AutoCAD独自)はサーバ内でDXFへ変換してから解析(外部送信なし)。
    # sanitize=True: 変換後に ezdxf.recover 正規化し、解析側が素の readfile で読めるようにする。
    conv = DWG.convert_dir(indir, sanitize=True)
    failed = [(src, err) for (src, dxf, err) in conv if dxf is None]
    if failed:
        for src, err in failed:
            app.logger.warning('dwg convert failed %s: %s', os.path.basename(src), err)
        rows = ''.join(f'<li>{os.path.basename(n)}: {e}</li>' for n, e in failed)
        return _page('<div class=card err>一部のDWGをDXFへ変換できませんでした。'
                     'その図面はDXFで保存して再アップロードしてください。'
                     f'<ul>{rows}</ul></div>'
                     '<div class=card><a class="btn g" href="/new">← 戻る</a></div>')
    # 製番でDXFを分類(制御F/スケルトンE/配置D,G/相番号LCT/機器相番号DCT)
    seq, skel, layout, lct, dct = _classify_inputs(indir)
    app.logger.info('create seiban=%s files=%d seq=%d skel=%d layout=%d lct=%d dct=%d',
                    safe, len(saved), len(seq), len(skel), len(layout), len(lct), len(dct))
    meta = {'seiban': safe, 'updated': _now(), 'counts': {
        'seq': len(seq), 'skel': len(skel), 'layout': len(layout),
        'lct': len(lct), 'dct': len(dct)}}
    # 2. ロケーターはこの時点では作らない。製番ページの「ロケーター図面を生成」ボタンで生成し、
    #    「保存」ボタンで確定する(段階を踏むワークフロー)。
    meta['locator'] = {}
    meta['locator_saved'] = False
    # 3. 端子番号割付UI(サーバ保存)。ボタンは4種(シーケンス/スケルトン/内部配置図/ロケーター)が
    #    揃うまで製番ページ側で無効化する。
    try:
        assign_html = os.path.join(outdir, f'{safe}_端子番号割付.html')
        n_term = SI.stage2_terminal_assign(seq, skel, safe, assign_html,
                                           save_url=f'/p/{safe}/save')
        meta['assign_html'] = os.path.basename(assign_html)
        meta['total'] = n_term
        meta['assigned'] = False
    except Exception:
        app.logger.exception('assign failed %s', safe)
        meta['assign_error'] = traceback.format_exc()
    if ENABLE_FUTOMONO:
        # 1人目 太物工程(太物配線)
        try:
            wire_html = os.path.join(outdir, f'{safe}_太物工程.html')
            meta['wires'] = SI.stage1_wires(skel, safe, wire_html, save_url=f'/p/{safe}/save-wire')
            meta['wire_html'] = os.path.basename(wire_html)
        except Exception:
            app.logger.exception('wire failed %s', safe)
            meta['wire_error'] = traceback.format_exc()
    if ENABLE_FROMTO:
        # 3人目 ハーネス工程(From-To)。2人目の確定端子(自動)で補正(保存後は手修正値で再生成)。
        try:
            _gen_fromto(safe, seq, skel, _term_override_auto(seq, skel), outdir, meta)
        except Exception:
            app.logger.exception('fromto failed %s', safe)
            meta['ft_error'] = traceback.format_exc()
    _save_meta(safe, meta)
    return redirect(f'/p/{safe}')


def _term_override_auto(seq, skel):
    try:
        return SI.assigned_terminals(seq, skel)
    except Exception:
        return None


def _gen_fromto(safe, seq, skel, term_ov, outdir, meta):
    """From-To(ハーネス工程)を完全版パイプライン(route_and_length＋相番号置換)で生成し、
    手本と同じネイティブ形式txt(相番号)も出力。確認UIも同じ行から描く。
    term_ov=2人目の確定端子(簡易版フォールバック時のみ使用)。
    相番号の対応表元が無い/失敗時は簡易版(stage3内部計算・記号名)にフォールバック(回帰ゼロ)。"""
    d, _ = _dir(safe)
    _s, _k, _layout, lct, dct = _classify_inputs(os.path.join(d, 'in'))
    loc_src = dct or lct          # 相番号の対応表元(手本DCT優先、無ければLCT)
    ft_html = os.path.join(outdir, f'{safe}_ハーネス工程.html')
    rows = None
    try:
        routed = HP.route_and_length(seq, skel, seiban=safe, dct_paths=loc_src or None)
        # 記号→相番号(A,B,C…)置換。対応表が無ければ不変(回帰ゼロ)。
        try:
            lm = LMAP.build_map(loc_src) if loc_src else None
            if lm and lm.get('by_actual'):
                n = LMAP.apply_to_routed(routed, lm)
                meta['ft_locator'] = n
        except Exception:
            app.logger.exception('locator map failed %s', safe)
        # From-To行(相番号) [号線,種別,From機,From番,From端,To機,To番,To端]
        rows = []
        for w in routed.get('wires', []):
            f, t = w.get('from', {}), w.get('to', {})
            rows.append([w.get('gousen', ''), w.get('kind', ''),
                         f.get('device', ''), f.get('no', ''), f.get('terminal', ''),
                         t.get('device', ''), t.get('no', ''), t.get('terminal', '')])
        # 手本と同じネイティブ形式(11列TAB)の ハーネスデータ txt
        try:
            native = os.path.join(outdir, f'{safe}_ハーネスデータ.txt')
            HP.to_harness_txt(routed, native, seiban=safe)
            meta['ft_native'] = os.path.basename(native)
        except Exception:
            app.logger.exception('harness txt failed %s', safe)
    except Exception:
        app.logger.exception('route_and_length failed %s; 簡易版へフォールバック', safe)
        rows = None
    st = SV.stage3_fromto(seq, skel, safe, ft_html, term_override=term_ov,
                          save_url=f'/p/{safe}/save-fromto', rows=rows)
    meta['ft_html'] = os.path.basename(ft_html)
    meta['gousen'] = st.get('gousen', 0)
    # 画面の保存と同じ4列(号線,種別,From,To)・cp932で From-To CSV を出力。
    try:
        ft_rows = [['号線', '種別', 'From', 'To']]
        for r in st.get('rows', []):
            frm = f'{r[2]}{r[3]}' + (f' :{r[4]}' if len(r) > 4 and r[4] else '')
            to = f'{r[5]}{r[6]}' + (f' :{r[7]}' if len(r) > 7 and r[7] else '')
            ft_rows.append([r[0], r[1], frm, to])
        _write_csv(os.path.join(outdir, f'{safe}_ハーネス工程FromTo.csv'), ft_rows)
        meta['ft_wires'] = len(ft_rows) - 1
    except Exception:
        app.logger.exception('fromto csv failed %s', safe)
    return st


def _term_override_from_saved(safe):
    """保存済み2人目の最終端子番号リストから term_override を作る。
    {(norm(機器), round(x), round(y)): 端子番号}。From-To側は空欄のみ補完(非空欄は壊さない)。"""
    d, _ = _dir(safe)
    p = os.path.join(d, f'{safe}_端子番号リスト.csv')
    if not os.path.exists(p):
        return None
    rows = None
    for enc in ('cp932', 'utf-8-sig', 'utf-8'):
        try:
            with open(p, encoding=enc, newline='') as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    if not rows:
        return None
    head = rows[0]
    try:
        di, xi, yi, ni = head.index('機器'), head.index('X'), head.index('Y'), head.index('端子番号')
    except ValueError:
        return None
    ov = {}
    for r in rows[1:]:
        if len(r) <= max(di, xi, yi, ni):
            continue
        num = (r[ni] or '').strip()
        if not num or num == '?':
            continue
        try:
            ov[(_norm(r[di]), round(float(r[xi])), round(float(r[yi])))] = num
        except Exception:
            pass
    return ov or None


def _generate_locator(safe, meta):
    """ロケーター(機器相番号図)を用意し meta['locator'] を更新。戻り: meta['locator']。
    図面の流れ: 配置図(D/G) →[相番号自動付与]→ LCT →[配線ダクト作図]→ DCT(完成形)。
    優先: DCT(完成形)が有ればそのまま採用 → 次に LCT(相番号済み)をそのまま採用 →
          どちらも無ければ配置図に相番号を自動付与して LCT を生成。"""
    d, _ = _dir(safe)
    indir = os.path.join(d, 'in')
    outdir = os.path.join(d, 'out')
    os.makedirs(outdir, exist_ok=True)
    seq, skel, layout, lct, dct = _classify_inputs(indir)
    # 相番号の基(対応表CSV)は、完成形DCT→中間LCTの順に、有る相番号図から読む。
    basis_src = dct or lct
    basis = LA.basis_from_dct(basis_src) if basis_src else None
    loc_csv = os.path.join(outdir, f'{safe}_ロケーター対応表.csv')
    if layout:
        LAP.write_table_csv(layout, loc_csv, basis_of=basis, dct_paths=basis_src)
    n_loc = len(LAP.locator_rows(layout, basis_of=basis, dct_paths=basis_src)) if layout else 0
    csv_name = os.path.basename(loc_csv) if layout else ''
    if dct:
        # 完成形(ダクト入り/手本)の機器相番号図。そのままロケーターとして採用。
        meta['locator'] = {'dxf': os.path.basename(dct[0]), 'source': 'dct',
                           'csv': csv_name, 'count': n_loc, 'generated': True}
    elif lct:
        # 相番号済み(ダクト前)の中間図 LCT。そのまま採用(ダクト作図はこの後の工程)。
        meta['locator'] = {'dxf': os.path.basename(lct[0]), 'source': 'lct',
                           'csv': csv_name, 'count': n_loc, 'generated': True}
    elif layout:
        # LCTもDCTも無い → 配置図に相番号(リレー/タイマのDEVICE1=A,B,C…)を自動付与して
        # LCT(相番号図・ダクト前)を生成する。手本12製番で相番号が116/116一致を確認済み。
        # ファイル名末尾を LCT.dxf に(ダクト作図後に DCT.dxf へ)。
        loc_dxf = os.path.join(outdir, f'{safe}_ロケーター_LCT.dxf')
        try:
            letters, _used = LN.write_number_dct(layout, loc_dxf)
            meta['locator'] = {'dxf': os.path.basename(loc_dxf), 'source': 'gen-lct',
                               'csv': csv_name, 'count': n_loc,
                               'letters': letters, 'generated': True}
        except Exception:
            # 失敗時のみ従来の重ね描き(オーバーレイ)へフォールバック。
            app.logger.exception('number-lct generate failed %s', safe)
            LAP.write_dxf(layout, loc_dxf, basis_of=basis, dct_paths=basis_src)
            meta['locator'] = {'dxf': os.path.basename(loc_dxf), 'source': 'gen',
                               'csv': csv_name, 'count': n_loc, 'generated': True}
    else:
        meta['locator'] = {}
    return meta['locator']


@app.route('/p/<seiban>/locator/generate', methods=['POST'])
def locator_generate(seiban):
    safe = _safe(seiban)
    meta = _load_meta(safe)
    if not meta:
        return redirect(f'/p/{safe}')
    try:
        _generate_locator(safe, meta)
        meta.pop('locator_error', None)
    except Exception:
        app.logger.exception('locator generate failed %s', safe)
        meta['locator_error'] = traceback.format_exc()
    meta['locator_saved'] = False       # 生成し直したら未保存に戻す
    meta['updated'] = _now()
    _save_meta(safe, meta)
    return redirect(f'/p/{safe}')


@app.route('/p/<seiban>/locator/save', methods=['POST'])
def locator_save(seiban):
    safe = _safe(seiban)
    meta = _load_meta(safe)
    if not meta:
        return redirect(f'/p/{safe}')
    if meta.get('locator', {}).get('generated'):
        meta['locator_saved'] = True
        meta['updated'] = _now()
        _save_meta(safe, meta)
    return redirect(f'/p/{safe}')


def _now():
    import time
    return time.strftime('%Y-%m-%dT%H:%M:%S')


# ---------- 3. 製番ページ ----------
@app.route('/p/<seiban>')
def project(seiban):
    safe = _safe(seiban)
    meta = _load_meta(safe)
    if not meta:
        return _page('<div class=card err>製番が見つかりません。</div>'
                     '<div class=card><a class="btn g" href="/">← ホーム</a></div>')
    d, _ = _dir(safe)
    indir = os.path.join(d, 'in')
    try:
        seqs, skels, layouts, lcts, dcts = _classify_inputs(indir)
    except Exception:
        seqs = skels = layouts = lcts = dcts = []

    def _names(paths):
        return '、'.join(sorted(_esc(os.path.basename(p)) for p in paths)) if paths else '—'

    def _row(label, paths, need):
        n = len(paths)
        mark = ('<span class=ok>✓</span>' if n
                else ('<span class=err>未</span>' if need else '<span class=muted>—</span>'))
        return (f'<tr><td>{label}</td><td style="text-align:center">{mark} {n}</td>'
                f'<td class=muted style="font-size:12px">{_names(paths)}</td></tr>')

    # ① アップロード状況(ファイル名を種類別に表示)
    up_tbl = ('<table><tr><th>種類</th><th>数</th><th>ファイル名</th></tr>'
              + _row('シーケンス（F＊）', seqs, True)
              + _row('スケルトン（E＊）', skels, True)
              + _row('内部配置図（D＊）', layouts, True)
              + _row('相番号図（＊-LCT／ダクト前）', lcts, False)
              + _row('機器相番号図（＊-DCT／完成形・手本）', dcts, False)
              + '</table>')
    have_uploads = bool(seqs and skels and layouts)
    up_note = ''
    if not have_uploads:
        # 切り分け用に、実際にアップロードされたファイルをそのまま列挙する。
        raw = sorted(os.path.basename(p) for p in
                     glob.glob(os.path.join(indir, '*')) if os.path.isfile(p))
        raw_html = ('<div class=muted style="margin-top:6px">アップロードされたファイル：'
                    + (('<br>' + '、'.join(_esc(n) for n in raw)) if raw else 'なし')
                    + '</div>')
        up_note = ('<div class=err style="margin-top:6px">必要な図面（シーケンス・スケルトン・内部配置図）が'
                   '揃っていません。ファイル名が「…-F001」「…-E001」「…-D001」の形か確認し、'
                   '不足分を含めて登録し直してください。</div>' + raw_html)
    step1 = f'<div class=card><h2>① 図面アップロード</h2>{up_tbl}{up_note}</div>'

    # ② ロケーター図面(生成→保存)
    loc = meta.get('locator', {})
    loc_saved = meta.get('locator_saved')
    if loc.get('generated') and loc.get('dxf'):
        _src = loc.get('source')
        dxf_label = {'dct': '機器相番号図(DXF・完成形/手本)',
                     'lct': '相番号図(DXF・ダクト前)',
                     'gen-lct': '相番号図(DXF・自動生成/ダクト前)'}.get(_src, 'ロケーター付図面(DXF)')
        if _src == 'gen-lct':
            _lt = loc.get('letters')
            gen_note = ('<div class=ok style="margin:6px 0">設計の配置図から機器相番号(A,B,C…)を'
                        f'自動付与しました（{_lt if _lt is not None else "?"}機器）＝相番号図(LCT)。'
                        '配線ダクトを作図して完成形(DCT)にする工程はこの後です。</div>')
        elif _src == 'lct':
            gen_note = ('<div class=muted style="margin:6px 0">相番号済みの図(LCT)を使用'
                        '（配線ダクトはこの後の工程）。</div>')
        elif _src == 'dct':
            gen_note = '<div class=muted style="margin:6px 0">完成形の機器相番号図(DCT)を使用。</div>'
        else:
            gen_note = ''
        loc_links = (f'<a class="btn g" href="/p/{safe}/locator.pdf" target=_blank>ロケーター図(PDF)を表示</a> '
                     f'<button class="btn o" onclick="saveAs(\'/p/{safe}/locator.pdf\',\'{safe}_ロケーター図.pdf\')">ロケーター図(PDF)を保存</button> '
                     f'<button class="btn g" onclick="saveAs(\'/p/{safe}/file/{_esc(loc["dxf"])}\',\'{_esc(loc["dxf"])}\')">{dxf_label}を保存</button> '
                     + (f'<button class="btn g" onclick="saveAs(\'/p/{safe}/file/{_esc(loc.get("csv",""))}\',\'{_esc(loc.get("csv",""))}\')">対応表(CSV)を保存</button>'
                        if loc.get('csv') else ''))
        regen = (f'<form method=post action="/p/{safe}/locator/generate" style="display:inline">'
                 f'<button class="btn g" type=submit>再生成</button></form>')
        if loc_saved:
            step2 = (f'<div class=card><h2>② ロケーター図面</h2>{gen_note}{loc_links}'
                     f'<div class=ok style="margin:8px 0">保存済み ✓</div>{regen}</div>')
        else:
            save_btn = (f'<form method=post action="/p/{safe}/locator/save" style="display:inline">'
                        f'<button class="btn o" type=submit>保存</button></form> ')
            step2 = (f'<div class=card><h2>② ロケーター図面</h2>{gen_note}{loc_links}'
                     f'<div class=muted style="margin:8px 0">内容を確認し「保存」してください。</div>'
                     f'{save_btn}{regen}</div>')
    else:
        can_gen = bool(layouts or lcts or dcts)
        errnote = ('<div class=err style="margin-top:6px">前回の生成でエラーが出ました。</div>'
                   if meta.get('locator_error') else '')
        gen_btn = (f'<form method=post action="/p/{safe}/locator/generate" style="display:inline">'
                   f'<button class="btn o" type=submit>ロケーター図面を生成</button></form>'
                   if can_gen else
                   '<span class=err>内部配置図（D＊）・相番号図（LCT）・機器相番号図（DCT）のいずれも無いため生成できません。</span>')
        step2 = (f'<div class=card><h2>② ロケーター図面</h2>'
                 f'<div class=muted style="margin-bottom:6px">LCT/DCTが有ればそれを使用、無ければ配置図から'
                 f'相番号を自動付与して相番号図(LCT)を生成します。</div>'
                 f'{gen_btn}{errnote}</div>')

    # ③ 端子番号割付(4種=シーケンス/スケルトン/内部配置図/ロケーター が揃って有効化)
    ready = bool(have_uploads and loc_saved)
    saved = meta.get('saved')
    two_out = ''
    if saved:
        two_out = (
            f'<div class=ok style="margin:8px 0">確認 {meta.get("checked",0)}/{meta.get("total",0)}・'
            f'修正 {meta.get("diffs",0)} 件・保存 {_esc(meta.get("saved",""))[:16].replace("T"," ")}'
            '（学習用の差分はサーバに蓄積済み）</div>'
            '<div class=muted style="margin-bottom:6px">④ 成果物を保存先を選んで端末(PC/スマホ)へ保存'
            '（PCは「名前を付けて保存」、iPhoneは共有シート「ファイルに保存」）</div>'
            f'<button class="btn o" onclick="saveAs(\'/p/{safe}/print\',\'{safe}_端子番号記入図.pdf\')">端子番号入り図面(PDF)を保存</button> '
            f'<button class="btn g" onclick="saveAs(\'/p/{safe}/file/{safe}_端子番号リスト.csv\',\'{safe}_端子番号リスト.csv\')">最終リスト(CSV)を保存</button> '
            f'<button class="btn g" onclick="saveAs(\'/p/{safe}/numbered.dxf\',\'{safe}_端子番号入りCAD.dxf\')">端子番号入りCAD(DXF)を保存</button>')
    if not meta.get('assign_html'):
        assign_area = '<span class=err>割付UIの生成に失敗しました。</span>'
    elif ready:
        assign_area = f'<a class="btn o" href="/p/{safe}/assign">端子番号割付を開く ▶</a>'
    else:
        miss = ([] if have_uploads else ['図面アップロード']) + ([] if loc_saved else ['ロケーターの保存'])
        assign_area = ('<button class="btn" type=button disabled '
                       'style="opacity:.45;cursor:not-allowed">端子番号割付（未解放）</button>'
                       f'<div class=muted style="margin-top:6px">先に完了してください：{"・".join(miss)}</div>')
    step3 = (f'<div class=card><h2>③ 端子番号割付</h2>{assign_area}{two_out}'
             f'<div class=muted style="margin-top:6px">押すと割付を実行し、割付表と端子番号入り図面を表示します。'
             f'確認後 ④の「保存」で割付表と割付図面を保存します。</div></div>')

    # 1人目(太物工程・既定OFF) / 3人目(ハーネス工程=From-To・既定ON)
    stage13 = ''
    if ENABLE_FUTOMONO:
        wire_btn = (f'<a class="btn o" href="/p/{safe}/wire">太物配線を開く ▶</a> '
                    f'<a class="btn g" href="/p/{safe}/wire.pdf" target=_blank>太物図面(PDF)</a>'
                    if meta.get('wire_html') else '<span class=err>太物UIの生成に失敗しました。</span>')
        wire_out = (f' <a class="btn g" href="/p/{safe}/file/{safe}_太物工程ハーネス.csv">太物ハーネス(CSV)</a>'
                    if meta.get('wire_saved') else '')
        stage13 += (
            f'<div class=card><h2>1人目：太物工程（太物配線 ≥8sq）</h2>{wire_btn}{wire_out}'
            f'<div class=muted style="margin-top:6px">配線を確認・修正し「保存」。</div></div>')
    if ENABLE_FROMTO:
        ft_hint = ('' if saved else
                   '<div class=muted>※ 先に③を保存すると、確定端子がFrom-Toに反映されます'
                   '（未保存時は自動の仮番号で生成）。</div>')
        ft_state = ('<span class=ok>保存済み ✓</span>' if meta.get('ft_saved')
                    else (f'<span class=muted>自動生成済み（{meta.get("ft_wires","?")}本）・未保存</span>'
                          if meta.get('ft_html') else ''))
        ft_btn = (f'<a class="btn o" href="/p/{safe}/fromto">From-Toを開く ▶</a> '
                  f'<a class="btn g" href="/p/{safe}/fromto.pdf" target=_blank>From-To図面(PDF)</a>'
                  if meta.get('ft_html') else '<span class=err>From-To UIの生成に失敗しました。</span>')
        ft_out = (f' <a class="btn g" href="/p/{safe}/file/{safe}_ハーネス工程FromTo.csv">From-To(CSV)</a>'
                  if meta.get('ft_html') else '')
        ft_native = (f' <a class="btn g" href="/p/{safe}/file/{_esc(meta["ft_native"])}">ハーネスデータ(手本形式txt)</a>'
                     if meta.get('ft_native') else '')
        ft_locnote = (f'<div class=ok style="margin:6px 0">機器を相番号（A,B,C…）で表記'
                      f'（配置図の対応表から {meta.get("ft_locator")} 件置換）＝手本のハーネスデータ形式。</div>'
                      if meta.get('ft_locator') else
                      '<div class=muted style="margin:6px 0">※ 配置図の相番号対応表（DCT）が無いため記号名で出力。'
                      '手本の相番号表記にするには機器相番号図(DCT)が要ります。</div>')
        stage13 += (
            f'<div class=card><h2>④ ハーネス工程（From-To ≤5.5sq）</h2>{ft_hint}{ft_btn}{ft_out}{ft_native}'
            f'{ft_locnote}'
            f'<div style="margin:6px 0">{ft_state}</div>'
            f'<div class=muted style="margin-top:6px">From-To（ハーネスデータ）を確認・修正し「保存」。'
            f'③の確定端子を反映します。配線ルート確定後、ダクト作図(DCT)に進みます。</div></div>')

    # 生成ファイルは「保存先を選んで端末(PC/スマホ)へ保存」する共通関数。
    # PCは showSaveFilePicker(名前を付けて保存)、iPhone等は通常DL(共有シート「ファイルに保存」)。
    savejs = ('<script>async function saveAs(u,f){try{const r=await fetch(u);'
              'if(!r.ok){alert("取得に失敗: "+r.status);return;}const b=await r.blob();'
              'if(window.showSaveFilePicker){try{const h=await showSaveFilePicker({suggestedName:f});'
              'const w=await h.createWritable();await w.write(b);await w.close();return;}'
              'catch(e){if(e&&e.name==="AbortError")return;}}'
              'const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=f;'
              'document.body.appendChild(a);a.click();a.remove();'
              'setTimeout(()=>URL.revokeObjectURL(a.href),1500);}catch(e){alert("保存に失敗: "+e);}}</script>')
    body = (
        f'<div class=card><h2>製番 {_esc(safe)}</h2>'
        f'<p class=muted>流れ：①アップロード → ②ロケーター生成・保存 → ③端子番号割付 → ④保存'
        f'<br>生成ファイルは端末(PC/スマホ)へ保存、学習用の差分はサーバに蓄積します。</p></div>'
        f'{step1}{step2}{step3}{stage13}'
        f'<div class=card><a class="btn g" href="/">← ホーム</a></div>{savejs}')
    return _page(body, f'製番 {safe}')


@app.route('/p/<seiban>/assign')
def assign(seiban):
    safe = _safe(seiban)
    meta = _load_meta(safe)
    html_ = meta.get('assign_html')
    d, _ = _dir(safe)
    p = os.path.join(d, 'out', html_ or '')
    if not html_ or not os.path.exists(p):
        return _page('<div class=card err>割付UIがありません。</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    return send_file(p, mimetype='text/html')


def _serve_out(safe, fname, label):
    d, _ = _dir(safe)
    p = os.path.join(d, 'out', fname or '')
    if not fname or not os.path.exists(p):
        return _page(f'<div class=card err>{label}UIがありません。</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    return send_file(p, mimetype='text/html')


@app.route('/p/<seiban>/wire')
def wire(seiban):
    safe = _safe(seiban)
    return _serve_out(safe, _load_meta(safe).get('wire_html'), '太物')


@app.route('/p/<seiban>/fromto')
def fromto(seiban):
    safe = _safe(seiban)
    return _serve_out(safe, _load_meta(safe).get('ft_html'), 'From-To')


@app.route('/p/<seiban>/save-wire', methods=['POST'])
def save_wire(seiban):
    return _save_stage(seiban, f'{_safe(seiban)}_太物工程ハーネス.csv', 'wire_saved', '太物配線')


@app.route('/p/<seiban>/save-fromto', methods=['POST'])
def save_fromto(seiban):
    return _save_stage(seiban, f'{_safe(seiban)}_ハーネス工程FromTo.csv', 'ft_saved', 'From-To')


def _save_stage(seiban, fname, flag, label):
    safe = _safe(seiban)
    d, _ = _dir(safe)
    if not os.path.isdir(d):
        return jsonify(ok=False, error='製番が見つかりません')
    rows = (request.get_json(force=True, silent=True) or {}).get('rows') or []
    try:
        _write_csv(os.path.join(d, fname), rows)
    except Exception:
        app.logger.exception('save %s failed %s', label, safe)
        return jsonify(ok=False, error=traceback.format_exc()[:400])
    meta = _load_meta(safe)
    meta[flag] = _now()
    meta['updated'] = _now()
    _save_meta(safe, meta)
    app.logger.info('save %s seiban=%s rows=%d', label, safe, max(len(rows) - 1, 0))
    return jsonify(ok=True, message=f'{label} {max(len(rows)-1,0)} 行を保存しました。')


@app.route('/p/<seiban>/wire.pdf')
def wire_pdf(seiban):
    safe = _safe(seiban)
    d, _ = _dir(safe)
    seq, skel, _l, _lct, _dct = _classify_inputs(os.path.join(d, 'in'))
    if not skel:
        return _page('<div class=card err>スケルトン(E＊)が無いため太物図面を出力できません。</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    pdf = os.path.join(d, f'{safe}_太物図.pdf')
    try:
        TP.render_dxf_pdf(skel, pdf, title_prefix=f'{safe}　太物工程（スケルトン）　')
    except Exception:
        app.logger.exception('wire pdf failed %s', safe)
        return _page(f'<div class=card err>PDF生成でエラー:\n{_esc(traceback.format_exc())}</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    return send_file(pdf, mimetype='application/pdf', download_name=f'{safe}_太物図.pdf')


@app.route('/p/<seiban>/fromto.pdf')
def fromto_pdf(seiban):
    safe = _safe(seiban)
    d, _ = _dir(safe)
    seq, skel, _l, _lct, _dct = _classify_inputs(os.path.join(d, 'in'))
    if not seq:
        return _page('<div class=card err>制御シート(F＊)が無いためFrom-To図面を出力できません。</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    pdf = os.path.join(d, f'{safe}_FromTo図.pdf')
    try:
        TP.render_pdf(seq, skel, safe, pdf, numbers=_saved_numbers(safe))
    except Exception:
        app.logger.exception('fromto pdf failed %s', safe)
        return _page(f'<div class=card err>PDF生成でエラー:\n{_esc(traceback.format_exc())}</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    return send_file(pdf, mimetype='application/pdf', download_name=f'{safe}_FromTo図.pdf')


def _saved_numbers(safe):
    """保存済み2人目の最終端子番号を {(シート,round(x),round(y)): 端子番号} で返す(PDF反映用)。"""
    d, _ = _dir(safe)
    final_csv = os.path.join(d, f'{safe}_端子番号リスト.csv')
    if not os.path.exists(final_csv):
        return {}
    for enc in ('cp932', 'utf-8-sig', 'utf-8'):
        try:
            with open(final_csv, encoding=enc, newline='') as f:
                return TP.numbers_from_final(list(csv.reader(f)))
        except UnicodeDecodeError:
            continue
    return {}


# ---------- 4. 保存（サーバ側） ----------
@app.route('/p/<seiban>/save', methods=['POST'])
def save(seiban):
    safe = _safe(seiban)
    d, _ = _dir(safe)
    if not os.path.isdir(d):
        return jsonify(ok=False, error='製番が見つかりません')
    data = request.get_json(force=True, silent=True) or {}
    final = data.get('final') or []
    diff = data.get('diff') or []
    try:
        # ①最終端子番号リスト（次工程=シーケンス図記入／PDFに使用）
        _write_csv(os.path.join(d, f'{safe}_端子番号リスト.csv'), final)
        # ②学習用（当初値＋確定値＋差分）
        _write_csv(os.path.join(d, f'{safe}_端子番号_学習用.csv'), diff)
        # 学習コーパスへ蓄積（複数製番一致で後日 learn)
        summary = TFB.ingest_rows(diff, source=f'web:{safe}')
    except Exception:
        app.logger.exception('save failed %s', safe)
        return jsonify(ok=False, error=traceback.format_exc()[:400])
    # メタ更新（確認件数・修正件数）
    checked = sum(1 for r in final[1:] if len(r) > 5 and str(r[5]) == '1') if final else 0
    meta = _load_meta(safe)
    meta.update({'saved': _now(), 'updated': _now(), 'assigned': True,
                 'checked': checked, 'diffs': summary.get('差異(修正)', 0),
                 'total': (len(final) - 1) if final else meta.get('total', 0)})
    # 3人目(From-To)を、保存された2人目の確定端子から再生成(工場の流れ: 2人目→3人目)。
    if ENABLE_FROMTO:
        try:
            indir = os.path.join(d, 'in')
            outdir = os.path.join(d, 'out')
            seq, skel, _l, _lct, _dct = _classify_inputs(indir)
            _gen_fromto(safe, seq, skel, _term_override_from_saved(safe), outdir, meta)
        except Exception:
            app.logger.exception('fromto regen failed %s', safe)
    _save_meta(safe, meta)
    app.logger.info('save seiban=%s rows=%d diffs=%s', safe, len(final) - 1, summary.get('差異(修正)'))
    msg = f'確認 {checked} 件、修正 {summary.get("差異(修正)",0)} 件を保存しました。'
    return jsonify(ok=True, message=msg)


def _write_csv(path, rows):
    with open(path, 'w', encoding='cp932', errors='replace', newline='') as f:
        csv.writer(f).writerows(rows)


# ---------- 5. 印刷（PDF） ----------
@app.route('/p/<seiban>/print')
def print_pdf(seiban):
    safe = _safe(seiban)
    d, _ = _dir(safe)
    indir = os.path.join(d, 'in')
    seq, skel, _layout, _lct, _dct = _classify_inputs(indir)
    if not seq:
        return _page('<div class=card err>制御シート(F＊)が無いため図面を出力できません。</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    numbers = _saved_numbers(safe)        # 保存済みの最終端子番号を図面に反映
    pdf = os.path.join(d, f'{safe}_端子番号記入図.pdf')
    try:
        TP.render_pdf(seq, skel, safe, pdf, numbers=numbers)
    except Exception:
        app.logger.exception('pdf failed %s', safe)
        return _page(f'<div class=card err>PDF生成でエラー:\n{_esc(traceback.format_exc())}</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    return send_file(pdf, mimetype='application/pdf',
                     as_attachment=False, download_name=f'{safe}_端子番号記入図.pdf')


@app.route('/p/<seiban>/numbered.dxf')
def numbered_dxf(seiban):
    """端子番号入りCAD(DXF)を生成して返す。保存済みの最終端子番号を反映。
    制御シートが複数ある場合はまとめてZIPで返す。"""
    safe = _safe(seiban)
    d, _ = _dir(safe)
    indir = os.path.join(d, 'in')
    outdir = os.path.join(d, 'out')
    seq, skel, _layout, _lct, _dct = _classify_inputs(indir)
    if not seq:
        return _page('<div class=card err>制御シート(F＊)が無いためCAD(DXF)を出力できません。</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    numbers = _saved_numbers(safe)
    try:
        files, _counts = SI.write_all_terminals_dxf(seq, skel, safe, outdir, numbers=numbers)
    except Exception:
        app.logger.exception('numbered dxf failed %s', safe)
        return _page(f'<div class=card err>DXF生成でエラー:\n{_esc(traceback.format_exc())}</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    if not files:
        return _page('<div class=card err>出力する図面がありません。</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    if len(files) == 1:
        return send_file(files[0], mimetype='image/vnd.dxf', as_attachment=True,
                         download_name=f'{safe}_端子番号入りCAD.dxf')
    import zipfile
    zpath = os.path.join(outdir, f'{safe}_端子番号入りCAD.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=os.path.basename(f))
    return send_file(zpath, mimetype='application/zip', as_attachment=True,
                     download_name=f'{safe}_端子番号入りCAD.zip')


@app.route('/p/<seiban>/locator.pdf')
def locator_pdf(seiban):
    safe = _safe(seiban)
    d, _ = _dir(safe)
    layout, lct, dct = _layout_and_dct(safe)
    if not (layout or lct or dct):
        return _page('<div class=card err>ロケーター図面がありません（配置図 D/G か LCT/DCT が必要です）。</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    pdf = os.path.join(d, f'{safe}_ロケーター図.pdf')
    # 自動生成した相番号図(LCT)= out/<製番>_ロケーター_LCT.dxf があれば、それが正本。
    meta = _load_meta(safe) or {}
    loc = meta.get('locator', {})
    gen_loc = None
    if loc.get('source') in ('gen-lct', 'gen-dct') and loc.get('dxf'):
        cand = os.path.join(d, 'out', loc['dxf'])
        if os.path.exists(cand):
            gen_loc = [cand]
    try:
        if dct:
            # 完成形(機器相番号 DCT)がそのままロケーター図。各機器のセルに相番号(A/B/C…)が
            # 入った正本を描画する。
            TP.render_dxf_pdf(dct, pdf, title_prefix=f'{safe}　ロケーター図（機器相番号・完成形）　')
        elif lct:
            # 相番号済みの中間図(LCT)をそのまま描画(配線ダクト前)。
            TP.render_dxf_pdf(lct, pdf, title_prefix=f'{safe}　ロケーター図（機器相番号・ダクト前）　')
        elif gen_loc:
            # 配置図から相番号を自動付与して生成したLCT。手本と同じ形式なのでそのまま描画。
            TP.render_dxf_pdf(gen_loc, pdf, title_prefix=f'{safe}　ロケーター図（機器相番号・自動生成）　')
        elif layout:
            # いずれも無い場合のみ、配置図にロケーター字(機器のセル)を赤字で重ねて生成(手本風)。
            rows = LAP.locator_rows(layout)
            TP.render_locator_pdf(layout, pdf, rows, title_prefix=f'{safe}　ロケーター図　')
        else:
            raise RuntimeError('描画対象がありません')
    except Exception:
        app.logger.exception('locator pdf failed %s', safe)
        return _page(f'<div class=card err>PDF生成でエラー:\n{_esc(traceback.format_exc())}</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    return send_file(pdf, mimetype='application/pdf', as_attachment=False,
                     download_name=f'{safe}_ロケーター図.pdf')


def _layout_and_dct(safe):
    d, _ = _dir(safe)
    seq, skel, layout, lct, dct = _classify_inputs(os.path.join(d, 'in'))
    return layout, lct, dct


@app.route('/p/<seiban>/file/<path:name>')
def getfile(seiban, name):
    safe = _safe(seiban)
    d, _ = _dir(safe)
    base = os.path.basename(name)
    for cand in (os.path.join(d, base), os.path.join(d, 'out', base), os.path.join(d, 'in', base)):
        if os.path.exists(cand):
            return send_file(cand, as_attachment=True, download_name=base)
    return _page('<div class=card err>ファイルがありません。</div>'
                 f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8100'))
    app.run(host='0.0.0.0', port=port, debug=False)
