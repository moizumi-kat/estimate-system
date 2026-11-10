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
from wireharness.fromto_qc import terminal_feedback as TFB
from wireharness.fromto_qc import terminal_print as TP
from wireharness.fromto_qc import run_local as RL
from wireharness.fromto_qc import dwg_convert as DWG
from wireharness.fromto_qc.geometry import norm as _norm

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('TERMINAL_WORK', os.path.join(HERE, 'terminal_work'))
os.makedirs(WORK, exist_ok=True)
PASSWORD = os.environ.get('TERMINAL_PASSWORD', '')
USER = os.environ.get('TERMINAL_USER', '')
# 1人目(太物工程)・3人目(ハーネス工程)の表示。まずは2人目のみリリースするため既定OFF。
# 環境変数 TERMINAL_STAGE13=1 で有効化(2人目の確認中に追加予定)。
ENABLE_STAGE13 = os.environ.get('TERMINAL_STAGE13', '') == '1'

app = Flask(__name__)
app.secret_key = os.environ.get('TERMINAL_SECRET', secrets.token_hex(16))
app.config['MAX_CONTENT_LENGTH'] = 300 * 1024 * 1024        # DXF一式(最大300MB)

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


# ---------- 1. ホーム ----------
@app.route('/')
def index():
    rows = _list_projects()
    if rows:
        trs = ''.join(
            f'<tr><td><b>{_esc(m.get("seiban"))}</b></td>'
            f'<td class=muted>{_esc(m.get("updated",""))[:16].replace("T"," ")}</td>'
            f'<td>{"保存済" if m.get("saved") else ("割付中" if m.get("assigned") else "生成のみ")}'
            f'（確認 {m.get("checked",0)}/{m.get("total",0)}）</td>'
            f'<td><a class="btn" href="/p/{_safe(m.get("seiban"))}">開く ▶</a></td></tr>'
            for m in rows)
        table = f'<table><tr><th>製番</th><th>更新</th><th>状態</th><th></th></tr>{trs}</table>'
    else:
        table = '<p class=muted>まだ製番がありません。「新しい製番」から設計DXFを登録してください。</p>'
    body = (f'<div class=card><h2>製番一覧</h2>{table}'
            f'<div style="margin-top:14px"><a class="btn o" href="/new">＋ 新しい製番（設計DXFを登録）</a></div></div>'
            f'<div class=card muted>流れ: ①設計DXFを選択 → ②ロケーター生成・保存 → ③端子番号割付 → ④保存 → ⑤PDF印刷。'
            f'　保存結果と修正差分はサーバに蓄積され、学習に使えます。</div>')
    return _page(body)


