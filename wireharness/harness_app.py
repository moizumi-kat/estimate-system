# -*- coding: utf-8 -*-
"""ハーネスデータ自動生成システム — Webアプリ(社内サーバ共有・ブラウザ動作)。

フロー:
  図面(DXF)アップロード → 一括生成(produce_seiban) → 確認UI(①設計不備→②ハーネス確認→③シート出力)
  → 出力ダウンロード(ハーネスデータ.txt / ラベルシート / 各CSV)。

起動: python wireharness/harness_app.py   (本番: gunicorn -b 0.0.0.0:8000 wireharness.harness_app:app)
"""
import os
import glob
import html
import traceback
from flask import Flask, request, redirect, url_for, send_file, abort, Response

from wireharness.fromto_qc import harness_produce as HP

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('HARNESS_WORK', os.path.join(HERE, 'harness_work'))
os.makedirs(WORK, exist_ok=True)

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
                     f'<a class="btn" href="/labels/{sb}">ラベル</a> '
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
            f'<div class="card muted">社内サーバ共有・ブラウザ動作。'
            f'正常な図面はハーネス生成まで全自動、図面不備は確認UIの①で前工程へ提示します。</div>')
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
    try:
        HP.produce_seiban(saved, seiban=safe, out_dir=outdir)
    except Exception:
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
    p, safe = _out_path(seiban, '_ラベルシート.html')
    if not os.path.exists(p):
        abort(404)
    return Response(open(p, encoding='utf-8').read(), mimetype='text/html')


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
            f'<a class="btn" href="/labels/{_esc(safe)}">ラベルシート</a>'
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


@app.route('/api/health')
def health():
    return {'status': 'ok', 'seibans': len(_list_seibans())}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    app.run(host='0.0.0.0', port=port, debug=False)
