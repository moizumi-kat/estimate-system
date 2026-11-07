# -*- coding: utf-8 -*-
"""ハーネス工程フロー(工場の流れに準拠)の統合ワークフロー。

工場の流れ:
  設計図面 → 【2人目=入口】① ロケーター付与 → ② 図面不備チェック → ③ 端子番号付与
                                  ↓ 次工程へ(①②③の成果を渡す)
                        ┌─────────┴─────────┐
                  【1人目】太物           【3人目】From-To   (並行)

run_workflow() は1製番ぶんの各工程UI/出力を生成し、ハブ(index)HTMLで流れを提示する。
各UIは自己完結HTML(社内資産は外部に出さずローカル確認)。
"""
import os
import glob
import html as _html

from . import locator_assign, locator_apply, harness_produce
from . import stage_views, stage_interactive


def _find(seiban_dir, seiban, codes):
    out = []
    for c in codes:
        out += glob.glob(os.path.join(seiban_dir, f'{seiban}-{c}*.DXF'))
        out += glob.glob(os.path.join(seiban_dir, f'{seiban}-{c}*.dxf'))
    return sorted(set(f for f in out if 'DCT' not in f.upper() or c == '*' ))


def run_workflow(seiban, seq_paths, skel_paths, layout_paths, dct_paths=None, out_dir='.'):
    """1製番の工程フローを一括実行し、各工程の確認UIとハブHTMLを out_dir に生成する。
    戻り: {工程: 統計} の dict。"""
    os.makedirs(out_dir, exist_ok=True)
    basis = locator_assign.basis_from_dct(dct_paths) if dct_paths else None

    def P(name):
        return os.path.join(out_dir, f'{seiban}_{name}')

    res = {}

    # ===== 2人目(入口) =====
    # ① ロケーター付与(自動): 設計図面へロケーター情報を書き戻す。
    #    手本通りに生成できていれば現場でも確認しないため、人による確認UIは設けない。
    loc_dxf = P('2人目a_ロケーター付図面.dxf')
    n_loc = 0
    try:
        n_loc = locator_apply.write_dxf(layout_paths, loc_dxf, basis_of=basis)
    except Exception as e:
        res['locator_dxf_error'] = str(e)
    # ② 図面不備チェック
    layout_path = layout_paths[0] if layout_paths else None
    defects = {'defects': [], 'external': 0, 'summary': {}}
    try:
        defects = harness_produce.analyze_defects(seq_paths, skel_paths, seiban, layout_path=layout_path)
    except Exception as e:
        res['defect_error'] = str(e)
    defect_html = P('2人目b_不備チェック.html')
    _write_defect_html(defect_html, seiban, defects)
    # ③ 端子番号付与(確認UI)
    term_html = P('2人目c_端子番号確認.html')
    term_stats = {}
    try:
        term_stats = stage_views.stage2_terminals(seq_paths, skel_paths, seiban, term_html)
    except Exception as e:
        res['terminal_error'] = str(e)

    res['2人目'] = {'locator': n_loc, 'defects': len(defects.get('defects', [])),
                    'terminals': term_stats.get('tb', 0),
                    'files': {'ロケーター付図面(DXF)': os.path.basename(loc_dxf),
                              '不備チェック': os.path.basename(defect_html),
                              '端子番号確認': os.path.basename(term_html)}}

    # ===== 1人目(並行): 太物 =====
    wire_html = P('1人目_配線確認.html')
    harness_csv = P('1人目_ハーネス.csv')
    n_wire = 0
    try:
        n_wire = stage_interactive.stage1_wires(skel_paths, seiban, wire_html)
        stage_interactive.export_stage1_harness(skel_paths, seiban, harness_csv)
    except Exception as e:
        res['stage1_error'] = str(e)
    res['1人目'] = {'wires': n_wire,
                    'files': {'太物配線 確認・編集': os.path.basename(wire_html),
                              '太物ハーネスCSV': os.path.basename(harness_csv)}}

    # ===== 3人目(並行): From-To =====
    fromto_html = P('3人目_FromTo確認.html')
    ft_stats = {}
    try:
        ft_stats = stage_views.stage3_fromto(seq_paths, skel_paths, seiban, fromto_html)
    except Exception as e:
        res['stage3_error'] = str(e)
    res['3人目'] = {'gousen': ft_stats.get('gousen', 0), 'wires': ft_stats.get('wires', 0),
                    'files': {'From-To 確認': os.path.basename(fromto_html)}}

    _write_hub(P('00_ハブ.html'), seiban, res)
    res['hub'] = os.path.basename(P('00_ハブ.html'))
    return res


def _esc(x):
    return _html.escape(str(x))