# ---------- 2. 新規（アップロード） ----------
@app.route('/new')
def new():
    body = ('<div class=card><h2>新しい製番：設計DXFを登録</h2>'
            '<form method=post action="/create" enctype="multipart/form-data">'
            '<p>製番 <input type=text name=seiban placeholder="例 1-12188-132" required '
            'style="padding:8px;border:1px solid #ccd;border-radius:6px"></p>'
            '<div class=drop>設計図一式を選択（制御シートF＊・スケルトンE＊・配置図D/G＊・機器相番号＊-DCT）<br>'
            '<span class=muted>DXF・DWG どちらでも可（DWGはサーバ内でDXFへ自動変換）</span><br>'
            '<input type=file name=files multiple accept=".dxf,.DXF,.dwg,.DWG" style="margin-top:10px"></div>'
            '<p style="margin-top:12px"><button class="btn o" type=submit>登録してロケーター生成 ▶</button> '
            '<span class=muted>アップロード→ロケーター生成→端子番号割付へ</span></p>'
            '</form></div><div class=card><a class="btn g" href="/">← ホーム</a></div>')
    return _page(body, '新しい製番')


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
    # 製番でDXFを分類(制御F/スケルトンE/配置D,G/機器相番号DCT)
    seq, skel, layout, dct = RL.collect_inputs(indir, safe)
    app.logger.info('create seiban=%s files=%d seq=%d skel=%d layout=%d dct=%d',
                    safe, len(saved), len(seq), len(skel), len(layout), len(dct))
    meta = {'seiban': safe, 'updated': _now(), 'counts': {
        'seq': len(seq), 'skel': len(skel), 'layout': len(layout), 'dct': len(dct)}}
    # 2. ロケーター生成・保存。手本(機器相番号 DCT)が有ればそれが正本＝そのまま使う。
    try:
        basis = LA.basis_from_dct(dct) if dct else None
        loc_csv = os.path.join(outdir, f'{safe}_ロケーター対応表.csv')
        if layout:
            LAP.write_table_csv(layout, loc_csv, basis_of=basis, dct_paths=dct)
        n_loc = len(LAP.locator_rows(layout, basis_of=basis, dct_paths=dct)) if layout else 0
        if dct:
            # 手本の機器相番号(各機器のセルにロケーター字)をロケーター図面とする。
            meta['locator'] = {'dxf': os.path.basename(dct[0]), 'source': 'dct',
                               'csv': os.path.basename(loc_csv) if layout else '', 'count': n_loc}
        elif layout:
            # DCTが無い場合のみ、配置図にロケーター字を重ねたDXFを生成(手本風の代替)。
            loc_dxf = os.path.join(outdir, f'{safe}_ロケーター付図面.dxf')
            LAP.write_dxf(layout, loc_dxf, basis_of=basis, dct_paths=dct)
            meta['locator'] = {'dxf': os.path.basename(loc_dxf), 'source': 'gen',
                               'csv': os.path.basename(loc_csv), 'count': n_loc}
        else:
            meta['locator'] = {}
    except Exception:
        app.logger.exception('locator failed %s', safe)
        meta['locator_error'] = traceback.format_exc()
    # 3. 端子番号割付UI(サーバ保存)
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
    if ENABLE_STAGE13:
        # 1人目 太物工程(太物配線)
        try:
            wire_html = os.path.join(outdir, f'{safe}_太物工程.html')
            meta['wires'] = SI.stage1_wires(skel, safe, wire_html, save_url=f'/p/{safe}/save-wire')
            meta['wire_html'] = os.path.basename(wire_html)
        except Exception:
            app.logger.exception('wire failed %s', safe)
            meta['wire_error'] = traceback.format_exc()
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
    """From-To UI を生成し meta を更新。term_ov=2人目の確定端子(自動 or 保存値)。"""
    ft_html = os.path.join(outdir, f'{safe}_ハーネス工程.html')
    st = SV.stage3_fromto(seq, skel, safe, ft_html, term_override=term_ov,
                          save_url=f'/p/{safe}/save-fromto')
    meta['ft_html'] = os.path.basename(ft_html)
    meta['gousen'] = st.get('gousen', 0)
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
    c = meta.get('counts', {})
    loc = meta.get('locator', {})
    loc_links = ''
    if loc.get('dxf'):
        dxf_label = 'ロケーター図面＝機器相番号(DXF)' if loc.get('source') == 'dct' else 'ロケーター付図面(DXF)'
        loc_links = (f'<a class="btn g" href="/p/{safe}/file/{_esc(loc["dxf"])}">{dxf_label}</a> '
                     f'<a class="btn g" href="/p/{safe}/locator.pdf" target=_blank>ロケーター図(PDF)</a> '
                     f'<a class="btn g" href="/p/{safe}/file/{_esc(loc.get("csv",""))}">対応表(CSV)</a>')
    else:
        loc_links = '<span class=muted>配置図(D/G)が無いためロケーターは生成されていません。</span>'
    assign_btn = (f'<a class="btn o" href="/p/{safe}/assign">端子番号割付を開く ▶</a>'
                  if meta.get('assign_html') else '<span class=err>割付UIの生成に失敗しました。</span>')
    saved = meta.get('saved')
    # 2人目 出力(保存後)
    two_out = ''
    if saved:
        two_out = (f'<div class=ok style="margin:8px 0">確認 {meta.get("checked",0)}/{meta.get("total",0)}・'
                   f'修正 {meta.get("diffs",0)} 件・保存 {_esc(meta.get("saved",""))[:16].replace("T"," ")}</div>'
                   f'<a class="btn g" href="/p/{safe}/file/{safe}_端子番号リスト.csv">最終リスト(CSV)</a> '
                   f'<a class="btn g" href="/p/{safe}/file/{safe}_端子番号_学習用.csv">学習用(CSV)</a> '
                   f'<a class="btn o" href="/p/{safe}/print" target=_blank>端子番号入り図面(PDF) ▶</a>')
    assign_btn = (f'<a class="btn o" href="/p/{safe}/assign">端子番号割付を開く ▶</a>'
                  if meta.get('assign_html') else '<span class=err>割付UIの生成に失敗しました。</span>')
    # 1人目 太物
    wire_btn = (f'<a class="btn o" href="/p/{safe}/wire">太物配線を開く ▶</a> '
                f'<a class="btn g" href="/p/{safe}/wire.pdf" target=_blank>太物図面(PDF)</a>'
                if meta.get('wire_html') else '<span class=err>太物UIの生成に失敗しました。</span>')
    wire_out = (f' <a class="btn g" href="/p/{safe}/file/{safe}_太物工程ハーネス.csv">太物ハーネス(CSV)</a>'
                if meta.get('wire_saved') else '')
    # 3人目 From-To
    ft_hint = '' if saved else '<div class=muted>※ 先に2人目を保存すると、確定端子がFrom-Toに反映されます。</div>'
    ft_btn = (f'<a class="btn o" href="/p/{safe}/fromto">From-Toを開く ▶</a> '
              f'<a class="btn g" href="/p/{safe}/fromto.pdf" target=_blank>From-To図面(PDF)</a>'
              if meta.get('ft_html') else '<span class=err>From-To UIの生成に失敗しました。</span>')
    ft_out = (f' <a class="btn g" href="/p/{safe}/file/{safe}_ハーネス工程FromTo.csv">From-To(CSV)</a>'
              if meta.get('ft_saved') else '')
    stage13 = ''
    if ENABLE_STAGE13:
        stage13 = (
            f'<div class=card><h2>1人目：太物工程（太物配線 ≥8sq）</h2>{wire_btn}{wire_out}'
            f'<div class=muted style="margin-top:6px">配線を確認・修正し「保存」。</div></div>'
            f'<div class=card><h2>3人目：ハーネス工程（From-To ≤5.5sq）</h2>{ft_hint}{ft_btn}{ft_out}'
            f'<div class=muted style="margin-top:6px">From-Toを確認・修正し「保存」。2人目の確定端子を反映します。</div></div>')
    body = (
        f'<div class=card><h2>製番 {_esc(safe)}</h2>'
        f'<p class=muted>制御シート{c.get("seq",0)}・スケルトン{c.get("skel",0)}・配置図{c.get("layout",0)}・機器相番号{c.get("dct",0)}</p>'
        f'<b>ロケーター</b>　{loc_links}</div>'
        f'<div class=card><h2>端子番号割付</h2>{assign_btn}{two_out}</div>'
        f'{stage13}'
        f'<div class=card><a class="btn g" href="/">← ホーム</a></div>')
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
    seq, skel, _l, _dct = RL.collect_inputs(os.path.join(d, 'in'), safe)
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
    seq, skel, _l, _dct = RL.collect_inputs(os.path.join(d, 'in'), safe)
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
    if ENABLE_STAGE13:
        try:
            indir = os.path.join(d, 'in')
            outdir = os.path.join(d, 'out')
            seq, skel, _l, _dct = RL.collect_inputs(indir, safe)
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
    seq, skel, _layout, _dct = RL.collect_inputs(indir, safe)
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


