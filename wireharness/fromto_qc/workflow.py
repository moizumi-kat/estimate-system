# -*- coding: utf-8 -*-
"""ハーネス工程フロー(工場の流れに準拠)の統合ワークフロー。

工場の流れ:
  設計図面 → 【端子番号割付=入口】① ロケーター付与 → ② 図面不備チェック → ③ 制御端子の割付
  (動力/機器の端子番号は設計図に記載済み=自動。動力は次に太物工程・太物へ)
                                  ↓ 次工程へ(①②③の成果を渡す)
                        ┌─────────┴─────────┐
                  【太物工程】太物           【ハーネス工程】From-To   (並行)

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


def run_workflow(seiban, seq_paths, skel_paths, layout_paths, dct_paths=None, out_dir='.',
                 terminal_csv=None):
    """1製番の工程フローを一括実行し、各工程の確認UIとハブHTMLを out_dir に生成する。
    terminal_csv: 端子番号割付の端子番号CSV。渡すとシーケンス図へ実番号を書き込む(無ければ仮番号)。
    戻り: {工程: 統計} の dict。"""
    os.makedirs(out_dir, exist_ok=True)
    basis = locator_assign.basis_from_dct(dct_paths) if dct_paths else None

    def P(name):
        return os.path.join(out_dir, f'{seiban}_{name}')

    res = {}

    # ===== 端子番号割付(入口) =====
    # ① ロケーター付与(自動): 設計図面へロケーター情報を書き戻す。
    #    手本通りに生成できていれば現場でも確認しないため、人による確認UIは設けない。
    loc_dxf = P('端子番号割付a_ロケーター付図面.dxf')
    n_loc = 0
    try:
        n_loc = locator_apply.write_dxf(layout_paths, loc_dxf, basis_of=basis, dct_paths=dct_paths)
    except Exception as e:
        res['locator_dxf_error'] = str(e)
    # 盤下ロケーター対応表(ロケーター↔実機器名)をCSVに出力
    loc_csv = P('端子番号割付a_ロケーター対応表.csv')
    try:
        locator_apply.write_table_csv(layout_paths, loc_csv, basis_of=basis, dct_paths=dct_paths)
    except Exception as e:
        res['locator_csv_error'] = str(e)
    # ② 図面不備チェック
    layout_path = layout_paths[0] if layout_paths else None
    defects = {'defects': [], 'external': 0, 'summary': {}}
    try:
        defects = harness_produce.analyze_defects(seq_paths, skel_paths, seiban, layout_path=layout_path)
    except Exception as e:
        res['defect_error'] = str(e)
    defect_html = P('端子番号割付b_不備チェック.html')
    _write_defect_html(defect_html, seiban, defects)
    # ③ 制御端子の割付(端子番号割付の仕事): 制御TB端子をシーケンス図上に表示し、リスト行クリックで
    #    該当端子をハイライト。端子番号(割付)を入力しCSV出力。ここで端子番号割付は一区切り。
    #    (動力/機器の端子番号は設計図に記載済み=自動。動力は次に太物工程・太物へ)
    term_html = P('端子番号割付c_制御端子割付.html')
    n_term = 0
    try:
        n_term = stage_interactive.stage2_terminal_assign(seq_paths, skel_paths, seiban, term_html)
    except Exception as e:
        res['terminal_error'] = str(e)
    # ③' 全端子の端子番号をシーケンス図へ色分け記入したDXFを出力。
    #     青=確定(図面記載) / 赤=要確認(記号推論・制御の割付)。
    term_dxfs = []
    try:
        term_dxfs, _c = stage_interactive.write_all_terminals_dxf(
            seq_paths, skel_paths, seiban, out_dir)
    except Exception as e:
        res['terminal_dxf_error'] = str(e)

    files2 = {'ロケーター付図面(DXF)': os.path.basename(loc_dxf),
              'ロケーター対応表(CSV)': os.path.basename(loc_csv),
              '不備チェック': os.path.basename(defect_html),
              '制御端子割付': os.path.basename(term_html)}
    for i, f in enumerate(term_dxfs):
        files2[f'端子番号記入シーケンス図{i+1}(DXF)'] = os.path.basename(f)
    res['端子番号割付'] = {'locator': n_loc, 'defects': len(defects.get('defects', [])),
                    'terminals': n_term, 'files': files2}

    # ===== 太物工程(並行): 太物 =====
    wire_html = P('太物工程_配線確認.html')
    harness_csv = P('太物工程_ハーネス.csv')
    n_wire = 0
    try:
        n_wire = stage_interactive.stage1_wires(skel_paths, seiban, wire_html)
        stage_interactive.export_stage1_harness(skel_paths, seiban, harness_csv)
    except Exception as e:
        res['stage1_error'] = str(e)
    res['太物工程'] = {'wires': n_wire,
                    'files': {'太物配線 確認・編集': os.path.basename(wire_html),
                              '太物ハーネスCSV': os.path.basename(harness_csv)}}

    # ===== ハーネス工程(並行): From-To =====
    # 2人目(端子番号割付)で確定した端子番号で From-To の端子を補正する。
    fromto_html = P('ハーネス工程_FromTo確認.html')
    ft_stats = {}
    try:
        term_ov = stage_interactive.assigned_terminals(seq_paths, skel_paths)
    except Exception:
        term_ov = None
    try:
        ft_stats = stage_views.stage3_fromto(seq_paths, skel_paths, seiban, fromto_html,
                                             term_override=term_ov)
    except Exception as e:
        res['stage3_error'] = str(e)
    res['ハーネス工程'] = {'gousen': ft_stats.get('gousen', 0), 'wires': ft_stats.get('wires', 0),
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
<h1>{_esc(seiban)}　② 図面不備チェック（端子番号割付）</h1>
<div class=sm>{sm}</div>
<table><tr><th>シート</th><th>号線</th><th>分類</th><th>理由</th><th>修正案</th></tr>{rows}</table>
<p style="font-size:12px;color:#555">※ 不備があれば設計(前工程)へ戻します。無ければ制御端子の割付へ進みます。</p>
</div></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)


def _write_hub(out_html, seiban, res):
    s2 = res.get('端子番号割付', {})
    s1 = res.get('太物工程', {})
    s3 = res.get('ハーネス工程', {})

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
<div class=sub>工場の流れに準拠：<b>端子番号割付(入口)</b>でロケーター付与→不備チェック→<b>制御端子の割付</b>。その後 <b>太物工程(太物)</b> と <b>ハーネス工程(From-To)</b> が並行。動力/機器の端子番号は設計図に記載済み(自動)で動力は太物工程へ。各リンクは確認UI（同じフォルダに保存して開いてください）。</div>
<div class=flow>
 <div class=card>
  <h2>端子番号割付（入口）<span class="tag t2">受け取り・前処理</span></h2>
  <div class=steps>
   <div class=step><div class=k>① ロケーター付与（自動）</div><div class=v>機器 {s2.get('locator',0)} 件（確認不要）</div></div>
   <div class=step><div class=k>② 図面不備チェック</div><div class=v>不備 {s2.get('defects',0)} 件</div></div>
   <div class=step><div class=k>③ 制御端子の割付</div><div class=v>制御端子 {s2.get('terminals',0)} 件</div></div>
  </div>
  <div>{links(s2)}</div>
 </div>
 <div class=arrow>▼ 次工程へ（並行）</div>
 <div class=par-label>太物工程 と ハーネス工程 は並行</div>
 <div class=par>
  <div class=card><h2>太物工程<span class="tag t1">太物 ≥8sq</span></h2>
   <div class=v style="font-size:12px;color:#555">配線 {s1.get('wires',0)} 本（編集・CSV出力可）</div>
   <div>{links(s1)}</div></div>
  <div class=card><h2>ハーネス工程<span class="tag t3">From-To ≤5.5sq</span></h2>
   <div class=v style="font-size:12px;color:#555">号線 {s3.get('gousen',0)}／配線 {s3.get('wires',0)}</div>
   <div>{links(s3)}</div></div>
 </div>
</div>
<p style="font-size:12px;color:#777;margin-top:16px">※ 社内資産につき取り扱い注意。</p>
</div></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)