def _write_defect_html(out_html, seiban, defects):
    items = defects.get('defects', [])
    summ = defects.get('summary', {})
    rows = ''.join(
        f'<tr><td>{_esc(d.get("sheet",""))}</td><td>{_esc(d.get("gousen",""))}</td>'
        f'<td>{_esc(d.get("cat",""))}</td><td>{_esc(d.get("reason",""))}</td>'
        f'<td>{_esc(d.get("fix",""))}</td></tr>' for d in items)
    if not rows:
        rows = '<tr><td colspan=5 class=ok>図面不備は検出されませんでした（問題なし）。</td></tr>'
    sm = '　'.join(f'{_esc(k)}: <b>{_esc(v)}</b>' for k, v in summ.items())
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} 図面不備チェック</title><style>
body{{font-family:IPAGothic,"Yu Gothic",sans-serif;margin:0;background:#f4f4f5;font-size:14px;color:#1a1a1a}}
.page{{max-width:1100px;margin:14px auto;background:#fff;padding:18px 22px;box-shadow:0 1px 6px rgba(0,0,0,.15);border-radius:6px}}
h1{{font-size:18px;border-bottom:3px solid #1a1a1a;padding-bottom:8px}}
.sm{{background:#eef2f7;border:1px solid #cdd7e3;border-radius:6px;padding:8px 12px;margin:10px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #dcdcdc;padding:5px 8px;text-align:left}} th{{background:#efefef}}
.ok{{color:#2a7a2a;text-align:center;padding:14px}}</style></head><body><div class=page>
<h1>{_esc(seiban)}　② 図面不備チェック（2人目）</h1>
<div class=sm>{sm}</div>
<table><tr><th>シート</th><th>号線</th><th>分類</th><th>理由</th><th>修正案</th></tr>{rows}</table>
<p style="font-size:12px;color:#555">※ 不備があれば設計(前工程)へ戻します。無ければ端子番号付与へ進みます。</p>
</div></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)


def _write_hub(out_html, seiban, res):
    s2 = res.get('2人目', {})
    s1 = res.get('1人目', {})
    s3 = res.get('3人目', {})

    def links(d):
        return ''.join(f'<a href="{_esc(fn)}">{_esc(label)}</a>' for label, fn in d.get('files', {}).items())
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} 工程フロー</title><style>
body{{font-family:IPAGothic,"Yu Gothic",sans-serif;margin:0;background:#eef0f3;font-size:14px;color:#1a1a1a}}
.page{{max-width:980px;margin:16px auto;padding:0 14px}}
h1{{font-size:20px}} .sub{{color:#555;font-size:13px;margin-bottom:14px}}
.flow{{display:flex;flex-direction:column;align-items:center;gap:0}}
.card{{background:#fff;border:1px solid #d3d8df;border-radius:10px;padding:14px 18px;width:100%;max-width:680px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
.card h2{{font-size:16px;margin:0 0 4px}} .tag{{font-size:12px;color:#fff;border-radius:10px;padding:1px 9px;margin-left:6px}}
.t2{{background:#1565c0}} .t1{{background:#2a7a2a}} .t3{{background:#c2410c}}
.steps{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
.step{{flex:1;min-width:150px;background:#f6f8fb;border:1px solid #dde3ea;border-radius:7px;padding:8px 10px}}
.step .k{{font-weight:bold;font-size:13px}} .step .v{{font-size:12px;color:#555}}
a{{display:inline-block;margin:4px 8px 0 0;color:#1565c0;font-size:13px;text-decoration:none;border-bottom:1px solid #9cc3ff}}
.arrow{{font-size:22px;color:#888;margin:4px 0}}
.par{{display:flex;gap:14px;width:100%;max-width:680px}} .par .card{{flex:1}}
.par-label{{color:#888;font-size:12px;margin:2px 0}}
.badge{{display:inline-block;background:#fff4e5;border:1px solid #f0c48a;border-radius:10px;padding:1px 9px;font-size:12px;margin-left:6px}}
</style></head><body><div class=page>
<h1>{_esc(seiban)}　ハーネス工程フロー</h1>
<div class=sub>工場の流れに準拠：<b>2人目(入口)</b>でロケーター付与→不備チェック→端子番号付与。その後 <b>1人目(太物)</b> と <b>3人目(From-To)</b> が並行。各リンクは確認UI（同じフォルダに保存して開いてください）。</div>
<div class=flow>
 <div class=card>
  <h2>2人目（入口）<span class="tag t2">受け取り・前処理</span></h2>
  <div class=steps>
   <div class=step><div class=k>① ロケーター付与（自動）</div><div class=v>機器 {s2.get('locator',0)} 件（確認不要）</div></div>
   <div class=step><div class=k>② 図面不備チェック</div><div class=v>不備 {s2.get('defects',0)} 件</div></div>
   <div class=step><div class=k>③ 端子番号付与</div><div class=v>要付番 {s2.get('terminals',0)} 件</div></div>
  </div>
  <div>{links(s2)}</div>
 </div>
 <div class=arrow>▼ 次工程へ（並行）</div>
 <div class=par-label>1人目 と 3人目 は並行</div>
 <div class=par>
  <div class=card><h2>1人目<span class="tag t1">太物 ≥8sq</span></h2>
   <div class=v style="font-size:12px;color:#555">配線 {s1.get('wires',0)} 本（編集・CSV出力可）</div>
   <div>{links(s1)}</div></div>
  <div class=card><h2>3人目<span class="tag t3">From-To ≤5.5sq</span></h2>
   <div class=v style="font-size:12px;color:#555">号線 {s3.get('gousen',0)}／配線 {s3.get('wires',0)}</div>
   <div>{links(s3)}</div></div>
 </div>
</div>
<p style="font-size:12px;color:#777;margin-top:16px">※ 社内資産につき取り扱い注意。</p>
</div></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)