@app.route('/p/<seiban>/locator.pdf')
def locator_pdf(seiban):
    safe = _safe(seiban)
    d, _ = _dir(safe)
    layout, dct = _layout_and_dct(safe)
    if not layout:
        return _page('<div class=card err>ロケーター図面がありません（配置図 D/G が必要です）。</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    pdf = os.path.join(d, f'{safe}_ロケーター図.pdf')
    try:
        if dct:
            # 手本(機器相番号 DCT)がそのままロケーター図。各機器のセルにロケーター字(A/B/C/D/E)が
            # 入った設計の正本を描画する(＝手本どおり)。
            TP.render_dxf_pdf(dct, pdf, title_prefix=f'{safe}　ロケーター図（機器相番号）　')
        else:
            # DCTが無い場合のみ、配置図にロケーター字(機器のセル)を赤字で重ねて生成(手本風)。
            rows = LAP.locator_rows(layout)
            TP.render_locator_pdf(layout, pdf, rows, title_prefix=f'{safe}　ロケーター図　')
    except Exception:
        app.logger.exception('locator pdf failed %s', safe)
        return _page(f'<div class=card err>PDF生成でエラー:\n{_esc(traceback.format_exc())}</div>'
                     f'<div class=card><a class="btn g" href="/p/{safe}">← 戻る</a></div>')
    return send_file(pdf, mimetype='application/pdf', as_attachment=False,
                     download_name=f'{safe}_ロケーター図.pdf')


def _layout_and_dct(safe):
    d, _ = _dir(safe)
    seq, skel, layout, dct = RL.collect_inputs(os.path.join(d, 'in'), safe)
    return layout, dct


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
