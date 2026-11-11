# -*- coding: utf-8 -*-
"""図面上で確認できる『インタラクティブ』確認UI(自己完結HTML)。

一覧の行をクリックすると、図面上の該当機器・該当配線をハイライトして示す。座標→ピクセル
変換で図面(base64内蔵PNG)に正確に重ねる。社内資産は外部に出さずローカルで確認する。

  locator_confirm(layout_paths, seiban, out_html, basis_of=None):
      ロケーター工程。どの機器にロケーターを採用し、図面上でどの名前かを確認(機器↔図面)。
  stage1_wires(skel_paths, seiban, out_html):
      太物工程(太物)工程。各配線が『どこからどこへ』繋がるかを一本ずつ図面上に表示。
"""
import io
import html as _html
import base64
import json

import ezdxf

from . import locator_apply as LAP, locator_assign as LA, edge_trace
from . import tracer
from . import review_viewer as rv
from . import terminal_rules as term_rules
from .geometry import norm

_TR_MODEL = term_rules.load_model()   # 端子番号の確定判定モデル(手本学習)


def _render_base(dxf_path, pts, margin_frac=0.06, w_in=24.0, dpi=130):
    """dxf を pts の外接範囲(＋余白)で描画し、(data_uri, Wpx, Hpx, extent) を返す。
    extent=(x0,y0,x1,y1)。座標→ピクセル: px=(x-x0)/(x1-x0)*Wpx, py=(1-(y-y0)/(y1-y0))*Hpx。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.font_manager as fm
    try:
        fm.fontManager.addfont('/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf')
        matplotlib.rcParams['font.family'] = 'IPAGothic'
    except Exception:
        pass
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing import config as C

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx = (max(xs) - min(xs)) * margin_frac + 120
    my = (max(ys) - min(ys)) * margin_frac + 120
    x0, x1 = min(xs) - mx, max(xs) + mx
    y0, y1 = min(ys) - my, max(ys) + my
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    Frontend(RenderContext(doc), MatplotlibBackend(ax),
             config=C.Configuration(color_policy=C.ColorPolicy.BLACK)).draw_layout(msp, finalize=True)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect('equal')
    ax.set_axis_off()
    fig.set_size_inches(w_in, w_in * (y1 - y0) / (x1 - x0))
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, facecolor='white')
    plt.close(fig)
    buf.seek(0)
    from PIL import Image
    w_px, h_px = Image.open(buf).size
    uri = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    return uri, w_px, h_px, (x0, y0, x1, y1)


def _to_px(x, y, extent, w_px, h_px):
    x0, y0, x1, y1 = extent
    return round((x - x0) / (x1 - x0) * w_px, 1), round((1 - (y - y0) / (y1 - y0)) * h_px, 1)


def locator_confirm(layout_paths, seiban, out_html, basis_of=None):
    """ロケーター確認UI(インタラクティブ)。戻り: 採番件数。"""
    rows = LAP.locator_rows(layout_paths, basis_of)
    pts = [(r[2], r[3]) for r in rows]
    uri, w, h, ext = _render_base(layout_paths[0], pts)
    items = []
    for loc, sym, x, y, t in rows:
        px, py = _to_px(x, y, ext, w, h)
        items.append({'loc': loc, 'sym': sym, 'px': px, 'py': py})
    _write_marker_html(out_html, seiban, 'ロケーター確認', uri, w, h, items,
                       cols=['loc', 'sym'], headers=['ロケーター', '機器'],
                       note='どの機器にロケーターを採用し、図面上でどの名前になったかの確認。'
                            '行をクリックすると図面上に表示します。')
    return len(items)


def _edges_of(dxf_path):
    """edge_trace で『機器間の配線』(同点・同一機器内・重複を除去)を返す。
    戻り: [(adev, aterm, ax, ay, bdev, bterm, bx, by), ...]。太物ハーネスの一本一本。"""
    res = edge_trace.trace_edges(dxf_path, with_pos=True)
    edges = res[2] if isinstance(res, tuple) and len(res) >= 3 else []
    out, seen = [], set()
    for e in edges:
        (d1, t1, x1, y1), (d2, t2, x2, y2) = e[0], e[1]
        if abs(x1 - x2) + abs(y1 - y2) < 1 or str(d1) == str(d2):
            continue
        key = tuple(sorted([(str(d1), str(t1)), (str(d2), str(t2))]))
        if key in seen:
            continue
        seen.add(key)
        out.append((str(d1), str(t1), x1, y1, str(d2), str(t2), x2, y2))
    return out


def stage1_wires(skel_paths, seiban, out_html, save_url=None):
    """太物工程(太物)の配線を一本ずつ図面上に表示・編集するUI。戻り: 配線本数。
    save_url: 指定するとUIの「保存」はそのURLへJSON POST(サーバ保存)。未指定はCSVダウンロード。"""
    sheets = []
    total = 0
    for p in skel_paths:
        es = _edges_of(p)
        if not es:
            continue
        pts = [(e[2], e[3]) for e in es] + [(e[6], e[7]) for e in es]
        uri, w, h, ext = _render_base(p, pts)
        wires = []
        for (ad, at, ax, ay, bd, bt, bx, by) in es:
            ax_, ay_ = _to_px(ax, ay, ext, w, h)
            bx_, by_ = _to_px(bx, by, ext, w, h)
            wires.append({'a': f'{ad}:{at}', 'b': f'{bd}:{bt}',
                          'adev': ad, 'aterm': at, 'bdev': bd, 'bterm': bt,
                          'ax': ax_, 'ay': ay_, 'bx': bx_, 'by': by_})
        total += len(wires)
        sheets.append({'name': _basename(p), 'uri': uri, 'w': w, 'h': h, 'wires': wires})
    _write_wire_html(out_html, seiban, sheets, save_url=save_url)
    return total


def _split_dev(dev):
    if '-' in str(dev):
        d, n = str(dev).rsplit('-', 1)
        return d, n
    return str(dev), ''


def _is_tb_lug(dev, term):
    """端子番号未記入の端子台ラグ(DEVICE=TB・番号なし・端子なし/?)か。"""
    d, n = _split_dev(dev)
    return norm(d) == 'TB' and (not n) and (not term or term == '?')


def _terminals_by_sheet(seq_paths, skel_paths):
    """端子番号未記入の端子台ラグを『シートごと』に返す。仮番号は全体で一意採番。
    戻り: [(sheet_path, [{'prov','x','y','gousen':[...]}, ...]), ...]。
    (harness_sheet.build_sheet と同じ仮番号採番規則に揃える)"""
    paths = list(seq_paths) + list(skel_paths or [])
    per = {}
    gmembers = {}
    for p in paths:
        try:
            tn = tracer.trace_nodes(p)
        except Exception:
            tn = {}
        per[p] = tn
        for g, nd in tn.items():
            gmembers.setdefault(g, set()).update(nd['members'])
    lugs = set()
    for g, mem in gmembers.items():
        if str(g).startswith('M@'):
            continue
        for (dev, term, x, y) in mem:
            if _is_tb_lug(dev, term):
                lugs.add((round(x), round(y), norm(g)))
    tb_prov = {lug: f"仮{i + 1}" for i, lug in enumerate(sorted(lugs))}
    prov_gousen = {}
    for g, mem in gmembers.items():
        if str(g).startswith('M@'):
            continue
        for (dev, term, x, y) in mem:
            if _is_tb_lug(dev, term):
                prov = tb_prov.get((round(x), round(y), norm(g)))
                if prov:
                    prov_gousen.setdefault(prov, set()).add(g)
    sheets = []
    for p in paths:
        seen = {}
        for g, nd in per[p].items():
            if str(g).startswith('M@'):
                continue
            for (dev, term, x, y) in nd['members']:
                if _is_tb_lug(dev, term):
                    prov = tb_prov.get((round(x), round(y), norm(g)))
                    if prov and prov not in seen:
                        seen[prov] = {'prov': prov, 'x': x, 'y': y,
                                      'gousen': sorted(str(v) for v in prov_gousen.get(prov, []))}
        if seen:
            sheets.append((p, sorted(seen.values(),
                                     key=lambda t: int(str(t['prov']).replace('仮', '') or 0))))
    return sheets


def _suggest_allocation(terms):
    """制御端子の割付提案: 回路(号線の接頭数字)でTBにグループ化し、各群を物理順
    (上→下・左→右)に並べて連番(1,2,3…)を割り当てる。
    手本から予測した規則(回路ごと・号線順=物理順・連番)。戻り: {prov: (回路, 連番)}。"""
    import collections
    import re as _re

    def circ(t):
        g = t['gousen'][0] if t['gousen'] else ''
        m = _re.match(r'(\d+)', g)
        return m.group(1) if m else g

    groups = collections.defaultdict(list)
    for t in terms:
        groups[circ(t)].append(t)
    out = {}
    for pre, ts in groups.items():
        for i, t in enumerate(sorted(ts, key=lambda z: (-z['y'], z['x'])), 1):
            out[t['prov']] = (pre, i)
    return out


def assigned_terminals(seq_paths, skel_paths):
    """2人目(端子番号割付)で確定した端子番号を位置キーで返す。
    戻り: {(正規化機器名, round(x), round(y)): 端子番号}。
    3人目(ハーネス工程 From-To)の空欄端子をこの割付で補完するのに使う。
    対象は2人目が特別に解決した機器のみ(選択SW・非リレー: SPD/TH/WHM 等)。一般リレーは
    主回路ラグが密集し位置照合が不確実(例 OL)なため対象外とし、From-Toの既定を尊重する。"""
    nonrelay = _TR_MODEL.get('contradicted', set())
    out = {}
    for p, items in _all_terminals_by_sheet(seq_paths, skel_paths):
        for it in items:
            if not (it['num'] and it['num'] != '?' and it['tier'] == 'confirmed'):
                continue
            if term_rules.is_selector(it['dev']) or term_rules.devtype(it['dev']) in nonrelay:
                out[(norm(it['dev']), round(it['x']), round(it['y']))] = it['num']
    return out


def _selector_contact_syms(path):
    """選択SWの接点記号を {機器名(norm): [(機能'a'/'b', x, y)]} で返す。
    接点記号名 LS_A*=a接点(NO), LS_B*=b接点(NC)。機能判定に使う。"""
    import re as _re
    out = {}
    try:
        doc = ezdxf.readfile(path)
    except Exception:
        return out
    for ins in doc.modelspace().query('INSERT'):
        a = {at.dxf.tag: (at.dxf.text or '').strip() for at in ins.attribs} if ins.attribs else {}
        dv = a.get('DEVICE', '')
        if not _re.match(r'(43|CS|COS|SS)', dv):
            continue
        n = ins.dxf.name.upper()
        f = 'a' if 'LS_A' in n else ('b' if 'LS_B' in n else None)
        if not f:
            continue
        key = norm(f"{dv}-{a.get('DEVICE1', '')}")
        out.setdefault(key, []).append((f, ins.dxf.insert.x, ins.dxf.insert.y))
    return out


def _assign_selector(dev, terms, syms, model):
    """選択SWの端子へ、接点記号の機能(a/b)＋型式の在庫で端子番号を割り付ける。
    terms: この機器の端子item一覧。syms: 接点記号[(機能,x,y)]。
    機能別に、検出端子を記号で機能判定→型式の a端子/b端子 を小さい順にペアで割付。
    記号で機能が付かない端子が残る/数が合わない場合は確定にせず '?'(要確認)。"""
    pairs = term_rules.selector_func_pairs(dev, model)
    if not (pairs.get('a') or pairs.get('b')):
        return False
    # 各端子を最寄り接点記号の機能に割り当てる(400以内)。
    byf = {'a': [], 'b': []}
    for it in terms:
        best, bd = None, 400.0 ** 2
        for f, sx, sy in syms:
            d = (sx - it['x']) ** 2 + (sy - it['y']) ** 2
            if d < bd:
                bd, best = d, f
        if best is None:
            return False                     # 機能不明の端子あり→確定しない
        byf[best].append(it)
    ok = False
    for f in ('a', 'b'):
        pts = sorted(byf[f], key=lambda z: (round(z['x']), -z['y']))
        fp = pairs.get(f, [])
        # 2端子=1接点(上=ペア先頭, 下=ペア末尾)。接点数が在庫ペア数を超えたら余りは'?'
        for i, it in enumerate(pts):
            pi = i // 2
            if pi < len(fp):
                it['num'], it['tier'] = fp[pi][i % 2], 'confirmed'
                ok = True
            else:
                it['num'], it['tier'] = '?', 'unknown'
    return ok


def _all_terminals_by_sheet(seq_paths, skel_paths):
    """シーケンス図の『全機器の接続端子』をシートごとに返す。
    端子番号は前回の実証と同じ review_viewer.extract で取得:
      ・drawn   = 図面に描かれた端子番号(確定)
      ・inferred= 記号から推論(要確認)
      ・unknown = 不明(空欄)
    接続号線はトレースの位置照合で補完。端子台の未記入ラグ(制御端子)には手本ルールの割付を与える。
    戻り: [(sheet_path, [{'dev','num','tier','tb','gousen':[],'x','y'}, ...]), ...]。"""
    import collections
    paths = list(seq_paths) + list(skel_paths or [])
    # 制御端子(TBラグ)の割付: (round x,round y)→(端子番号, TB表示)
    ctrl = {}
    for p, terms in _terminals_by_sheet(seq_paths, skel_paths):
        al = _suggest_allocation(terms)
        for t in terms:
            pre, seq = al.get(t['prov'], ('', ''))
            ctrl[(p, round(t['x']), round(t['y']))] = (str(seq) if seq else '',
                                                        f'TB({pre})' if pre else 'TB')
    sheets = []
    for p in paths:
        # 接続号線を位置から引くための辞書(トレース)
        gmap = {}
        try:
            for g, nd in tracer.trace_nodes(p).items():
                if str(g).startswith('M@'):
                    continue
                for (dev, term, x, y) in nd['members']:
                    gmap.setdefault((norm(dev), round(x), round(y)), set()).add(str(g))
        except Exception:
            pass
        try:
            terms = rv.extract(p).get('terminals', [])
        except Exception:
            terms = []
        uniq = collections.OrderedDict()
        for tup in terms:
            tup = list(tup) + ['', '', 0, 0, '']
            dev, term, x, y, tier = str(tup[0]), str(tup[1]), tup[2], tup[3], str(tup[4])
            key = (dev, term, round(x), round(y))
            if key in uniq:
                continue
            filled = term not in ('?', '', 'None')
            tb = ''
            num = term if filled else ''
            c = ctrl.get((p, round(x), round(y)))
            if c and not filled:               # 制御端子: 割付の連番を与える
                num, tb = c
                tier = 'allocated'
            # 機器名が無い無名接点は『機器の端子』ではなく線番(号線)のラベル点。
            # 線番は元図のSENBANブロックとして図面に残り、配線がつながる実端子
            # (RS-1:13 等)も別途注記されるため、端子番号の対象からは除外する
            # (番号の有無に依らず除外。番号は端子番号でなく線番のため)。
            if dev in ('接点', '', 'None'):
                continue
            gs = sorted(gmap.get((norm(dev), round(x), round(y)), []))
            # 端子番号が自分の号線(線番)と同じ点は、端子でなく線番ラベルの重複。
            # 端子台(TB)は線番=端子番号なので除外しない。
            if num and num in gs and not str(dev).upper().startswith('TB'):
                continue
            tier2 = tier if filled or tb else 'unknown'
            # 推論端子は手本学習モデルで確度判定: 確定可なら 'confirmed'(青)、
            # 手本と矛盾する機器タイプ(②)等は 'inferred'(赤=要確認)のまま。
            if tier2 == 'inferred' and term_rules.is_confirmed(dev, num, _TR_MODEL):
                tier2 = 'confirmed'
            uniq[key] = {'dev': dev, 'num': num, 'tier': tier2,
                         'tb': tb, 'gousen': gs, 'x': x, 'y': y}
        items = sorted(uniq.values(), key=lambda z: (-z['y'], z['x']))
        # ②非リレー機器(FAN/SPD/TH/WHM 等)は固有端子テンプレート(手本学習)で端子名を割付。
        # 機器ごとに上(y大)から順位付けし、タイプ×順位→端子名を当てる。
        # テンプレートがある順位は確定(青)、無い順位は誤った番号を出さず要確認(空欄)。
        # 対象=手本と矛盾した非リレー機器タイプ(FAN/SPD/TH/WHM 等)＋選択SW。
        nonrelay = _TR_MODEL.get('contradicted', set())
        bydev = {}
        for it in items:
            if it['tier'] == 'drawn':
                continue                         # 図面記載は尊重
            if term_rules.devtype(it['dev']) in nonrelay or term_rules.is_selector(it['dev']):
                bydev.setdefault(it['dev'], []).append(it)
        sel_syms = _selector_contact_syms(p) if any(
            term_rules.is_selector(d) for d in bydev) else {}
        for dev, lst in bydev.items():
            if term_rules.is_selector(dev):
                # 選択SW: 接点記号の機能(a/b)＋型式在庫で割付(富士AR22体系, 手本一致を確認済)。
                syms = sel_syms.get(norm(dev), [])
                if syms and _assign_selector(dev, lst, syms, _TR_MODEL):
                    continue
                # 記号(LS_A/LS_B)で機能判定できない切替SW(LS_CHROB等)→ 図面記載から学習した
                # 位置テンプレート(上→左→右 の順)で割付。同型の手本記載と全数一致を確認済。
                tp = term_rules.selector_template(dev, _TR_MODEL)
                if tp:
                    lst.sort(key=lambda z: (-z['y'], z['x']))
                    if all(str(r) in tp for r in range(len(lst))):
                        for rank, it in enumerate(lst):
                            it['num'], it['tier'] = tp[str(rank)], 'confirmed'
                        continue
                # それも不可→誤番号は出さず要確認。
                for it in lst:
                    it['num'], it['tier'] = '?', 'unknown'
                continue
            if term_rules.is_blank_type(dev, _TR_MODEL):
                # 番号を持たない機器(例: FAN=交流送風機)。手本は電源リードに端子番号を
                # 付けない→『番号なし(確定)』。推論の+/-等の誤番号は出さない。
                for it in lst:
                    it['num'], it['tier'] = '', 'confirmed'
                continue
            lst.sort(key=lambda z: -z['y'])
            for rank, it in enumerate(lst):
                nm = term_rules.template_name(dev, rank, _TR_MODEL)
                if nm:
                    it['num'], it['tier'] = nm, 'confirmed'           # 手本学習で確定
                else:
                    it['num'], it['tier'] = '?', 'unknown'            # 誤番号は出さず要確認
        # ④現場確認の修正学習(差異)を最終反映。複数製番で一致した修正のみ採用済(terminal_feedback)。
        #   同タイプ×順位(上→左→右)で確定端子番号を上書き。図面記載(drawn)は尊重して対象外。
        corr = _TR_MODEL.get('corrections', {}).get('term', {})
        if corr:
            cbydev = {}
            for it in items:
                cbydev.setdefault(it['dev'], []).append(it)
            for dev, lst in cbydev.items():
                lst.sort(key=lambda z: (-z['y'], z['x']))   # 取込側の順位付けと一致(全端子で順位)
                dt = term_rules.devtype(dev)
                for rank, it in enumerate(lst):
                    if it['tier'] == 'drawn':
                        continue                            # 図面記載は尊重(上書きしない)
                    v = corr.get(f'{dt}|{rank}')
                    if v is not None:
                        it['num'], it['tier'] = v, 'confirmed'
        # 番号を決められない端子(機器名の無い点・空欄)は注記しない。'?'(要確認)は残す。
        items = [it for it in items if it['num'] or it['tier'] != 'unknown']
        if items:
            sheets.append((p, items))
    return sheets


def stage2_terminal_assign(seq_paths, skel_paths, seiban, out_html, save_url=None):
    """端子番号割付の対話UI。シーケンス図上の『全機器の接続端子』を一覧表示し、行クリックで
    その端子を図面上でハイライトする。端子番号は設計記載分をそのまま表示し、未記入の
    制御端子には手本ルールの割付(連番)を自動提案(編集可)。戻り: 端子件数。
    save_url: 指定するとUIの「保存」はそのURLへJSON POST(サーバ保存)。未指定はCSVダウンロード。"""
    sheets_data = []
    total = 0
    for p, terms in _all_terminals_by_sheet(seq_paths, skel_paths):
        pts = [(t['x'], t['y']) for t in terms]
        uri, w, h, ext = _render_base(p, pts)
        items = []
        for t in terms:
            px, py = _to_px(t['x'], t['y'], ext, w, h)
            label = f"{t['dev']}:{t['num']}" if t['num'] else t['dev']
            orig = '' if str(t['num']) in ('?', '') else t['num']
            items.append({'dev': t['dev'], 'num': t['num'], 'orig': orig,
                          'tier': t.get('tier', ''),
                          'tb': t['tb'], 'gousen': '／'.join(t['gousen']),
                          'label': label, 'px': px, 'py': py,
                          'x': round(t['x'], 1), 'y': round(t['y'], 1)})
        total += len(items)
        sheets_data.append({'name': _basename(p), 'uri': uri, 'w': w, 'h': h, 'items': items})
    _write_terminal_html(out_html, seiban, sheets_data, save_url=save_url)
    return total


def _power_terminals_by_sheet(skel_paths):
    """動力・アースのTB端子をシートごとに返す。端子番号＝TB番号(DEVICE1)＋TERMINAL各文字。
    (手本ルール: 回路番号＋相記号/E。設計のTERMINAL属性に埋め込まれており決定的)
    戻り: [(sheet_path, [{'tb','num','x','y'}, ...]), ...]。"""
    sheets = []
    for p in (skel_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        items = []
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {k.dxf.tag: (k.dxf.text or '').strip() for k in ins.attribs}
            if norm(a.get('DEVICE', '')) != 'TB' or a.get('DEVICE2') == ':':
                continue
            no, term = a.get('DEVICE1', ''), a.get('TERMINAL', '')
            letters = [s.strip() for s in term.split(',') if s.strip()]
            if not (no and letters):
                continue
            try:
                x, y = ins.dxf.insert.x, ins.dxf.insert.y
            except Exception:
                continue
            for k, t in enumerate(letters):
                items.append({'tb': f'TB-{no}', 'num': f'{no}{t}',
                              'x': x, 'y': y - k * 18.0})   # 端子ごとに少し縦ずらし
        if items:
            sheets.append((p, items))
    return sheets


def stage2_power_terminals(skel_paths, seiban, out_html):
    """端子番号割付(動力・アース端子番号)の確認UI。スケルトン上のTBに端子番号(手本ルール:
    TB番号＋相記号/E)を表示し、リスト行クリックで該当TBを図面上でハイライトする。
    端子番号は設計のTERMINAL属性から決定的に生成(人の入力不要)。戻り: 端子件数。"""
    sheets_data = []
    total = 0
    for p, items in _power_terminals_by_sheet(skel_paths):
        pts = [(t['x'], t['y']) for t in items]
        uri, w, h, ext = _render_base(p, pts)
        out = []
        for t in items:
            px, py = _to_px(t['x'], t['y'], ext, w, h)
            out.append({'tb': t['tb'], 'num': t['num'], 'px': px, 'py': py})
        total += len(out)
        sheets_data.append({'name': _basename(p), 'uri': uri, 'w': w, 'h': h, 'items': out})
    _write_power_html(out_html, seiban, sheets_data)
    return total


def write_power_terminals_dxf(skel_paths, seiban, out_dir, layer='PWRTERM', color=5, height=22.0, dy=14.0):
    """動力・アース端子番号を各スケルトン図へ TEXT で書き込んだ DXF を保存する。戻り: (パス一覧, 件数)。"""
    import os
    out_files, counts = [], {}
    os.makedirs(out_dir, exist_ok=True)
    for p, items in _power_terminals_by_sheet(skel_paths):
        doc = ezdxf.readfile(p)
        msp = doc.modelspace()
        if layer not in doc.layers:
            doc.layers.add(layer, color=color)
        for t in items:
            msp.add_text(t['num'], dxfattribs={'layer': layer, 'height': height, 'color': color,
                                               'insert': (t['x'] + 20.0, t['y'] + dy)})
        out = os.path.join(out_dir, f"{seiban}_端子番号割付d_動力端子番号_{_basename(p)}.dxf")
        doc.saveas(out)
        out_files.append(out)
        counts[_basename(p)] = len(items)
    return out_files, counts


def _blacken(doc):
    """確認用に元図の線・文字を黒(ACI7)へ統一する(端子番号の色を目立たせるため)。
    元の設計DXFは変更せず、この記入版(確認用)だけ黒地にする。"""
    for ly in doc.layers:
        if ly.dxf.name not in ('TERMNO_OK', 'TERMNO_CHK'):
            ly.color = 7
    spaces = [doc.modelspace()] + [b for b in doc.blocks
                                   if not b.name.startswith('*')]
    for sp in spaces:
        for e in sp:
            try:
                e.dxf.color = 7
            except Exception:
                pass


def write_all_terminals_dxf(seq_paths, skel_paths, seiban, out_dir,
                            height=20.0, dx=12.0, dy=10.0, black_base=True):
    """シーケンス図へ全端子の端子番号を色分けで記入したDXFを出力する。
      青(ACI5) = 確定(図面記載 drawn)
      赤(ACI1) = 要確認(記号推論 inferred / 制御端子の割付)
    元図の線・文字は黒(確認用,black_base)。端子位置の少し右上に記入。
    戻り: (出力パス一覧, {シート名: 件数})。"""
    import os
    out_files, counts = [], {}
    os.makedirs(out_dir, exist_ok=True)
    for p, items in _all_terminals_by_sheet(seq_paths, skel_paths):
        doc = ezdxf.readfile(p)
        msp = doc.modelspace()
        if black_base:
            _blacken(doc)                      # 元図を黒へ(端子番号を書き込む前に)
        for layer, color in (('TERMNO_OK', 5), ('TERMNO_CHK', 1)):
            if layer not in doc.layers:
                doc.layers.add(layer, color=color)
        # 同一アンカーに複数端子(例:動力ラグ 1/3/5・2/4/6、OL 95/96/97/98)が集まると
        # 重なって潰れるため、位置でまとめて縦方向に段積みして記入する。
        step = height * 1.3
        groups = {}
        for it in items:
            if not it.get('num'):
                continue
            key = (round(it['x'] / 35.0), round(it['y'] / 35.0))
            groups.setdefault(key, []).append(it)
        n = 0
        for grp in groups.values():
            # 重複(同番号・同位置)は1つに。残りは番号順で安定化。
            seen, uniq = set(), []
            for it in grp:
                k = str(it['num'])
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(it)
            uniq.sort(key=lambda it: (str(it['num'])))
            top = (len(uniq) - 1) / 2.0
            for j, it in enumerate(uniq):
                # 確定(青)= 図面記載(drawn) または 手本学習で確定(confirmed)。
                ok = it.get('tier') in ('drawn', 'confirmed')
                layer, color = ('TERMNO_OK', 5) if ok else ('TERMNO_CHK', 1)
                oy = it['y'] + dy + (top - j) * step  # 上から順に縦積み
                msp.add_text(str(it['num']),
                             dxfattribs={'layer': layer, 'height': height, 'color': color,
                                         'insert': (it['x'] + dx, oy)})
                n += 1
        out = os.path.join(out_dir, f"{seiban}_端子番号割付_端子番号記入_{_basename(p)}.dxf")
        doc.saveas(out)
        out_files.append(out)
        counts[_basename(p)] = n
    return out_files, counts


def write_terminals_dxf(seq_paths, skel_paths, seiban, out_dir,
                        numbers=None, layer='TERMNO', color=1, height=25.0, dy=25.0):
    """各シーケンス図(制御シート)に端子番号を TEXT で書き込んだ DXF を保存する。
    端子台の未記入ラグ(仮番号)の位置に端子番号を注記する。
    numbers: {仮番号: 端子番号} があれば実番号を、無ければ仮番号を書く
             (端子番号割付が付番後、CSVから実番号を渡して再生成できる)。
    戻り: (出力パス一覧, {シート名: 書込件数})。"""
    import os
    numbers = numbers or {}
    out_files, counts = [], {}
    os.makedirs(out_dir, exist_ok=True)
    for p, terms in _terminals_by_sheet(seq_paths, skel_paths):
        doc = ezdxf.readfile(p)
        msp = doc.modelspace()
        if layer not in doc.layers:
            doc.layers.add(layer, color=color)
        n = 0
        for t in terms:
            label = numbers.get(t['prov']) or t['prov']
            msp.add_text(str(label),
                         dxfattribs={'layer': layer, 'height': height, 'color': color,
                                     'insert': (t['x'], t['y'] + dy)})
            n += 1
        out = os.path.join(out_dir, f"{seiban}_端子番号割付c_端子番号_{_basename(p)}.dxf")
        doc.saveas(out)
        out_files.append(out)
        counts[_basename(p)] = n
    return out_files, counts


def terminal_numbers_from_csv(csv_path):
    """端子番号割付の端子番号CSV(列: シート,仮番号,接続号線,端子番号)から {仮番号: 端子番号} を読む。
    端子番号が空の行は除外。write_terminals_dxf の numbers に渡す。"""
    import csv
    out = {}
    for enc in ('cp932', 'utf-8-sig', 'utf-8'):
        try:
            with open(csv_path, encoding=enc, newline='') as f:
                for row in csv.DictReader(f):
                    prov = (row.get('仮番号') or '').strip()
                    num = (row.get('端子番号') or '').strip()
                    if prov and num:
                        out[prov] = num
            return out
        except UnicodeDecodeError:
            continue
    return out


def export_stage1_harness(skel_paths, seiban, out_csv):
    """太物工程(太物)ハーネス情報を CSV 出力(サーバー側・無編集の基準出力)。
    列: シート, From機器, From端子, To機器, To端子, サイズ。戻り: 行数。
    UI(stage1_wires)の『ハーネスCSV出力』は、これに人手修正を反映したもの。"""
    import csv
    rows = []
    for p in skel_paths:
        name = _basename(p)
        for (ad, at, ax, ay, bd, bt, bx, by) in _edges_of(p):
            rows.append([name, ad, at, bd, bt, ''])
    with open(out_csv, 'w', encoding='cp932', errors='replace', newline='') as f:
        wr = csv.writer(f)
        wr.writerow(['シート', 'From機器', 'From端子', 'To機器', 'To端子', 'サイズ'])
        wr.writerows(rows)
    return len(rows)


# ---- HTML ビルダ ----
def _basename(p):
    import os
    return os.path.splitext(os.path.basename(p))[0]


def _esc(x):
    return _html.escape(str(x))


_CSS = """body{font-family:IPAGothic,"Yu Gothic",sans-serif;margin:0;background:#f4f4f5;font-size:14px;color:#1a1a1a}
.hd{padding:8px 14px;border-bottom:2px solid #1a1a1a;background:#fff}
.hd h1{font-size:17px;margin:2px 0}.hd .n{font-size:12px;color:#555}
.wrap{display:flex;gap:12px;padding:12px}
.L{flex:0 0 300px;max-height:90vh;overflow:auto;background:#fff;border-radius:6px;padding:6px}
.R{flex:1;background:#fff;border-radius:6px;overflow:auto;max-height:90vh}
.box{position:relative}.box img{display:block}
svg.ov{position:absolute;left:0;top:0;pointer-events:none}
table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{border-bottom:1px solid #eee;padding:3px 7px;text-align:left}
th{position:sticky;top:0;background:#efefef}
tbody tr{cursor:pointer} tbody tr.on td{background:#ffe3cc}
.lc{font-weight:bold;color:#c2410c;white-space:nowrap} .ar{color:#999;text-align:center}
.sh{font-weight:bold;margin:6px 4px 2px}"""


def _write_marker_html(out_html, seiban, subtitle, uri, w, h, items, cols, headers, note):
    """機器マーカー確認＋編集UI。ロケーター/機器を修正でき、対象外も可。CSV出力付き。"""
    body_rows = ''.join(
        f'<tr data-i="{i}">'
        f'<td><input class=cloc value="{_esc(d["loc"])}"></td>'
        f'<td><input class=csym value="{_esc(d["sym"])}"></td>'
        f'<td class=ce><input type=checkbox class=ex></td></tr>'
        for i, d in enumerate(items))
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} {_esc(subtitle)}</title><style>{_CSS}
.L input{{border:1px solid #ddd;border-radius:3px;padding:2px 4px;font-size:12.5px;width:92%}}
.ce{{text-align:center}} tr.del input{{text-decoration:line-through;color:#999;background:#f6f6f6}}
.dl{{background:#1565c0;color:#fff;border:none;border-radius:5px;padding:5px 14px;cursor:pointer;margin-left:10px;font-size:13px}}</style></head><body>
<div class=hd><h1>{_esc(seiban)}　{_esc(subtitle)}
<button class=dl onclick="exportCSV()">ロケーターCSV出力（修正反映）</button></h1>
<div class=n>機器 {len(items)}件。{_esc(note)} <b>ロケーター/機器は直接編集でき、対象外にもできます。</b></div></div>
<div class=wrap>
 <div class=L><table><thead><tr><th>{_esc(headers[0])}</th><th>{_esc(headers[1])}</th><th>対象外</th></tr></thead>
   <tbody>{body_rows}</tbody></table></div>
 <div class=R><div class=box style="width:{w}px"><img src="{uri}" width="{w}" height="{h}">
   <svg class=ov viewBox="0 0 {w} {h}" width="{w}" height="{h}"></svg></div></div>
</div>
<script>
const items={json.dumps(items, ensure_ascii=False)};
const ov=document.querySelector('.ov');
function curLoc(i){{const tr=document.querySelector(`tr[data-i="${{i}}"]`);return tr?tr.querySelector('.cloc').value:items[i].loc;}}
function draw(sel){{let s='';items.forEach((d,i)=>{{const on=i===sel;
 s+=`<circle cx="${{d.px}}" cy="${{d.py}}" r="${{on?26:12}}" fill="none" stroke="${{on?'#ff2d00':'#e65100'}}" stroke-width="${{on?5:2}}"/>`;
 if(on)s+=`<text x="${{d.px}}" y="${{d.py-32}}" fill="#ff2d00" font-size="32" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">${{curLoc(i)||''}}</text>`;}});ov.innerHTML=s;}}
draw(-1);
document.querySelectorAll('tbody tr').forEach(tr=>{{
 tr.querySelector('.ex').onchange=e=>tr.classList.toggle('del',e.target.checked);
 tr.onclick=ev=>{{if(ev.target.tagName==='INPUT')return;
  document.querySelectorAll('tbody tr').forEach(t=>t.classList.remove('on'));tr.classList.add('on');
  const i=+tr.dataset.i;draw(i);const d=items[i];
  document.querySelector('.R').scrollTo({{left:d.px-320,top:d.py-260,behavior:'smooth'}});}};}});
function exportCSV(){{let out=[['ロケーター','機器']];
 document.querySelectorAll('tbody tr').forEach(tr=>{{if(tr.querySelector('.ex').checked)return;
  out.push([tr.querySelector('.cloc').value,tr.querySelector('.csym').value]);}});
 const csv='\\ufeff'+out.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\\r\\n');
 const b=new Blob([csv],{{type:'text/csv'}});const a=document.createElement('a');
 a.href=URL.createObjectURL(b);a.download='{_esc(seiban)}_ロケーター.csv';a.click();}}
</script></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)


def _write_power_html(out_html, seiban, sheets):
    """端子番号割付(動力・アース端子番号)の確認UI。TB端子番号を図面上に表示し、リスト行クリックで
    該当TBをハイライト。端子番号は手本ルールで決定的(TB番号＋相記号/E)なので編集は不可、
    誤りのみ『除外』可。CSV出力付き。"""
    total = sum(len(s['items']) for s in sheets)
    secs = []
    for si, s in enumerate(sheets):
        rows = ''.join(
            f'<tr data-i="{i}"><td class=lc>{_esc(it["num"])}</td>'
            f'<td>{_esc(it["tb"])}</td>'
            f'<td class=ce><input type=checkbox class=ex></td></tr>'
            for i, it in enumerate(s['items']))
        secs.append(f"""<section data-sheet="{si}" style="display:{'block' if si == 0 else 'none'}">
<div class=wrap>
 <div class=L><div class=sh>{_esc(s['name'])}（動力端子 {len(s['items'])}）</div>
  <table><thead><tr><th>端子番号</th><th>端子台</th><th>除外</th></tr></thead>
  <tbody>{rows}</tbody></table></div>
 <div class=R><div class=box style="width:{s['w']}px"><img src="{s['uri']}" width="{s['w']}" height="{s['h']}">
   <svg class=ov viewBox="0 0 {s['w']} {s['h']}" width="{s['w']}" height="{s['h']}"></svg></div></div>
</div></section>""")
    tabs = ''.join(f'<button data-t="{si}" class="{"on" if si == 0 else ""}">{_esc(s["name"])}</button>'
                   for si, s in enumerate(sheets))
    data = {si: s['items'] for si, s in enumerate(sheets)}
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} 端子番号割付 動力端子番号 確認</title><style>{_CSS}
.tabs{{padding:6px 12px;background:#fff}} .tabs button{{margin-right:6px;padding:4px 10px;border:1px solid #ccc;background:#f3f3f3;border-radius:5px;cursor:pointer}}
.tabs button.on{{background:#1a1a1a;color:#fff;border-color:#1a1a1a}}
.ce{{text-align:center}} tr.del td{{text-decoration:line-through;color:#999;background:#f6f6f6}}
.dl{{background:#1565c0;color:#fff;border:none;border-radius:5px;padding:5px 14px;cursor:pointer;margin-left:10px;font-size:13px}}</style></head><body>
<div class=hd><h1>{_esc(seiban)}　端子番号割付 動力・アース端子番号 確認
<button class=dl onclick="exportCSV()">端子番号CSV出力</button></h1>
<div class=n>動力端子 {total}。行クリックで図面に該当端子台を表示。<b>端子番号は手本ルール（TB番号＋相記号/E）で自動決定。</b>誤りは「除外」。</div></div>
<div class=tabs>{tabs}</div>
{''.join(secs)}
<script>
const DATA={json.dumps(data, ensure_ascii=False)};
function ov(si){{return document.querySelector(`section[data-sheet="${{si}}"] .ov`);}}
function drawAll(si){{let s='';DATA[si].forEach(t=>{{s+=`<circle cx="${{t.px}}" cy="${{t.py}}" r="10" fill="none" stroke="#1565c0" stroke-width="2"/>`;}});ov(si).innerHTML=s;}}
function sel(si,i){{let s='';DATA[si].forEach((t,j)=>{{const on=j===i;
 s+=`<circle cx="${{t.px}}" cy="${{t.py}}" r="${{on?24:10}}" fill="none" stroke="${{on?'#ff2d00':'#1565c0'}}" stroke-width="${{on?5:2}}"/>`;
 if(on)s+=`<text x="${{t.px}}" y="${{t.py-30}}" fill="#ff2d00" font-size="30" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">${{t.num}}</text>`;}});ov(si).innerHTML=s;}}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{{
 const si=+b.dataset.t;document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 document.querySelectorAll('section[data-sheet]').forEach(se=>se.style.display=(+se.dataset.sheet===si)?'block':'none');}});
document.querySelectorAll('section[data-sheet]').forEach(se=>{{
 const si=+se.dataset.sheet;drawAll(si);
 se.querySelectorAll('tbody tr').forEach(tr=>{{
  tr.querySelector('.ex').onchange=e=>tr.classList.toggle('del',e.target.checked);
  tr.onclick=ev=>{{if(ev.target.tagName==='INPUT')return;
   se.querySelectorAll('tbody tr').forEach(t=>t.classList.remove('on'));tr.classList.add('on');
   const i=+tr.dataset.i;sel(si,i);const t=DATA[si][i];
   se.querySelector('.R').scrollTo({{left:t.px-320,top:t.py-260,behavior:'smooth'}});}};}});}});
function exportCSV(){{let out=[['シート','端子番号','端子台']];
 document.querySelectorAll('section[data-sheet]').forEach(se=>{{const nm=se.querySelector('.sh').textContent.replace(/（.*/,'');
  se.querySelectorAll('tbody tr').forEach(tr=>{{if(tr.querySelector('.ex').checked)return;
   out.push([nm,tr.querySelector('.lc').textContent,tr.children[1].textContent]);}});}});
 const csv='\\ufeff'+out.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\\r\\n');
 const b=new Blob([csv],{{type:'text/csv'}});const a=document.createElement('a');
 a.href=URL.createObjectURL(b);a.download='{_esc(seiban)}_端子番号割付動力端子番号.csv';a.click();}}
</script></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)


def _write_terminal_html(out_html, seiban, sheets, save_url=None):
    """端子番号割付の確認＋編集UI。シーケンス図上の『全機器の接続端子』を一覧表示し、
    行クリックで該当端子を図面上でハイライト。端子番号は設計記載分＋制御の割付(連番)を
    自動入力(編集可)。制御端子は黄色で区別。save_url 指定でサーバ保存、未指定でCSVダウンロード。"""
    total = sum(len(s['items']) for s in sheets)

    def _tierinfo(it):
        """(行クラス, 区分表示)。確定=青(図面記載/手本学習)、要確認=赤、割付=制御連番。"""
        t = it.get('tier', '')
        if it.get('tb') or t == 'allocated':
            return 'ctrl', '割付'
        if t in ('inferred', 'unknown'):
            return 'infer', '要確認'
        if t in ('drawn', 'confirmed'):
            return '', '確定'
        return '', '―'
    n_chk = sum(1 for s in sheets for it in s['items'] if _tierinfo(it)[1] == '要確認')
    # 端子番号コンボボックスの候補: 機器タイプごとに、図面内の確定/記載済み番号＋選択SW在庫を集める。
    # 誤って消しても候補から選び直せる(＋placeholderに元の自動値を表示)。
    import collections as _collections
    import re as _re2
    cand = _collections.defaultdict(set)
    for s in sheets:
        for it in s['items']:
            num = str(it.get('num', '')).strip()
            if num and num != '?':
                cand[term_rules.devtype(it['dev'])].add(num)
            if term_rules.is_selector(it['dev']):
                for t in term_rules.selector_inventory(it['dev'], _TR_MODEL):
                    cand[term_rules.devtype(it['dev'])].add(str(t))

    def _candkey(v):
        return (0, int(v)) if str(v).isdigit() else (1, str(v))

    def _did(dt):
        return 'dt_' + _re2.sub(r'[^0-9A-Za-z]', '_', dt or 'x')
    datalists = ''.join(
        f'<datalist id="{_did(dt)}">'
        + ''.join(f'<option value="{_esc(v)}">' for v in sorted(cand[dt], key=_candkey))
        + '</datalist>'
        for dt in sorted(cand) if cand[dt])
    secs = []
    for si, s in enumerate(sheets):
        rows = ''
        for i, it in enumerate(s['items']):
            cls, kubun = _tierinfo(it)
            val = '' if str(it.get('num', '')) in ('?', '') else _esc(it['num'])
            dt = term_rules.devtype(it['dev'])
            lst = f' list="{_did(dt)}"' if cand.get(dt) else ''
            ph = f'元:{_esc(it["orig"])}' if it.get('orig') else '―'
            rows += (
                f'<tr data-i="{i}" class="{cls}">'
                f'<td class=lc>{_esc(it["dev"])}</td>'
                f'<td><input class=tn value="{val}" data-orig="{val}"{lst} placeholder="{ph}"></td>'
                f'<td class=tr>{kubun}</td>'
                f'<td class=g>{_esc(it["gousen"])}</td>'
                f'<td class=ce><input type=checkbox class=ck></td></tr>')
        secs.append(f"""<section data-sheet="{si}" style="display:{'block' if si == 0 else 'none'}">
<div class=wrap>
 <div class=L><div class=sh>{_esc(s['name'])}（端子 {len(s['items'])}）</div>
  <table><thead><tr><th>機器</th><th>端子番号</th><th>区分</th><th>接続号線</th><th>確認</th></tr></thead>
  <tbody>{rows}</tbody></table></div>
 <div class=R><div class=box style="width:{s['w']}px"><img src="{s['uri']}" width="{s['w']}" height="{s['h']}">
   <svg class=ov viewBox="0 0 {s['w']} {s['h']}" width="{s['w']}" height="{s['h']}"></svg></div></div>
</div></section>""")
    tabs = ''.join(f'<button data-t="{si}" class="{"on" if si == 0 else ""}">{_esc(s["name"])}</button>'
                   for si, s in enumerate(sheets))
    data = {si: s['items'] for si, s in enumerate(sheets)}
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} 端子番号割付 確認・編集</title><style>{_CSS}
.tabs{{padding:6px 12px;background:#fff}} .tabs button{{margin-right:6px;padding:4px 10px;border:1px solid #ccc;background:#f3f3f3;border-radius:5px;cursor:pointer}}
.tabs button.on{{background:#1a1a1a;color:#fff;border-color:#1a1a1a}}
.L input{{border:1px solid #ddd;border-radius:3px;padding:2px 4px;font-size:12.5px;width:90%}}
.L td.g{{font-size:11.5px;color:#555}} .L td.tr{{font-size:11px;color:#c2410c;white-space:nowrap}}
tr.ctrl .tn{{background:#fffbe9}} tr.infer .tn{{background:#ffe0e0}} tr.infer td.tr{{font-weight:bold}}
tr.chg .tn{{background:#e8f5e9;border-color:#2e7d32;font-weight:bold}}
tr.ok td.ce{{background:#e8f5e9}} tr.ok .ck{{accent-color:#2e7d32}}
.hl{{color:#c2410c}} .chgc{{color:#2e7d32}} .okc{{color:#2e7d32}}
.ce{{text-align:center}}
.dl{{background:#1565c0;color:#fff;border:none;border-radius:5px;padding:5px 14px;cursor:pointer;margin-left:10px;font-size:13px}}
.gh{{background:#555;color:#fff;border:none;border-radius:5px;padding:5px 10px;cursor:pointer;margin-left:8px;font-size:12px}}</style></head><body>
<div class=hd><h1>{_esc(seiban)}　端子番号割付 確認・編集
<button class=dl onclick="save()">保存</button>
<button class=gh onclick="checkAll()">このシートを全て確認済みに</button></h1>
<div class=n>全機器の接続端子 {total}（<b class=hl>要確認 {n_chk}</b>／<b class=chgc>変更 <span id=chgn>0</span></b>／<b class=okc>確認 <span id=okn>0</span>/{total}</b>）。行／図の青字番号をクリックで相互に移動。<b>確定＝図面記載＋手本学習。要確認（赤）＝図面で確認して記入。割付（黄）＝制御端子の連番。</b>端子番号は候補から選択も自由入力も可。消すと欄に元の自動値（元:…）が出ます。確認した端子は「確認」にチェック。<b>「保存」</b>で①最終の端子番号リストと②学習用（当初値＋差分）を保存します。</div></div>
<div class=tabs>{tabs}</div>
{''.join(secs)}
{datalists}
<script>
const DATA={json.dumps(data, ensure_ascii=False)};
const SEIBAN={json.dumps(seiban, ensure_ascii=False)};
const SAVE_URL={json.dumps(save_url, ensure_ascii=False)};   // サーバ保存URL(null=CSVダウンロード)
const SEL={{}};                                   // シートごとの選択index(-1=無選択)
function ov(si){{return document.querySelector(`section[data-sheet="${{si}}"] .ov`);}}
function col(t){{return t.tb?'#1565c0':'#e65100';}}
// 表示する端子番号(表の現在値を優先=編集が即図面に反映)。空/「?」は非表示。
function dispNum(si,j){{const tr=document.querySelector(`section[data-sheet="${{si}}"] tr[data-i="${{j}}"]`);
 let v=tr?tr.querySelector('.tn').value:((DATA[si][j].num)||'');v=(v||'').trim();return (v&&v!=='?')?v:'';}}
function esc(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}}
// シーケンス図に端子番号を青字で描画。選択端子は赤・拡大。丸と番号はクリック可能。
function drawSheet(si){{const sel=SEL[si];let s='';
 // 近接端子(例 避雷器SPD R/S/T)の番号が重ならないよう、同一位置グループ内で縦に段積みする。
 const grp={{}};
 DATA[si].forEach((t,j)=>{{const k=Math.round(t.px/8)+'_'+Math.round(t.py/8);(grp[k]=grp[k]||[]).push(j);}});
 const goff={{}};
 Object.keys(grp).forEach(k=>{{let idx=0;grp[k].forEach(j=>{{goff[j]=dispNum(si,j)?idx++:0;}});}});
 DATA[si].forEach((t,j)=>{{const on=j===sel;
 const num=dispNum(si,j);const c=on?'#ff2d00':col(t);
 const lh=on?22:15;const dy=-5-goff[j]*lh;   // 段積みの縦オフセット(同位置の2個目以降を上へ)
 s+=`<g data-i="${{j}}" style="cursor:pointer;pointer-events:all">`;
 s+=`<circle cx="${{t.px}}" cy="${{t.py}}" r="16" fill="#fff" fill-opacity="0.01"/>`;   // クリック用(不可視)
 s+=`<circle cx="${{t.px}}" cy="${{t.py}}" r="${{on?8:2.6}}" fill="none" stroke="${{c}}" stroke-width="${{on?2.5:1}}"/>`;   // 位置の丸は小さく(番号主役)
 if(num)s+=`<text x="${{t.px+7}}" y="${{t.py+dy}}" fill="${{on?'#ff2d00':'#1565c0'}}" font-size="${{on?24:16}}" font-weight="bold" paint-order="stroke" stroke="#fff" stroke-width="${{on?5:3.5}}">${{esc(num)}}</text>`;
 s+=`</g>`;}});
 ov(si).innerHTML=s;}}
// 端子を選択。fromDraw=図の番号クリック→表の行へスクロール、=false(行クリック)→図の番号へスクロール。
function selectTerm(si,j,fromDraw){{const se=document.querySelector(`section[data-sheet="${{si}}"]`);
 se.querySelectorAll('tbody tr').forEach(t=>t.classList.remove('on'));
 const tr=se.querySelector(`tr[data-i="${{j}}"]`);if(tr)tr.classList.add('on');
 SEL[si]=j;drawSheet(si);const t=DATA[si][j];
 if(fromDraw){{if(tr)tr.scrollIntoView({{block:'center',behavior:'smooth'}});}}
 else{{se.querySelector('.R').scrollTo({{left:t.px-320,top:t.py-260,behavior:'smooth'}});}}}}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{{
 const si=+b.dataset.t;document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 document.querySelectorAll('section[data-sheet]').forEach(se=>se.style.display=(+se.dataset.sheet===si)?'block':'none');}});
function refreshChg(){{let n=0;document.querySelectorAll('tbody tr').forEach(tr=>{{
 const inp=tr.querySelector('.tn');const chg=(inp.value||'').trim()!==(inp.dataset.orig||'').trim();
 tr.classList.toggle('chg',chg);if(chg)n++;}});
 const el=document.getElementById('chgn');if(el)el.textContent=n;}}
function refreshOk(){{let n=0;document.querySelectorAll('tbody tr').forEach(tr=>{{
 const ck=tr.querySelector('.ck').checked;tr.classList.toggle('ok',ck);if(ck)n++;}});
 const el=document.getElementById('okn');if(el)el.textContent=n;}}
function checkAll(){{const se=[...document.querySelectorAll('section[data-sheet]')].find(s=>s.style.display!=='none');
 if(se)se.querySelectorAll('tbody tr .ck').forEach(c=>c.checked=true);refreshOk();}}
document.querySelectorAll('section[data-sheet]').forEach(se=>{{
 const si=+se.dataset.sheet;SEL[si]=-1;drawSheet(si);
 ov(si).addEventListener('click',ev=>{{const g=ev.target.closest('[data-i]');if(g)selectTerm(si,+g.dataset.i,true);}});
 se.querySelectorAll('tbody tr').forEach(tr=>{{
  tr.querySelector('.tn').addEventListener('input',()=>{{refreshChg();drawSheet(si);}});
  tr.querySelector('.ck').addEventListener('change',refreshOk);
  tr.onclick=ev=>{{if(ev.target.tagName==='INPUT')return;selectTerm(si,+tr.dataset.i,false);}};}});}});
refreshChg();refreshOk();
function csvdl(name,arr){{const csv='\\ufeff'+arr.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\\r\\n');
 const b=new Blob([csv],{{type:'text/csv'}});const a=document.createElement('a');
 a.href=URL.createObjectURL(b);a.download=name;a.click();}}
// 保存: ①最終の端子番号リスト(次工程=シーケンス図への記入に使用) ②学習用(当初値＋確定値＋差分, 管理者が後日一括学習)。
function save(){{
 let fin=[['製番','シート','機器','接続号線','端子番号','確認','X','Y']];
 let dif=[['製番','シート','機器','接続号線','自動端子番号','確定端子番号','変更','X','Y']];
 document.querySelectorAll('section[data-sheet]').forEach(se=>{{const si=+se.dataset.sheet;const nm=se.querySelector('.sh').textContent.replace(/（.*/,'');
  se.querySelectorAll('tbody tr').forEach(tr=>{{const i=+tr.dataset.i;const t=DATA[si][i];
   const v=tr.querySelector('.tn').value;const ok=tr.querySelector('.ck').checked?'1':'';
   const chg=((v||'').trim()!==(t.orig||'').trim());
   fin.push([SEIBAN,nm,t.dev,t.gousen,v,ok,t.x,t.y]);
   dif.push([SEIBAN,nm,t.dev,t.gousen,(t.orig||''),v,(chg?'1':''),t.x,t.y]);}});}});
 if(SAVE_URL){{
  const btn=document.querySelector('button.dl');if(btn){{btn.disabled=true;btn.textContent='保存中…';}}
  fetch(SAVE_URL,{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{seiban:SEIBAN,final:fin,diff:dif}})}})
   .then(r=>r.json()).then(j=>{{if(btn){{btn.disabled=false;btn.textContent='保存';}}
    alert(j.ok?('サーバに保存しました。'+(j.message||'')):'保存に失敗: '+(j.error||''));}})
   .catch(e=>{{if(btn){{btn.disabled=false;btn.textContent='保存';}}alert('保存に失敗: '+e);}});
 }}else{{
  csvdl(SEIBAN+'_端子番号リスト.csv',fin);
  setTimeout(()=>csvdl(SEIBAN+'_端子番号_学習用.csv',dif),500);
 }}}}
</script></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)


def _write_wire_html(out_html, seiban, sheets, save_url=None):
    """太物工程(太物)配線の確認＋編集UI。From/To/サイズを修正でき、除外も可。
    save_url 指定でサーバ保存(JSON POST)、未指定でCSVダウンロード。"""
    total = sum(len(s['wires']) for s in sheets)
    secs = []
    for si, s in enumerate(sheets):
        rows = ''.join(
            f'<tr data-i="{i}">'
            f'<td><input class=fa value="{_esc(w["a"])}"></td><td class=ar>→</td>'
            f'<td><input class=fb value="{_esc(w["b"])}"></td>'
            f'<td><input class=sz value="" placeholder="例 8sq" size=6></td>'
            f'<td class=ce><input type=checkbox class=ex></td></tr>'
            for i, w in enumerate(s['wires']))
        secs.append(f"""<section data-sheet="{si}" style="display:{'block' if si==0 else 'none'}">
<div class=wrap>
 <div class=L><div class=sh>{_esc(s['name'])}（配線 {len(s['wires'])}本）</div>
  <table><thead><tr><th>From</th><th></th><th>To</th><th>サイズ</th><th>除外</th></tr></thead>
  <tbody>{rows}</tbody></table></div>
 <div class=R><div class=box style="width:{s['w']}px"><img src="{s['uri']}" width="{s['w']}" height="{s['h']}">
   <svg class=ov viewBox="0 0 {s['w']} {s['h']}" width="{s['w']}" height="{s['h']}"></svg></div></div>
</div></section>""")
    tabs = ''.join(f'<button data-t="{si}" class="{"on" if si==0 else ""}">{_esc(s["name"])}</button>'
                   for si, s in enumerate(sheets))
    data = {si: s['wires'] for si, s in enumerate(sheets)}
    names = {si: s['name'] for si, s in enumerate(sheets)}
    doc = f"""<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(seiban)} 太物工程 太物配線 確認・編集</title><style>{_CSS}
.tabs{{padding:6px 12px;background:#fff}} .tabs button{{margin-right:6px;padding:4px 10px;border:1px solid #ccc;background:#f3f3f3;border-radius:5px;cursor:pointer}}
.tabs button.on{{background:#1a1a1a;color:#fff;border-color:#1a1a1a}}
.L input{{border:1px solid #ddd;border-radius:3px;padding:2px 4px;font-size:12.5px;width:95%}}
.L input.sz{{width:56px}} .ce{{text-align:center}} tr.del input{{text-decoration:line-through;color:#999;background:#f6f6f6}}
.dl{{background:#1565c0;color:#fff;border:none;border-radius:5px;padding:5px 14px;cursor:pointer;margin-left:10px;font-size:13px}}</style></head><body>
<div class=hd><h1>{_esc(seiban)}　太物工程 太物配線 確認・編集
<button class=dl onclick="save()">{'保存' if save_url else 'ハーネスCSV出力（修正反映）'}</button></h1>
<div class=n>配線 {total}本。行クリックで図面に表示。<b>From/To/サイズは直接編集でき、誤りは「除外」で外せます。</b>修正後「保存」で太物工程ハーネス情報を保存。</div></div>
<div class=tabs>{tabs}</div>
{''.join(secs)}
<script>
const DATA={json.dumps(data, ensure_ascii=False)};
const NAMES={json.dumps(names, ensure_ascii=False)};
const SEIBAN={json.dumps(seiban, ensure_ascii=False)};
const SAVE_URL={json.dumps(save_url, ensure_ascii=False)};
function ov(si){{return document.querySelector(`section[data-sheet="${{si}}"] .ov`);}}
function drawAll(si){{let s='';DATA[si].forEach(w=>{{s+=`<line x1="${{w.ax}}" y1="${{w.ay}}" x2="${{w.bx}}" y2="${{w.by}}" stroke="#9cc3ff" stroke-width="2"/>`;}});ov(si).innerHTML=s;}}
function sel(si,i){{let s='';DATA[si].forEach((w,j)=>{{const on=j===i;
 s+=`<line x1="${{w.ax}}" y1="${{w.ay}}" x2="${{w.bx}}" y2="${{w.by}}" stroke="${{on?'#ff2d00':'#cfe0fb'}}" stroke-width="${{on?6:1.6}}"/>`;}});
 const w=DATA[si][i];
 s+=`<circle cx="${{w.ax}}" cy="${{w.ay}}" r="11" fill="#2a7a2a"/><circle cx="${{w.bx}}" cy="${{w.by}}" r="11" fill="#c0392b"/>`;
 s+=`<text x="${{w.ax}}" y="${{w.ay-16}}" fill="#2a7a2a" font-size="30" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">From</text>`;
 s+=`<text x="${{w.bx}}" y="${{w.by-16}}" fill="#c0392b" font-size="30" font-weight="bold" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="5">To</text>`;
 ov(si).innerHTML=s;}}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{{
 document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 document.querySelectorAll('section').forEach(se=>se.style.display='none');
 const si=b.dataset.t;document.querySelector(`section[data-sheet="${{si}}"]`).style.display='block';drawAll(si);}});
document.querySelectorAll('section').forEach(se=>{{const si=se.dataset.sheet;drawAll(si);
 se.querySelectorAll('tbody tr').forEach(tr=>{{
  tr.querySelector('.ex').onchange=e=>tr.classList.toggle('del',e.target.checked);
  tr.onclick=ev=>{{if(ev.target.tagName==='INPUT')return;
   se.querySelectorAll('tbody tr').forEach(t=>t.classList.remove('on'));tr.classList.add('on');
   const i=+tr.dataset.i;sel(si,i);const w=DATA[si][i];
   se.querySelector('.R').scrollTo({{left:(w.ax+w.bx)/2-320,top:(w.ay+w.by)/2-260,behavior:'smooth'}});}};}});}});
function splitFT(s){{const p=String(s).split(':');return p.length>1?[p.slice(0,-1).join(':'),p[p.length-1]]:[s,''];}}
function buildRows(){{let out=[['シート','From機器','From端子','To機器','To端子','サイズ']];
 document.querySelectorAll('section').forEach(se=>{{const si=se.dataset.sheet;
  se.querySelectorAll('tbody tr').forEach(tr=>{{
   if(tr.querySelector('.ex').checked)return;
   const fa=splitFT(tr.querySelector('.fa').value), fb=splitFT(tr.querySelector('.fb').value);
   out.push([NAMES[si],fa[0],fa[1],fb[0],fb[1],tr.querySelector('.sz').value]);}});}});
 return out;}}
function save(){{const out=buildRows();
 if(SAVE_URL){{const btn=document.querySelector('button.dl');if(btn){{btn.disabled=true;btn.textContent='保存中…';}}
  fetch(SAVE_URL,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{seiban:SEIBAN,kind:'wire',rows:out}})}})
   .then(r=>r.json()).then(j=>{{if(btn){{btn.disabled=false;btn.textContent='保存';}}alert(j.ok?('サーバに保存しました。'+(j.message||'')):'保存に失敗: '+(j.error||''));}})
   .catch(e=>{{if(btn){{btn.disabled=false;btn.textContent='保存';}}alert('保存に失敗: '+e);}});
 }}else{{const csv='\\ufeff'+out.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\\r\\n');
  const b=new Blob([csv],{{type:'text/csv'}});const a=document.createElement('a');
  a.href=URL.createObjectURL(b);a.download=SEIBAN+'_太物工程ハーネス.csv';a.click();}}}}
</script></body></html>"""
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(doc)
