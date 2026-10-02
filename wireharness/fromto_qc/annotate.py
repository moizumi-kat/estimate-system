# -*- coding: utf-8 -*-
"""生成結果の『確認用・注記付き図面』(SVG, ブラウザ表示)。

図面(DXF)をSVGに描き、その上に生成したハーネスの確認情報を重ねる:
  ・配線色(号線ごとの色。電源線は実色 赤/白/青/緑、制御線はパレット色)
  ・号線番号
  ・機器:端子(デバイス番号/端子番号)
位置は tracer のノードメンバ座標、座標変換は dxf_svg と同一式(X=x-xmin, Y=ymax-y)。
表示はチェックボックスで 色/号線/機器:端子 を切替できる。
"""
import os
import html as _html
import zlib

from . import dxf_svg, tracer

_POWER = {'赤': '#e23b3b', '白': '#9aa0a6', '青': '#2554d8', '緑': '#1a9a3a'}
_PALETTE = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
            '#8c564b', '#e377c2', '#17becf', '#bcbd22', '#7f7f7f']


def _color_of(gousen, color):
    if color in _POWER:
        return _POWER[color]
    g = str(gousen or '')
    return _PALETTE[zlib.crc32(g.encode('utf-8')) % len(_PALETTE)]


def _esc(s):
    return _html.escape(str(s if s is not None else ''))


def build_annotated_html(routed, seq_paths, skel_paths, seiban=''):
    """確認用の注記付き図面(自己完結HTML)を返す。"""
    # 号線 → 色
    color_of = {}
    for w in routed.get('wires', []):
        g = str(w.get('gousen') or w.get('color') or '')
        if g and g not in color_of:
            color_of[g] = _color_of(g, w.get('color'))

    sheets = []
    legend = {}
    for p in list(seq_paths or []) + list(skel_paths or []):
        try:
            r = dxf_svg.render([p])
        except Exception:
            continue
        xmin, ymax = r['xmin'], r['ymax']

        def X(x):
            return round(x - xmin, 1)

        def Y(y):
            return round(ymax - y, 1)

        dots, gl, dl = [], [], []
        try:
            nodes = tracer.trace_nodes(p)
        except Exception:
            nodes = {}
        for g, nd in nodes.items():
            gg = str(g)
            if gg.startswith('M@'):
                continue
            col = color_of.get(gg) or _color_of(gg, '')
            legend[gg] = col
            placed_g = False
            for (d, t, x, y) in nd.get('members', []):
                cx, cy = X(x), Y(y)
                dots.append(f'<circle cx="{cx}" cy="{cy}" r="2.6" fill="{col}" fill-opacity="0.85"/>')
                dev = f'{d}' + (f':{t}' if t and t != '?' else '')
                if dev:
                    dl.append(f'<text x="{cx + 3}" y="{cy + 7}" font-size="5.5" fill="#111">{_esc(dev)}</text>')
                if not placed_g:
                    gl.append(f'<text x="{cx + 3}" y="{cy - 3}" font-size="6" fill="{col}" font-weight="700">{_esc(gg)}</text>')
                    placed_g = True
        svg = (f'<svg viewBox="0 0 {r["w"]} {r["h"]}" preserveAspectRatio="xMidYMid meet">'
               f'<g class="dwg">{r["body"]}</g>'
               f'<g class="ov-color">{"".join(dots)}</g>'
               f'<g class="ov-gousen">{"".join(gl)}</g>'
               f'<g class="ov-dev">{"".join(dl)}</g></svg>')
        sheets.append((os.path.basename(p), svg))

    leg = ''.join(f'<span class="lg"><i style="background:{c}"></i>{_esc(g)}</span>'
                  for g, c in sorted(legend.items())[:60])
    sheets_html = ''.join(
        f'<div class="sheet"><h3>{_esc(name)}</h3><div class="svgbox">{svg}</div></div>'
        for name, svg in sheets) or '<p class="muted">描画できる図面(結線/スケルトン)がありません。</p>'

    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(seiban)} 確認図面</title>
<style>
 body{{margin:0;background:#eef1f5;color:#16233c;font-family:"Noto Sans JP","Yu Gothic",sans-serif;font-size:13px}}
 header{{background:#1f4fb0;color:#fff;padding:10px 16px;font-weight:700;position:sticky;top:0;z-index:5;
   display:flex;gap:16px;flex-wrap:wrap;align-items:center}}
 header .ttl{{font-size:15px}} label{{font-weight:400;font-size:12.5px}}
 .wrap{{padding:12px 14px}}
 .sheet{{background:#fff;border:1px solid #ccd3de;border-radius:10px;padding:10px;margin-bottom:14px}}
 .sheet h3{{margin:0 0 6px;font-size:13px}}
 .svgbox{{overflow:auto;border:1px solid #eee;background:#fff}}
 svg{{width:100%;height:auto;min-width:800px}}
 /* 図面 */
 .dwg line,.dwg path,.dwg circle{{fill:none;stroke:#333;stroke-width:0.4;vector-effect:non-scaling-stroke}}
 .dwg .wm{{stroke:#c0392b}} .dwg .we{{stroke:#1a9a3a}} .dwg .wc{{stroke:#555}}
 .dwg .gm{{stroke:#9aa7b8}} .dwg .sol{{fill:#333;stroke:none}}
 .dwg text{{fill:#222;font-family:sans-serif}} .dwg .dv{{fill:#114}} .dwg .sn{{fill:#1565c0}} .dwg .tm{{fill:#6a1b9a}}
 /* オーバーレイ 切替 */
 .lg{{display:inline-flex;align-items:center;gap:4px;margin:0 6px 4px 0;font-size:11px}}
 .lg i{{width:11px;height:11px;border-radius:50%;display:inline-block}}
 body:not(.c) .ov-color{{display:none}} body:not(.g) .ov-gousen{{display:none}} body:not(.d) .ov-dev{{display:none}}
</style></head>
<body class="c g">
<header><span class="ttl">確認図面 {_esc(seiban)}</span>
 <label><input type="checkbox" id="t-c" checked> 配線色(号線)</label>
 <label><input type="checkbox" id="t-g" checked> 号線番号</label>
 <label><input type="checkbox" id="t-d"> 機器:端子</label>
 <span style="flex:1"></span><a href="/" style="color:#cfe0ff">← メニュー</a></header>
<div class="wrap">
 <div class="sheet"><h3>凡例(号線→色)</h3><div>{leg or '<span class=muted>なし</span>'}</div></div>
 {sheets_html}
</div>
<script>
 function sync(){{var b=document.body;
   b.classList.toggle('c',document.getElementById('t-c').checked);
   b.classList.toggle('g',document.getElementById('t-g').checked);
   b.classList.toggle('d',document.getElementById('t-d').checked);}}
 ['t-c','t-g','t-d'].forEach(function(id){{document.getElementById(id).addEventListener('change',sync);}});
 sync();
</script></body></html>'''


def write(routed, seq_paths, skel_paths, path, seiban=''):
    html = build_annotated_html(routed, seq_paths, skel_paths, seiban=seiban)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path
