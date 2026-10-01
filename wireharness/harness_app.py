# -*- coding: utf-8 -*-
"""ハーネスデータ自動生成システム — Webアプリ(社内サーバ共有・ブラウザ動作)。

フロー:
  図面(DXF)アップロード → 一括生成(produce_seiban) → 確認UI(①設計不備→②ハーネス確認→③シート出力)
  → 出力ダウンロード(ハーネスデータ.txt / ラベルシート / 各CSV)。

起動: python wireharness/harness_app.py   (本番: gunicorn -b 0.0.0.0:8000 wireharness.harness_app:app)
運用: 詳細は wireharness/運用マニュアル.md を参照。
"""
import os
import re
import glob
import html
import hmac
import shutil
import secrets
import logging
import datetime
import traceback
from logging.handlers import RotatingFileHandler
from flask import Flask, request, redirect, url_for, send_file, abort, Response, session

from wireharness.fromto_qc import harness_produce as HP

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 80 * 1024 * 1024  # 80MB(大容量の配置図DXFに対応)
# セッション署名鍵。未設定なら起動毎にランダム(本番は HARNESS_SECRET を固定)。
app.secret_key = os.environ.get('HARNESS_SECRET', secrets.token_hex(16))
# 画面ログイン(評価フェーズはID+パスワードで可)。HARNESS_PASSWORD 未設定なら認証オフ(ローカル試用)。
HARNESS_USER = os.environ.get('HARNESS_USER', '')          # 空ならID照合なし(パスワードのみ)
HARNESS_PASSWORD = os.environ.get('HARNESS_PASSWORD', '')
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('HARNESS_WORK', os.path.join(HERE, 'harness_work'))
os.makedirs(WORK, exist_ok=True)

# --- ログ(ローテーション) ---
LOG_DIR = os.path.join(WORK, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
_handler = RotatingFileHandler(os.path.join(LOG_DIR, 'app.log'),
                               maxBytes=1_000_000, backupCount=5, encoding='utf-8')
_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
app.logger.addHandler(_handler)
app.logger.setLevel(logging.INFO)

# --- 知識ファイル(学習結果)のバックアップ ---
# 実際の保存先(HARNESS_STATE で永続領域に外部化され得る)をモジュールから解決する。
from wireharness.fromto_qc import harness_learn as _HL
from wireharness.fromto_qc import design_check as _DC
BACKUP_DIR = os.path.join(WORK, '_backup')


def backup_knowledge(tag='auto', keep=30):
    """学習結果(learned.json / design_cases.json / confirmed/)をタイムスタンプ退避。
    学習で上書きする前に呼ぶ。直近 keep 世代のみ保持。失敗しても処理は止めない。"""
    try:
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        dst = os.path.join(BACKUP_DIR, f'{ts}_{tag}')
        os.makedirs(dst, exist_ok=True)
        for src in (_HL.LEARNED_PATH, _DC.CASES_PATH):
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(dst, os.path.basename(src)))
        conf = _HL.CONFIRMED_DIR
        if os.path.isdir(conf):
            shutil.copytree(conf, os.path.join(dst, 'confirmed'), dirs_exist_ok=True)
        # 世代上限
        gens = sorted(glob.glob(os.path.join(BACKUP_DIR, '*')))
        for old in gens[:-keep]:
            shutil.rmtree(old, ignore_errors=True)
        app.logger.info('backup_knowledge ok: %s', dst)
        return dst
    except Exception:
        app.logger.exception('backup_knowledge failed')
        return None


@app.errorhandler(Exception)
def _on_error(e):
    # abort(404/400 等)はそのまま、未捕捉例外はログして分かりやすく表示
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    app.logger.exception('unhandled error: %s %s', request.method, request.path)
    body = ('<div class="card err">予期しないエラーが発生しました。'
            'お手数ですが管理者へログ(app.log)の確認をご依頼ください。</div>'
            '<div class="card"><a class="btn" href="/">← ホームへ</a></div>')
    return _page(body, title='エラー'), 500


