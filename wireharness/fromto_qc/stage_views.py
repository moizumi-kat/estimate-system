# -*- coding: utf-8 -*-
"""工程ごとの『図面上で確認できる』確認UI(自己完結HTML)を生成する。

社内資産(図面)はローカルに閉じるため、画像は base64 で HTML に内蔵し、1ファイルで渡せる。
  stage3_fromto(seq_paths, skel_paths, seiban, out_html):
      3人目 From-To の確認UI。結線検査図(ネット色分け)＋From-To一覧(号線ごと)＋要確認。
"""
import os
import io
import glob
import html as _html
import base64
import collections

from . import export, conn_view


def _img_b64(png_path, max_w=1800):
    """PNGを max_w 幅に縮小して data URI を返す。"""
    from PIL import Image
    im = Image.open(png_path)
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.save(buf, format='PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _fromto_rows(seq_paths, skel_paths, seiban, tmp_dir):
    """export_fromto を実行し、From-To行(号線/種別/From/To)のリストを返す。"""
    os.makedirs(tmp_dir, exist_ok=True)
    export.export_fromto(seq_paths, skel_paths=skel_paths, seiban=seiban,
                         out_dir=tmp_dir, with_watari=True)
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


def stage3_fromto(seq_paths, skel_paths, seiban, out_html, tmp_dir=None):
    """3人目 From-To の確認UI(自己完結HTML)を out_html に書く。戻り: 統計 dict。"""
    tmp_dir = tmp_dir or os.path.join(os.path.dirname(os.path.abspath(out_html)), '_tmp')
    rows = _fromto_rows(seq_paths, skel_paths, seiban, tmp_dir)
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
            trs.append(f'<tr>{g_cell}<td>{frm}</td><td class="ar">→</td><td>{to}</td></tr>')
    img_html = ''.join(
        f'<div class="sheet"><div class="sh">{esc(n)}'
        + (f' <span class="st">nets={s.get("nets","?")} 浮端子={s.get("floating","?")} '
           f'ギャップ={s.get("trace_gap","?")}</span>' if 'error' not in s else
           f' <span class="err">描画失敗: {esc(s["error"])}</span>') + '</div>'
        + (f'<img src="{b}">' if b else '') + '</div>'
        for n, b, s in imgs)

    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(seiban)} 3人目 From-To 確認</title><style>
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
</style></head><body><div class="page">
<h1>{esc(seiban)}　3人目 From-To 確認UI</h1>
<div class="stat">
 <div>号線 <b>{stats['gousen']}</b></div>
 <div>配線(From-To) <b>{stats['wires']}</b></div>
 <div>制御 <b>{n_ctrl}</b></div>
 <div>主回路 <b>{n_main}</b></div>
 <div>シート <b>{stats['sheets']}</b></div>
</div>
<div class="cols">
 <div class="left"><table><tr><th>号線</th><th>From</th><th></th><th>To</th></tr>
 {''.join(trs)}</table></div>
 <div class="right">
  <div class="note">右＝結線検査図。<b>同じ色＝1つの繋がったネット</b>。左のFrom-To一覧と図面を見比べて、号線ごとの結線をご確認ください。</div>
  {img_html}
 </div>
</div>
<p class="note">※ 社内資産につき取り扱い注意。From-Toは図面(制御シート)から自動生成。</p>
</div></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)
    return stats
