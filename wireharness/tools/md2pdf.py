# -*- coding: utf-8 -*-
"""現場確認シート.md → フォーム体裁のHTML → PDF（Chromium）。別紙の端子番号図を埋込む。"""
import base64, html, os, re, sys
from playwright.sync_api import sync_playwright

MD = '/home/user/estimate-system/wireharness/現場確認シート.md'
IMG = '/tmp/claude-0/-home-user-estimate-system/e69ea718-e47e-5da0-9e7b-a5595a02af3e/scratchpad/renders/tanshi_bangou.png'
OUT = '/home/user/estimate-system/wireharness/現場確認シート.pdf'

def inline(s):
    s = html.escape(s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    return s

lines = open(MD, encoding='utf-8').read().splitlines()
body = []
i = 0
# 先頭H1 とリード文を処理後、別紙画像を差し込む
img_b64 = base64.b64encode(open(IMG, 'rb').read()).decode()
inserted_img = False
in_ul = False

def close_ul():
    global in_ul
    if in_ul:
        body.append('</ul>'); in_ul = False

while i < len(lines):
    ln = lines[i].rstrip()
    if re.match(r'^#\s', ln):
        close_ul(); body.append(f'<h1>{inline(ln[2:])}</h1>')
    elif re.match(r'^##\s', ln):
        close_ul()
        if not inserted_img:
            body.append(f'<div class="fig"><img src="data:image/png;base64,{img_b64}">'
                        f'<div class="figcap">【別紙】「端子番号」とは、この番号のことです</div></div>')
            inserted_img = True
        body.append(f'<h2>{inline(ln[3:])}</h2>')
    elif ln.strip() == '---':
        close_ul()
    elif re.match(r'^-\s\[\s\]\s', ln):
        if not in_ul:
            body.append('<ul class="checks">'); in_ul = True
        body.append(f'<li><span class="box"></span>{inline(ln[6:])}</li>')
    elif re.match(r'^\s+-\s', ln):   # インデント付きサブ項目
        if not in_ul:
            body.append('<ul class="checks">'); in_ul = True
        body.append(f'<li class="sub">{inline(ln.strip()[2:])}</li>')
    elif re.match(r'^-\s', ln):      # 案内用の普通の箇条書き（回答欄ではない）
        if not in_ul:
            body.append('<ul class="checks">'); in_ul = True
        body.append(f'<li class="bullet">{inline(ln[2:])}</li>')
    elif ln.strip() == '':
        close_ul()
    elif ln.startswith('※'):
        close_ul(); body.append(f'<p class="note">{inline(ln)}</p>')
    else:
        close_ul(); body.append(f'<p>{inline(ln)}</p>')
    i += 1
close_ul()

HTML = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 16mm 15mm; }}
  * {{ font-family: 'IPAGothic','IPAPGothic',sans-serif; }}
  body {{ color:#111; font-size: 10.5pt; line-height: 1.6; }}
  h1 {{ font-size: 16pt; text-align:center; margin:0 0 6px; }}
  h2 {{ font-size: 12pt; background:#1f3b63; color:#fff; padding:5px 9px;
        border-radius:4px; margin:16px 0 8px; page-break-after:avoid; }}
  p {{ margin:4px 0; }}
  p.note {{ font-size:9.5pt; color:#333; background:#f2f5fa;
            border-left:4px solid #1f3b63; padding:6px 9px; margin:8px 0; }}
  ul.checks {{ list-style:none; margin:4px 0 8px; padding-left:2px; }}
  ul.checks li {{ margin:3px 0; page-break-inside:avoid; }}
  ul.checks li.sub {{ margin-left:26px; color:#333; font-size:10pt; }}
  ul.checks li.bullet {{ list-style:disc; margin-left:20px; }}
  .box {{ display:inline-block; width:12px; height:12px; border:1.5px solid #333;
          border-radius:2px; margin-right:8px; vertical-align:-1px; }}
  strong {{ color:#13335e; }}
  .fig {{ text-align:center; margin:10px 0 14px; page-break-inside:avoid; }}
  .fig img {{ width:88%; border:1px solid #ccc; }}
  .figcap {{ font-size:9.5pt; color:#444; margin-top:4px; }}
  h2 + p, h2 ~ p strong {{ }}
</style></head><body>
{''.join(body)}
</body></html>"""

hp = OUT + '.html'
open(hp, 'w', encoding='utf-8').write(HTML)
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path='/opt/pw-browsers/chromium-1194/chrome-linux/chrome')
    pg = b.new_page()
    pg.goto('file://' + hp)
    pg.pdf(path=OUT, format='A4', print_background=True,
           margin={'top':'16mm','bottom':'14mm','left':'15mm','right':'15mm'})
    b.close()
os.remove(hp)
print('PDF:', OUT, os.path.getsize(OUT), 'bytes')