# --- 画面ログイン(ID+パスワード。評価フェーズ向け。社内限定運用と併用可) ---
_LOGIN_HTML = '''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ハーネスシステム ログイン</title>
<style>body{font-family:"Noto Sans JP","Yu Gothic",sans-serif;background:#eef1f5;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.box{background:#fff;border:1px solid #ccd3de;border-radius:10px;padding:30px;width:320px}
h1{font-size:16px;color:#16233c;margin:0 0 18px}input{width:100%;padding:10px;margin-top:8px;
border:1px solid #ccd3de;border-radius:7px;font-size:14px;box-sizing:border-box}
button{width:100%;margin-top:14px;padding:11px;background:#1f4fb0;color:#fff;border:0;border-radius:8px;
font-size:14px;font-weight:700;cursor:pointer}.e{color:#c0392b;font-size:12px;margin-top:10px}</style>
</head><body><form class="box" method="post" action="/login">
<h1>ハーネスデータ自動生成システム</h1>
{USER}<input type="password" name="pw" placeholder="パスワード" autofocus>
<button>ログイン</button>{ERR}</form></body></html>'''


def _login_page(err=''):
    user_field = ('<input type="text" name="user" placeholder="ID" autofocus>'
                  if HARNESS_USER else '')
    html_ = _LOGIN_HTML.replace('{USER}', user_field).replace(
        '{ERR}', f'<div class="e">{_esc(err)}</div>' if err else '')
    return Response(html_, mimetype='text/html')


@app.before_request
def _require_login():
    if not HARNESS_PASSWORD:                      # 未設定=認証オフ(ローカル試用)
        return None
    p = request.path
    if p == '/login' or p == '/api/health' or p.startswith('/static'):
        return None
    if session.get('auth'):
        return None
    if p.startswith('/api/') or p.startswith('/confirm'):
        return Response('{"ok":false,"error":"未認証"}', status=401, mimetype='application/json')
    return redirect('/login')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not HARNESS_PASSWORD:
        return redirect('/')
    if request.method == 'POST':
        ok_pw = hmac.compare_digest(request.form.get('pw', ''), HARNESS_PASSWORD)
        ok_user = (not HARNESS_USER) or hmac.compare_digest(request.form.get('user', ''), HARNESS_USER)
        if ok_pw and ok_user:
            session['auth'] = True
            session['user'] = request.form.get('user', '') or 'user'
            app.logger.info('login ok user=%s', session['user'])
            return redirect('/')
        app.logger.info('login failed')
        return _login_page('IDまたはパスワードが違います')
    return _login_page()


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# 出力種別 → (ファイル接尾, 表示名, ダウンロード時のMIME)
OUTPUTS = [
    ('_ハーネスデータ.xlsx', 'ハーネスデータ Excel(既存システム取込→シール印刷)',
     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    ('_ハーネスデータ.txt', 'ハーネスデータ(社内形式TAB)', 'text/plain; charset=utf-8'),
    ('_ハーネス.csv', 'ハーネス(測長/ルート)', 'text/csv; charset=utf-8'),
    ('_機器対応表.csv', '機器対応表(正式↔仮名)', 'text/csv; charset=utf-8'),
    ('_設計指摘書.csv', '設計指摘書(前工程へ)', 'text/csv; charset=utf-8'),
]


def _seiban_dir(seiban):
    safe = ''.join(c for c in seiban if c.isalnum() or c in '-_')
    return os.path.join(WORK, safe), safe


def _list_seibans():
    out = []
    for d in sorted(glob.glob(os.path.join(WORK, '*'))):
        if not os.path.isdir(d):
            continue
        sb = os.path.basename(d)
        rv = os.path.join(d, 'out', f'{sb}_確認.html')
        out.append({'seiban': sb, 'has_out': os.path.exists(rv),
                    'n_in': len(glob.glob(os.path.join(d, 'in', '*')))})
    return out


def _esc(s):
    return html.escape(str(s or ''))


def _page(body, title='ハーネスデータ自動生成システム'):
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_esc(title)}</title>
<style>
 :root{{--bg:#eef1f5;--panel:#fff;--ink:#16233c;--line:#ccd3de;--muted:#5c6572;--accent:#1f4fb0;--ok:#0a6b2e}}
 *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);
   font-family:"Noto Sans JP","Yu Gothic",sans-serif;font-size:14px;line-height:1.6}}
 header{{background:var(--accent);color:#fff;padding:12px 20px;font-weight:700;font-size:16px}}
 .wrap{{max-width:980px;margin:18px auto;padding:0 16px}}
 .card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}}
 h2{{font-size:15px;margin:0 0 10px}}
 .drop{{border:2px dashed #9db4e8;border-radius:10px;padding:22px;text-align:center;color:var(--muted);background:#f7f9fe}}
 input[type=text]{{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:7px;width:220px}}
 button,.btn{{font:inherit;border:1px solid var(--line);background:#fff;border-radius:8px;padding:8px 14px;cursor:pointer;text-decoration:none;color:var(--ink);display:inline-block}}
 button.primary{{background:var(--accent);color:#fff;border-color:var(--accent);font-weight:700}}
 table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid var(--line);padding:7px 10px;text-align:left}}
 th{{background:#eef1f6}} .muted{{color:var(--muted);font-size:12px}}
 .err{{background:#fdecea;border:1px solid #f3c9c3;color:#8a1c12;border-radius:8px;padding:12px;white-space:pre-wrap}}
 .ok{{color:var(--ok);font-weight:700}} a{{color:var(--accent)}}
 .row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}}
</style></head><body>
<header>ハーネスデータ自動生成システム</header>
<div class="wrap">{body}</div></body></html>'''


@app.route('/')
def index():
    seibans = _list_seibans()
    rows = ''
    for s in seibans:
        links = ''
        if s['has_out']:
            sb = _esc(s['seiban'])
            links = (f'<a class="btn" href="/review/{sb}">確認UI ▶</a> '
                     f'<a class="btn" href="/outputs/{sb}">出力一覧</a>')
        else:
            links = '<span class="muted">未生成</span>'
        rows += (f'<tr><td><b>{_esc(s["seiban"])}</b></td><td>{s["n_in"]}枚</td>'
                 f'<td>{"生成済" if s["has_out"] else "―"}</td><td>{links}</td></tr>')
    table = ('<table><tr><th>製番</th><th>図面</th><th>状態</th><th>操作</th></tr>'
             + (rows or '<tr><td colspan=4 class="muted">まだ製番がありません。下から図面を登録してください。</td></tr>')
             + '</table>')
    form = '''
      <form method="post" action="/generate" enctype="multipart/form-data">
        <div class="row"><label>製番 <input type="text" name="seiban" placeholder="例 5-29026-5" required></label></div>
        <div class="drop" style="margin-top:10px">
          図面DXF一式を選択（シーケンス/結線図・スケルトン/外形図・内部配置図・ダクト図）<br>
          <input type="file" name="files" multiple accept=".dxf,.DXF" style="margin-top:8px">
        </div>
        <div class="row"><button class="primary" type="submit">図面を登録して一括生成 ▶</button>
          <span class="muted">アップロード→自動生成→確認UIへ</span></div>
      </form>'''
    body = (f'<div class="card"><h2>製番一覧</h2>{table}</div>'
            f'<div class="card"><h2>新しい製番を登録</h2>{form}</div>'
            f'<div class="card"><h2>単独検図（図面だけを検査）</h2>'
            f'<p class="muted">ハーネス生成とは別に、図面(DXF)の不具合を検図できます（R1-R7＋H1-H5＋AI補助＋学習）。'
            f'検図システムと同一エンジンをこのアプリに統合しています。</p>'
            f'<div class="row"><a class="btn" href="/check">単独検図を実行 ▶</a></div></div>'
            f'<div class="card"><h2>設計不備の学習（修正前後の図面を登録）</h2>'
            f'<p class="muted">設計不備を直した後、<b>修正前</b>と<b>修正後</b>の図面(DXF)を登録すると、'
            f'before→after差分で「実際の直し方」を学習し、次回以降の①設計不備の指摘＋実績修正案に反映します。</p>'
            f'<div class="row"><a class="btn" href="/design">設計不備を登録／一覧 ▶</a></div></div>'
            f'<div class="card muted">社内サーバ共有・ブラウザ動作。'
            f'正常な図面はハーネス生成まで全自動、図面不備は確認UIの①で前工程へ提示します。'
            f'　<a href="/help">使い方</a> ・ <a href="/admin">管理（バックアップ/ログ）</a>'
            + ('　・ <a href="/logout">ログアウト</a>' if HARNESS_PASSWORD else '')
            + '</div>')
    return _page(body)


@app.route('/generate', methods=['POST'])
def generate():
    seiban = (request.form.get('seiban') or '').strip()
    files = request.files.getlist('files')
    if not seiban or not files:
        return _page('<div class="card err">製番と図面ファイルを指定してください。</div>'
                     '<div class="card"><a class="btn" href="/">← 戻る</a></div>')
    d, safe = _seiban_dir(seiban)
    indir = os.path.join(d, 'in')
    outdir = os.path.join(d, 'out')
    os.makedirs(indir, exist_ok=True)
    os.makedirs(outdir, exist_ok=True)
    saved = []
    for f in files:
        if not f.filename.lower().endswith('.dxf'):
            continue
        p = os.path.join(indir, os.path.basename(f.filename))
        f.save(p)
        saved.append(p)
    if not saved:
        return _page('<div class="card err">DXFファイルがありません。</div>'
                     '<div class="card"><a class="btn" href="/">← 戻る</a></div>')
    app.logger.info('generate seiban=%s files=%d', safe, len(saved))
    try:
        HP.produce_seiban(saved, seiban=safe, out_dir=outdir)
    except Exception:
        app.logger.exception('generate failed seiban=%s', safe)
        return _page(f'<div class="card err">生成でエラーが発生しました:\n{_esc(traceback.format_exc())}</div>'
                     '<div class="card"><a class="btn" href="/">← 戻る</a></div>')
    return redirect(url_for('review', seiban=safe))


def _out_path(seiban, suffix):
    d, safe = _seiban_dir(seiban)
    return os.path.join(d, 'out', f'{safe}{suffix}'), safe


@app.route('/review/<seiban>')
def review(seiban):
    p, safe = _out_path(seiban, '_確認.html')
    if not os.path.exists(p):
        abort(404)
    return Response(open(p, encoding='utf-8').read(), mimetype='text/html')


@app.route('/labels/<seiban>')
def labels(seiban):
    # 印刷は既存システム(Excel取込)が担うためUIからは非表示。
    # ラベル直接印刷は第二弾の印刷機能として再有効化する想定で、生成・ルートは温存。
    p, safe = _out_path(seiban, '_ラベルシート.html')
    if not os.path.exists(p):
        abort(404)
    return Response(open(p, encoding='utf-8').read(), mimetype='text/html')


@app.route('/confirm/<seiban>', methods=['POST'])
def confirm(seiban):
    """確認UIの「確定・出力＋学習」。人手修正後の行を受け取り、
    モデル同一フォーマットの最終Excel/txtを書き出し、確定データを学習に反映する。"""
    d, safe = _seiban_dir(seiban)
    outdir = os.path.join(d, 'out')
    if not os.path.isdir(outdir):
        return {'ok': False, 'error': '製番が見つかりません。'}, 404
    data = request.get_json(force=True, silent=True) or {}
    rows = data.get('rows') or []
    corrections = data.get('corrections') or []
    if not rows:
        return {'ok': False, 'error': '確定する行がありません。'}, 400
    backup_knowledge(tag='confirm')     # 学習で上書きする前に退避
    app.logger.info('confirm seiban=%s rows=%d corrections=%d', safe, len(rows), len(corrections))
    try:
        res = HP.confirm_and_learn(rows, corrections=corrections, seiban=safe, out_dir=outdir)
    except Exception:
        return {'ok': False, 'error': traceback.format_exc()}, 500
    return {'ok': True, 'seiban': safe,
            'download': {'xlsx': f'/download/{safe}/_ハーネスデータ.xlsx',
                         'txt': f'/download/{safe}/_ハーネスデータ.txt'},
            'learned': res.get('learned'), 'wires': res.get('wires'),
            'corrections': res.get('corrections')}


@app.route('/outputs/<seiban>')
def outputs(seiban):
    d, safe = _seiban_dir(seiban)
    items = ''
    for suffix, label, _mime in OUTPUTS:
        p = os.path.join(d, 'out', f'{safe}{suffix}')
        if os.path.exists(p):
            items += (f'<tr><td>{_esc(label)}</td><td class="muted">{_esc(safe + suffix)}</td>'
                      f'<td><a class="btn" href="/download/{_esc(safe)}/{_esc(suffix)}">ダウンロード</a></td></tr>')
    body = (f'<div class="card"><h2>出力一覧：{_esc(safe)}</h2>'
            f'<table><tr><th>種別</th><th>ファイル</th><th></th></tr>{items}</table>'
            f'<div class="row"><a class="btn" href="/review/{_esc(safe)}">確認UI ▶</a>'
            f'<a class="btn" href="/">← 製番一覧</a></div></div>')
    return _page(body)


@app.route('/download/<seiban>/<path:suffix>')
def download(seiban, suffix):
    if not suffix.startswith('_') or '/' in suffix or '..' in suffix:
        abort(400)
    p, safe = _out_path(seiban, suffix)
    if not os.path.exists(p):
        abort(404)
    return send_file(p, as_attachment=True, download_name=f'{safe}{suffix}')


@app.route('/design')
def design_home():
    """設計不備の学習ページ: 修正前後の図面を登録、蓄積済みケース一覧。"""
    from wireharness.fromto_qc import design_check as DC
    data = DC.load_cases()
    cases = data.get('cases', [])
    summary = data.get('summary', {})
    rows = ''
    for c in cases:
        det = c.get('detection', {})
        l1 = '／'.join(det.get('層1検出ルール', []) or []) or '—'
        rows += (f'<tr><td><b>{_esc(c["seiban"])}</b></td><td>{_esc(c["sheet"])}</td>'
                 f'<td>{_esc("／".join(c.get("categories", [])))}</td>'
                 f'<td>{_esc("／".join(r.get("detail","") for r in c.get("revisions", [])) or "—")}</td>'
                 f'<td>{_esc(l1)}</td></tr>')
    table = ('<table><tr><th>製番</th><th>シート</th><th>分類</th><th>不備内容(改訂)</th>'
             '<th>層1自動検出</th></tr>'
             + (rows or '<tr><td colspan=5 class="muted">まだ登録がありません。</td></tr>') + '</table>')
    form = '''
      <form method="post" action="/design/learn" enctype="multipart/form-data">
        <div class="row"><label>製番 <input type="text" name="seiban" placeholder="例 6-21072-1" required></label>
          <label>シート <input type="text" name="sheet" placeholder="例 E001"></label></div>
        <div class="row"><label>修正前 DXF <input type="file" name="before" accept=".dxf,.DXF" required></label></div>
        <div class="row"><label>修正後 DXF <input type="file" name="after" accept=".dxf,.DXF">
          <span class="muted">（未修正でも登録可。あれば差分から直し方を学習）</span></label></div>
        <div class="row"><label>不備メモ（任意） <input type="text" name="note" placeholder="例 1Φ3WなのにTBがV" style="width:360px"></label></div>
        <div class="row"><button class="primary" type="submit">登録して学習 ▶</button></div>
      </form>'''
    body = (f'<div class="card"><h2>設計不備ケース（学習済み {summary.get("件数", len(cases))} 件）</h2>{table}'
            f'<p class="muted">分類別: {_esc(summary.get("分類別", {}))}</p></div>'
            f'<div class="card"><h2>修正前後の図面を登録して学習</h2>{form}</div>'
            f'<div class="card"><a class="btn" href="/">← 製番一覧</a></div>')
    return _page(body, title='設計不備の学習')


@app.route('/design/learn', methods=['POST'])
def design_learn():
    from wireharness.fromto_qc import design_check as DC
    seiban = (request.form.get('seiban') or '').strip()
    sheet = (request.form.get('sheet') or '').strip()
    note = (request.form.get('note') or '').strip()
    before = request.files.get('before')
    after = request.files.get('after')
    if not seiban or not before or not before.filename:
        return _page('<div class="card err">製番と修正前DXFは必須です。</div>'
                     '<div class="card"><a class="btn" href="/design">← 戻る</a></div>')
    d = os.path.join(WORK, '_design', _seiban_dir(seiban)[1])
    os.makedirs(d, exist_ok=True)
    bpath = os.path.join(d, f'before_{os.path.basename(before.filename)}')
    before.save(bpath)
    apath = None
    if after and after.filename:
        apath = os.path.join(d, f'after_{os.path.basename(after.filename)}')
        after.save(apath)
    backup_knowledge(tag='design')      # 学習で上書きする前に退避
    app.logger.info('design_learn seiban=%s sheet=%s after=%s', seiban, sheet, bool(apath))
    try:
        case = DC.build_case(seiban, sheet or 'sheet', bpath, apath, pdf_text=note)
        data = DC.add_cases([case])
    except Exception:
        return _page(f'<div class="card err">学習でエラーが発生しました:\n{_esc(traceback.format_exc())}</div>'
                     '<div class="card"><a class="btn" href="/design">← 戻る</a></div>')
    diff = case['diff']
    nchg = len(diff['attrs_changed'])
    nba = sum(x['n'] for x in diff['blocks_added'])
    nbr = sum(x['n'] for x in diff['blocks_removed'])
    body = (f'<div class="card"><h2 class="ok">✔ 登録・学習しました</h2>'
            f'<p>製番 <b>{_esc(seiban)}</b> / シート {_esc(sheet or "sheet")}</p>'
            f'<p>分類: <b>{_esc("／".join(case["categories"]))}</b></p>'
            f'<p>不備内容(改訂/メモ): {_esc("／".join(case["defect_texts"]) or "—")}</p>'
            f'<p>修正の実体（before→after差分）: ブロック +{nba}/-{nbr}・属性変更 {nchg} 件'
            f'{"（修正後なし＝不備のみ登録）" if not apath else ""}</p>'
            f'<p class="muted">蓄積ケース数: {data.get("summary", {}).get("件数")} 件。'
            f'次回以降の①設計不備で、この分類の実績修正案として参照されます。</p>'
            f'<div class="row"><a class="btn" href="/design">設計不備一覧へ</a>'
            f'<a class="btn" href="/">← 製番一覧</a></div></div>')
    return _page(body, title='設計不備の学習')


@app.route('/admin')
def admin():
    """管理: 学習結果のバックアップ状況・手動退避・ログ末尾。"""
    gens = sorted(glob.glob(os.path.join(BACKUP_DIR, '*')), reverse=True)[:20]
    rows = ''
    for g in gens:
        rows += f'<tr><td>{_esc(os.path.basename(g))}</td><td class="muted">{_esc(g)}</td></tr>'
    logtail = ''
    lp = os.path.join(LOG_DIR, 'app.log')
    if os.path.exists(lp):
        with open(lp, encoding='utf-8', errors='replace') as f:
            logtail = ''.join(f.readlines()[-40:])
    body = (
        '<div class="card"><h2>学習結果のバックアップ</h2>'
        '<p class="muted">learned.json / design_cases.json / confirmed/ を退避します（学習時に自動、直近30世代保持）。</p>'
        '<form method="post" action="/admin/backup"><button class="primary">今すぐバックアップ</button></form>'
        f'<table style="margin-top:10px"><tr><th>世代</th><th>場所</th></tr>'
        f'{rows or "<tr><td colspan=2 class=muted>まだありません</td></tr>"}</table></div>'
        f'<div class="card"><h2>ログ（末尾40行）</h2><pre style="white-space:pre-wrap;font-size:12px">{_esc(logtail) or "（ログなし）"}</pre></div>'
        '<div class="card"><a class="btn" href="/help">使い方</a> <a class="btn" href="/">← ホーム</a></div>')
    return _page(body, title='管理')


@app.route('/admin/backup', methods=['POST'])
def admin_backup():
    dst = backup_knowledge(tag='manual')
    body = (f'<div class="card"><h2 class="ok">✔ バックアップしました</h2>'
            f'<p class="muted">{_esc(dst) or "失敗しました（ログ参照）"}</p>'
            '<div class="row"><a class="btn" href="/admin">← 管理へ</a></div></div>')
    return _page(body, title='管理')


@app.route('/help')
def help_page():
    body = '''
      <div class="card"><h2>使い方（かんたんマニュアル）</h2>
        <h3>1. ハーネスデータを作る</h3>
        <ol>
          <li>ホームで <b>製番</b> を入力し、図面DXF一式（シーケンス/結線・スケルトン/外形・内部配置図）を選んで「図面を登録して一括生成」。</li>
          <li>確認UIが開きます。<b>①設計不備</b>：指摘があれば「設計へ戻す／製造で手直し」を選択。</li>
          <li><b>②ハーネス確認</b>：黄色＝要確認を修正/確認。</li>
          <li><b>③確定・出力＋学習</b>：ボタンでモデル同一形式のExcelを出力（既存システムへ取込→シール印刷）。修正内容は学習されます。</li>
        </ol>
        <h3>2. 単独検図（図面だけを検査）</h3>
        <ol>
          <li><a href="/check">単独検図</a> で図面DXFをアップロード→ R1-R7＋H1-H5（＋AI補助）＋学習済み見逃しルールで検査。</li>
          <li>検図システムと同一エンジンをこのアプリに統合。結果は{場所/問題/提案/根拠}様式で表示（判断は設計）。</li>
          <li>現場で見つかった「あるべき機器の欠落」等は『見逃し登録』で学習→次回以降 自動指摘。</li>
        </ol>
        <h3>3. 設計不備を学習させる（修正前後）</h3>
        <ol>
          <li>ホーム下部または <a href="/design">設計不備を登録／一覧</a> から、<b>修正前</b>と<b>修正後</b>の図面(DXF)を登録。</li>
          <li>before→after差分で「実際の直し方」を学習し、次回以降の①で実績修正案として参照されます。</li>
        </ol>
        <h3>4. 二つの学習</h3>
        <ul>
          <li><b>設計不備の学習</b>：修正前/不備/修正後 の3点セット（電気図面チェック）。層1=自動検出（R2容量逆転・R3中性線）、層2=参考提示。</li>
          <li><b>出力確認の学習</b>：③確定時に、人が直した箇所の差分だけを学習（既存知識は保持＝回帰ゼロ）。</li>
        </ul>
        <h3>5. バックアップ／ログ</h3>
        <p>学習結果は自動で退避されます（<a href="/admin">管理画面</a>で確認・手動退避・ログ閲覧）。</p>
      </div>
      <div class="card"><a class="btn" href="/">← ホーム</a></div>'''
    return _page(body, title='使い方')


def _classify_for_check(paths):
    """検図(run_check)用に seq / skel / layout(単一) / table(単一) へ振り分ける。"""
    seq, skel, layout, table = [], [], None, None
    for p in paths:
        b = os.path.basename(p)
        u = b.upper()
        if '社内確認' in b or '000-Z001' in u or re.search(r'-Z\d', u):
            table = table or p
        elif '内部配置' in b or '外形' in b or '配置図' in b or re.search(r'-[DG]\d', u):
            layout = layout or p
        elif 'シーケンス' in b or '結線' in b or '展開' in b or re.search(r'-[FH]\d', u):
            seq.append(p)
        elif 'スケルトン' in b or '系統' in b or re.search(r'-E\d', u):
            skel.append(p)
    return seq, skel, layout, table


def _learned_defect_findings(paths):
    """学習済み見逃しルール(defect_rules)を図面に適用した findings(kenzu形式)。"""
    try:
        from wireharness.fromto_qc import defect_rules, tracer
        from wireharness.fromto_qc.geometry import DrawingModel, norm
        dev_syms = set()
        for p in paths:
            try:
                m = DrawingModel(p)
            except Exception:
                continue
            for e in m.msp:
                if e.dxftype() == 'INSERT' and e.attribs:
                    a = {x.dxf.tag: (x.dxf.text or '').strip() for x in e.attribs}
                    d = a.get('DEVICE', '')
                    if d:
                        dev_syms.add(f"{d}-{a.get('DEVICE1','')}".strip('-'))
        gousen = {}
        for p in paths:
            try:
                for g, nd in tracer.trace_nodes(p).items():
                    if str(g).startswith('M@'):
                        continue
                    s = gousen.setdefault(str(g), set())
                    for (d, t, x, y) in nd['members']:
                        s.add(f"{d}-{t}".strip('-'))
            except Exception:
                pass
        return defect_rules.check(dev_syms, gousen)
    except Exception:
        app.logger.exception('learned defect check failed')
        return []


@app.route('/check', methods=['GET', 'POST'])
def check():
    """単独検図: 図面(DXF)をアップロード→ R1-R7＋H1-H5(＋AI補助)＋学習済み見逃しルール で検査。
    従来の検図と同一エンジン(R1-R7＋H1-H5。旧・単独検図アプリは本アプリに統合)。
    設計への指摘として {場所/問題/提案/根拠} 様式で表示。"""
    from wireharness.fromto_qc import run_check as _rc, defect_check as _dc
    if request.method == 'GET':
        form = '''
          <form method="post" enctype="multipart/form-data">
            <div class="drop">図面DXF一式を選択（シーケンス/結線・スケルトン/系統・内部配置図・社内確認表）<br>
              <input type="file" name="files" multiple accept=".dxf,.DXF" style="margin-top:8px"></div>
            <div class="row"><label class="muted"><input type="checkbox" name="ai"> AI補助(R6 SPD警報)も実行（要APIキー）</label></div>
            <div class="row"><button class="primary" type="submit">検図を実行 ▶</button></div>
          </form>'''
        body = (f'<div class="card"><h2>単独検図（図面不具合チェック）</h2>'
                f'<p class="muted">ハーネス生成とは別に、図面だけを検査できます。R1配置図漏れ/R2容量逆転/'
                f'R3中性線端子/R4変更漏れ/R5社内確認表/R7接地/H1-H5ハーネス化検図＋学習済みの見逃しルール。'
                f'判断は設計（本画面は指摘＋修正案の提示）。</p>{form}</div>'
                f'<div class="card"><a class="btn" href="/">← ホーム</a></div>')
        return _page(body, title='単独検図')
    files = request.files.getlist('files')
    ai = bool(request.form.get('ai'))
    d = os.path.join(WORK, '_check', datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    os.makedirs(d, exist_ok=True)
    saved = []
    for f in files:
        if f.filename and f.filename.lower().endswith('.dxf'):
            p = os.path.join(d, os.path.basename(f.filename))
            f.save(p)
            saved.append(p)
    if not saved:
        return _page('<div class="card err">DXFファイルを指定してください。</div>'
                     '<div class="card"><a class="btn" href="/check">← 戻る</a></div>', title='単独検図')
    seq, skel, layout, table = _classify_for_check(saved)
    app.logger.info('check files=%d seq=%d skel=%d layout=%s table=%s ai=%s',
                    len(saved), len(seq), len(skel), bool(layout), bool(table), ai)
    findings = _rc.run(seq, skel, layout, table, ai=ai)
    for lf in _learned_defect_findings(saved):
        findings.append({'rule': lf.get('rule', '学習'), 'severity': 'med', 'confidence': 'med',
                         '場所': '', '問題': lf.get('detail', ''), '提案': '設計に確認してください。',
                         '根拠': '学習済み見逃しルール(defect_rules)'})
    # 集計＋表示
    bycnt = {}
    for f in findings:
        bycnt[f['rule']] = bycnt.get(f['rule'], 0) + 1
    rows = ''
    for f in findings:
        rows += (f'<tr><td><b>{_esc(f.get("rule"))}</b></td><td>{_esc(f.get("severity",""))}</td>'
                 f'<td>{_esc(f.get("場所",""))}</td><td>{_esc(f.get("問題",""))}</td>'
                 f'<td>{_esc(f.get("提案",""))}</td><td class="muted">{_esc(f.get("根拠",""))}</td></tr>')
    table_html = ('<table><tr><th>ルール</th><th>重要度</th><th>場所</th><th>問題</th><th>提案</th><th>根拠</th></tr>'
                  + (rows or '<tr><td colspan=6 class="muted">指摘はありません（図面不具合は検出されませんでした）。</td></tr>')
                  + '</table>')
    missed = '''
      <form method="post" action="/check/learn_missed" style="margin-top:8px">
        <div class="row"><span class="muted">見逃し登録（学習）:</span>
          <input type="text" name="if_base" placeholder="機器A(例 MCCB)" style="width:140px">
          <span>が有るのに</span>
          <input type="text" name="need_base" placeholder="機器B(例 ET)" style="width:140px">
          <span>が無ければ指摘</span>
          <input type="text" name="name" placeholder="ルール名(任意)" style="width:180px">
          <button class="primary" type="submit">学習に追加</button></div>
      </form>'''
    body = (f'<div class="card"><h2>検図結果：{len(findings)}件　<span class="muted">内訳 {_esc(bycnt)}</span></h2>'
            f'{table_html}</div>'
            f'<div class="card"><h2>見逃しを学習させる</h2>'
            f'<p class="muted">現場で見つかった「本来あるべき機器の欠落」等を登録すると、次回以降の検図で自動指摘します。</p>'
            f'{missed}</div>'
            f'<div class="card"><a class="btn" href="/check">別の図面を検図</a>'
            f'<a class="btn" href="/">← ホーム</a></div>')
    return _page(body, title='単独検図 結果')


@app.route('/check/learn_missed', methods=['POST'])
def check_learn_missed():
    from wireharness.fromto_qc import defect_rules
    if_base = (request.form.get('if_base') or '').strip()
    need_base = (request.form.get('need_base') or '').strip()
    name = (request.form.get('name') or '').strip()
    if not if_base or not need_base:
        return _page('<div class="card err">機器A・機器Bを入力してください。</div>'
                     '<div class="card"><a class="btn" href="/check">← 戻る</a></div>', title='単独検図')
    backup_knowledge(tag='missed')
    rid = defect_rules.learn_from_missed(if_base, need_base, name=name,
                                         user=(session.get('user') or ''))
    app.logger.info('learn_missed %s: %s->%s', rid, if_base, need_base)
    body = (f'<div class="card"><h2 class="ok">✔ 学習しました（{_esc(rid)}）</h2>'
            f'<p>{_esc(if_base)} が有るのに {_esc(need_base)} が無ければ、次回以降の検図で指摘します。</p>'
            f'<div class="row"><a class="btn" href="/check">検図へ</a><a class="btn" href="/">← ホーム</a></div></div>')
    return _page(body, title='単独検図')


@app.route('/api/health')
def health():
    return {'status': 'ok', 'seibans': len(_list_seibans())}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    app.run(host='0.0.0.0', port=port, debug=False)
