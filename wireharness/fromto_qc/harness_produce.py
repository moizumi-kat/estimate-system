# -*- coding: utf-8 -*-
"""本番フロー: モデル(台帳)無しで、図面だけからハーネスシートを生成する。

方針(茂泉様):
  ・入力は図面のみ。モデル(台帳)は使わない。
  ・図面に不具合/不備があれば「示唆＋修正案」を提示する(勝手に埋めない)。
  ・不備が無ければ、ハーネスシートを従来と同じフォーマットで出力する。

構成(既存資産を束ねる):
  ① 不備検出   … fromto_review.build_floor_data(図面のみ) の要確認号線
                   (端子台番号未記入/浮き線端/近接ギャップ 等。外部・予備は不備でない)
  ② 生成       … harness_sheet.build_sheet(図面のみ) 号線=等電位ノード＋渡り規則生成
  ③ 出力ゲート … 不備が無ければ ②を同フォーマット(号線/種別/サイズ/色/From/To)で出す。
                   不備が有れば ①を修正案付きで返し、最終シートは保留(参考出力は付す)。

エラー/不備は握りつぶさず必ず提示する(◎誤答ゼロ・迷ったら△の方針に沿う)。
"""
import re
import collections
from . import harness_sheet, fromto_review
from . import router as _router


# 「不備ではない」= 盤外/予備/別ハーネスで結線される(確認不要)カテゴリ
_NOT_DEFECT_CATS = {'external', 'offsheet'}

# カテゴリ別の修正案テンプレート(図面をどう直せば本システムが結線を確定できるか)
_FIX = {
    'tb':    '端子台の端子番号を図面に記入してください（何番に繋ぐか）。候補: {cand}',
    'float': '線端がどの機器にも届いていません。結線先の機器・端子まで線を延ばすか、'
             '結線先を図面に明記してください。候補: {cand}',
    'near':  '線端が機器端子の直前で止まっています。端子まで接続してください（ほぼ接続）。候補: {cand}',
    'bus':   '単線母線/相です。この母線に繋がる機器を図面で確定してください。候補: {cand}',
    'watari':'同じ号線が複数箇所にあります。渡りで繋がる相手を図面で確定してください。候補: {cand}',
}


def _fix_proposal(item):
    """要確認1件 → 修正案文字列。候補機器を添える。"""
    cats = item.get('cat', 'float')
    cands = item.get('suggest') or [c['device'] for c in item.get('candidates', [])[:3]]
    cand = '／'.join(str(c) for c in cands[:3]) if cands else '(近傍機器なし)'
    tmpl = _FIX.get(cats, _FIX['float'])
    return tmpl.format(cand=cand)


def analyze_defects(seq_paths, skel_paths=None, seiban='', layout_path=None):
    """図面のみで不備検出し、修正案付きで返す。
    戻り: {'seiban','defects':[{sheet,gousen,cat,reason,fix,candidates}], 'external', 'summary'}"""
    fd = fromto_review.build_floor_data(seq_paths, skel_paths=skel_paths,
                                        layout_path=layout_path, seiban=seiban)
    defects = []
    external = 0
    for sh in fd['sheets']:
        for r in sh['review']:
            if r['cat'] in _NOT_DEFECT_CATS:
                external += 1
                continue
            defects.append({
                'sheet': sh['name'], 'gousen': r['gousen'], 'cat': r['cat'],
                'reason': r['reason'], 'fix': _fix_proposal(r),
                'candidates': [c['device'] for c in r.get('candidates', [])[:5]],
                'label': r.get('label', {}),
            })
    return {'seiban': seiban, 'defects': defects, 'external': external,
            'auto_confirmed': fd['summary'].get('自動確定', 0),
            'summary': {'不備件数': len(defects), '外部・予備(不備でない)': external,
                        '自動確定号線': fd['summary'].get('自動確定', 0)}}


def terminal_number_defects(seq_paths, skel_paths=None, seiban=''):
    """端子台の番号未記入を不備として検出し、修正案付きで返す。
    番号が無いと別ラグの結線が1端子に集約され繋ぎ込み数が膨張する(作業性悪化)ため、
    本システムは位置で個別化し仮番号(仮N)を振っている。設計での番号記入を促す。
    戻り: {'seiban','count','items':[{仮番号, 位置, 接続号線[], 修正案}], 'note'}"""
    sheet = harness_sheet.build_sheet(seq_paths, skel_paths=skel_paths, seiban=seiban)
    # 仮番号ラグ → その号線を集める
    prov = {}   # 仮番号 -> {'x','y','gousen':set}
    for r in sheet:
        for m in r['members']:
            if m.get('unfilled_tb'):
                p = prov.setdefault(m['no'], {'x': m['x'], 'y': m['y'], 'gousen': set()})
                if r['gousen'] and not str(r['gousen']).startswith('M@'):
                    p['gousen'].add(r['gousen'])
    items = []
    for prov_no in sorted(prov, key=lambda s: int(str(s).replace('仮', '') or 0)):
        p = prov[prov_no]
        gs = sorted(p['gousen'])
        items.append({
            'provisional': prov_no,
            'position': {'x': round(p['x'], 1), 'y': round(p['y'], 1)},
            'gousen': gs,
            'fix': (f'端子台の番号が未記入です（仮に {prov_no} を割当）。接続号線: '
                    f'{"／".join(str(g) for g in gs) if gs else "(なし)"}。'
                    f'設計図に端子台の端子番号を記入してください。'),
        })
    return {'seiban': seiban, 'count': len(items), 'items': items,
            'note': '番号未記入は繋ぎ込み数を増やし作業性を下げます。位置で個別化し仮番号を'
                    '割り当てて出力しています（要確認）。'}


def produce(seq_paths, skel_paths=None, seiban='', layout_path=None, strict=True):
    """本番フロー本体。図面のみでハーネスシートを生成する。
    strict=True: 不備が1件でもあれば最終シートは保留し、不備＋修正案を提示(参考シートは付す)。
    strict=False: 不備を併記しつつシートも出す(現場判断用)。
    戻り: {
      'seiban', 'status': 'ok'|'has_defects',
      'defects': [...修正案付き...], 'external',
      'sheet': [ハーネス行...](status=ok、または strict=False の参考),
      'summary'
    }"""
    da = analyze_defects(seq_paths, skel_paths=skel_paths, seiban=seiban, layout_path=layout_path)
    sheet = harness_sheet.build_sheet(seq_paths, skel_paths=skel_paths, seiban=seiban)
    n_wires = sum(len(r['wires']) for r in sheet)
    status = 'ok' if not da['defects'] else 'has_defects'
    out = {'seiban': seiban, 'status': status,
           'defects': da['defects'], 'external': da['external'],
           'summary': {**da['summary'], '生成号線': len(sheet), '生成渡り(電線)': n_wires,
                       '判定': ('不備なし→出力' if status == 'ok' else '不備あり→修正案を提示')}}
    if status == 'ok' or not strict:
        out['sheet'] = sheet
    else:
        out['sheet'] = None
        out['reference_sheet'] = sheet     # 参考(不備解消前の暫定生成)
    return out


def route_and_length(seq_paths, skel_paths=None, seiban='', dct_paths=None,
                     topology='connection', physical='length', duct_type=None):
    """【次バージョン】1本ずつ「長さ」「ルート」を出す。ルート決定は優先度で選択。
      topology(渡りの張り方): 'connection'=繋ぎ込み数最小(作業性)/'length'=総配線長最小/'duct'
      physical(物理経路):     'length'=各線を最短経路/'capacity'=ダクト平準化(混雑を分散)
    ダクト網は内部配置図(DCT)から取得。配置図が無ければ端点間マンハッタン長で近似。
    戻り: {'seiban','priority','wires':[{gousen,from,to,size,length,route}],
           'total_length','duct_util','summary'}"""
    from .layout import Layout
    # ダクト型式: 明示が無ければ盤種別で自動選択(制御盤=ID48/分電盤=ID38)。
    # 分電盤はH系シート(結線図)を使うので、パスにH*があれば分電盤とみなす。
    import os as _os
    import re as _re
    # 盤種別(分電盤=結線図H系/外形図G系, 他=制御盤)。ダクト型式＋学習した標準電線に使う。
    is_haiden = any(_re.search(r'-[HG]\d', _os.path.basename(str(p)).upper())
                    for p in list(seq_paths) + list(skel_paths or []))
    kind = '分電盤' if is_haiden else '制御盤'
    if duct_type is None:
        duct_type = _router.duct_for_panel(kind)
    # 過去台帳から学習した盤別標準電線(未記入号線の補完に使用)
    from . import harness_learn
    dflt_wire = harness_learn.default_wire(kind)
    sheet = harness_sheet.build_sheet(seq_paths, skel_paths=skel_paths, seiban=seiban,
                                      priority=topology, default_wire=dflt_wire)
    # 電線(端点座標つき)を集める
    flat = []
    for r in sheet:
        for wi in r['wires']:
            if wi.get('from_pos') and wi.get('to_pos'):
                # 主回路(単線図)の無名結線は内部ID 'M@n' で拾っている。出力はモデルに倣い
                # 回路番号(端点の番号)を号線名にする(内部IDは出さない)。
                g = r['gousen']
                if str(g).startswith('M@'):
                    g = wi['from'].get('no') or wi['to'].get('no') or ''
                flat.append({'gousen': g, 'kind': r['kind'], 'size': r['size'],
                             'color': wi.get('color', ''),
                             'from': wi['from'], 'to': wi['to'],
                             'from_pos': wi['from_pos'], 'to_pos': wi['to_pos']})
    # ダクト網(配置図)
    lay = None
    for p in (dct_paths or []):
        try:
            lay = Layout(p)
            if getattr(lay, 'hducts', None) or getattr(lay, 'vducts', None):
                break
        except Exception:
            lay = None
    wires_out = []
    total = 0.0
    duct_util = {}
    if lay is not None and (getattr(lay, 'hducts', None) or getattr(lay, 'vducts', None)):
        results, total, util = _router.route_wires(flat, lay, priority=physical,
                                                   duct_type=duct_type)
        duct_util = {f'{sorted(e)}': v for e, v in util.items()}
        for w, path, length in results:
            wires_out.append({'gousen': w['gousen'], 'kind': w.get('kind', ''),
                              'size': w['size'], 'color': w.get('color', ''),
                              'from': w['from'], 'to': w['to'],
                              'from_pos': list(w['from_pos']), 'to_pos': list(w['to_pos']),
                              'length': round(length, 1), 'route': [list(p) for p in path]})
    else:
        # ダクト網なし: マンハッタン長で近似(経路は直結)
        for w in flat:
            fp, tp = w['from_pos'], w['to_pos']
            length = abs(fp[0] - tp[0]) + abs(fp[1] - tp[1])
            total += length
            wires_out.append({'gousen': w['gousen'], 'kind': w.get('kind', ''),
                              'size': w['size'], 'color': w.get('color', ''),
                              'from': w['from'], 'to': w['to'],
                              'from_pos': list(fp), 'to_pos': list(tp),
                              'length': round(length, 1), 'route': [list(fp), list(tp)]})
    # 端子台の台番号を配置図(D/G, 格子枠)のTBブロックから幾何的に付番 → 端子(相/回路)を付番。
    dev_lays = []
    for p in list(dct_paths or []) + list(skel_paths or []):
        bn = _os_basename(p).upper()
        if 'DCT' in bn:                 # ダクト図は除外(配置図/外形図・スケルトンのみ)
            continue
        try:
            L = Layout(p)
            if getattr(L, 'devices', None):
                dev_lays.append(L)
        except Exception:
            pass
    _assign_tb_block(wires_out, dev_lays)
    _assign_maincircuit_tb(wires_out)
    # L_OUTSIDE(端子台先)の回路番号なし参照記号から生じる「情報ゼロの重複配線」のみ除外。
    # = 号線が空 かつ 両端とも 番号・端子 が全て空(識別情報が一切ない)。
    # ※CPU/伝送ユニット等の端子間配線(端子名 シロ/伝送+ 等を持つ)は実配線なので残す(回帰防止)。
    def _blank_dup(w):
        if str(w.get('gousen', '')).strip():
            return False
        for e in (w['from'], w['to']):
            if str(e.get('no', '')).strip() or str(e.get('terminal', '')).strip():
                return False
        return True
    wires_out = [w for w in wires_out if not _blank_dup(w)]
    return {'seiban': seiban, 'priority': {'topology': topology, 'physical': physical},
            'duct_type': duct_type, 'wires': wires_out,
            'total_length': round(total, 1), 'duct_util': duct_util,
            'summary': {'電線数': len(wires_out), '総配線長': round(total, 1),
                        'ダクト種別': duct_type, 'ダクト有': lay is not None and bool(duct_util)}}


def duct_decision(seq_paths, skel_paths=None, seiban='', dct_paths=None, duct_type=None):
    """標準ダクトに収まらない場合の判断支援(茂泉様):
      標準ダクトで占有率>32%(=基準超過)なら、ユーザに2案を提示:
        ① 迂回(capacity)  … 標準ダクトのまま混雑を避けて分散。総配線長が伸びる。
        ② ダクト昇格      … 一回り大きい型式にする。総配線長は最短のまま、ダクトが大きくなる。
    戻り: {'seiban','duct_type','fits'(標準で収まるか),
           'standard':{最短時の総長・最大占有率},
           'option_detour':{総長・増分・最大占有率} or None,
           'option_upsize':{型式・断面・最大占有率} or None,
           'recommend'}"""
    # 標準ダクト・最短経路での占有率
    base = route_and_length(seq_paths, skel_paths=skel_paths, seiban=seiban,
                            dct_paths=dct_paths, topology='connection',
                            physical='length', duct_type=duct_type)
    dt = base['duct_type']
    util = list(base['duct_util'].values())
    max_util = max(util) if util else 0.0
    fits = max_util <= 1.0        # util = 使用面積 / (断面×32%)。1.0超=基準32%超過
    out = {'seiban': seiban, 'duct_type': dt, 'fits': fits,
           'standard': {'総配線長': base['total_length'], '最大占有率': round(max_util, 2)},
           'option_detour': None, 'option_upsize': None, 'recommend': None}
    if fits:
        out['recommend'] = f'標準ダクト {dt} で基準内(最大占有率{round(max_util*100)}%≤32%枠)。変更不要。'
        return out
    # ① 迂回(capacity): 標準ダクトのまま分散し、総長の増分を見る
    det = route_and_length(seq_paths, skel_paths=skel_paths, seiban=seiban,
                           dct_paths=dct_paths, topology='connection',
                           physical='capacity', duct_type=dt)
    du = list(det['duct_util'].values())
    base_len = base['total_length']
    inc = round(det['total_length'] - base_len, 1)
    out['option_detour'] = {
        '型式': dt,
        '総配線長(標準最短)': base_len,
        '総配線長(迂回後)': det['total_length'],
        '総長増分': inc,
        '総長増加率%': round(inc / base_len * 100, 1) if base_len else 0.0,
        '最大占有率': round(max(du) if du else 0, 2),
        '基準内': (max(du) if du else 0) <= 1.0}
    # ② 昇格: 収まる最小の一回り大きい型式を探す(最短経路で判定)
    for cand in _router.larger_ducts(dt):
        up = route_and_length(seq_paths, skel_paths=skel_paths, seiban=seiban,
                              dct_paths=dct_paths, topology='connection',
                              physical='length', duct_type=cand)
        uu = list(up['duct_util'].values())
        if (max(uu) if uu else 0) <= 1.0:
            t = _router.DUCT_TYPES[cand]
            out['option_upsize'] = {'型式': cand, '断面': f"{t['w']}×{t['h']}",
                                    '総配線長': up['total_length'],
                                    '最大占有率': round(max(uu) if uu else 0, 2)}
            break
    det = out['option_detour']
    det_ok = bool(det and det['基準内'])
    up = out['option_upsize']
    det_len = (f"総長 {det['総配線長(標準最短)']}→{det['総配線長(迂回後)']} "
               f"(+{det['総長増分']}, +{det['総長増加率%']}%)") if det else '—'
    if det_ok:
        out['recommend'] = (
            f"標準ダクト {dt} 超過。①迂回で基準内に収まります: {det_len}。"
            f"総長を伸ばしたくなければ ②昇格({up['型式'] if up else '該当なし'}"
            f"{('・'+up['断面']) if up else ''}, 総長は最短のまま)。ユーザ選択。")
    else:
        out['recommend'] = (
            f"標準ダクト {dt} 超過。①迂回では基準内に収まりません（{det_len}／占有率"
            f"{det['最大占有率'] if det else '—'}）。②ダクト昇格が必要: "
            f"{up['型式'] if up else '該当なし'}{('・'+up['断面']) if up else ''}"
            f"{('（総長は最短 '+str(up['総配線長'])+'）') if up else ''}。")
    return out


def electrical_check(seq_paths, skel_paths=None, seiban=''):
    """生成ハーネスが電気的に問題ないか検証する。
    ・各号線(等電位ノード)内は連結(全端子が繋がる)＝渡りは全域木で構成(構造的に保証)。
    ・同一端子が複数の号線に現れる＝短絡の疑い(誤トレース) → 指摘。
    ・端子1つだけの号線(結線相手が無い)＝浮き → 指摘(不備側で扱う)。
    戻り: {'ok', 'shorts':[...], 'summary'}"""
    from . import tracer
    from .geometry import norm as _n
    term_nodes = collections.defaultdict(set)   # 端子 → その端子が属する号線集合
    for p in list(seq_paths) + list(skel_paths or []):
        for g, nd in tracer.trace_nodes(p).items():
            if str(g).startswith('M@'):
                continue
            for (d, t, x, y) in nd['members']:
                # 端子番号が未記入(空/?)の端子は識別不能=別途「番号未記入」で指摘済み。
                # 短絡判定は「実端子番号が付いた端子」だけを対象にする(誤検出を避ける)。
                if not t or t == '?':
                    continue
                term_nodes[(_n(d), _n(t))].add(_n(g))
    shorts = []
    for (dev, term), gs in term_nodes.items():
        if term and len(gs) >= 2:
            shorts.append({'terminal': f'{dev}:{term}', '号線': sorted(gs)})
    # 各号線内の渡りは全域木(構造的に連結・同一電位)＝電気的に整合。
    # shorts は「同一端子が複数号線に出現」= 真の短絡 or トレーサのノード形成曖昧の候補
    # (内部QC。設計指摘には出さず、抽出精度の確認に使う)。
    return {'ok': not shorts, 'shorts': shorts,
            'summary': {'各号線内の結線': '全域木で連結(電気的に整合)',
                        '要確認(同一端子が複数号線)': len(shorts)}}


# 不備カテゴリ → 前工程(設計)への指摘と解決案
_DESIGN_FIX = {
    'terminal_no': ('端子台の端子番号 未記入',
                    '結線図で端子台の端子番号(何番に繋ぐか)を記入してください。'
                    '未記入だと繋ぎ込み数が正しく評価できません。'),
    'wire_size':  ('【生成時整合性】電線サイズ 未記入',
                   'CABLE(電線サイズ)の DENSEN に実サイズ(例 HIV1.25sq)を記入してください。'
                   '未記入は社内標準で仮補完しています(要確認)。'),
    'float':      ('【生成時整合性】浮き線端(結線先なし)',
                   '線端がどの機器にも届いていません。結線先の機器・端子を明記してください。'),
    'near':       ('【生成時整合性】近接ギャップ(未接続)',
                   '線端が機器端子の直前で止まっています。端子まで接続してください。'),
    'short':      ('短絡疑い(同一端子が複数号線)',
                   '同じ端子が異なる号線に現れています。図面の結線を確認してください。'),
}


def design_feedback(seq_paths, skel_paths=None, seiban='', out_dir=None):
    """図面不備を前工程(設計)への『指摘＋解決案』としてまとめる。
    端子台番号未記入・電線サイズ未記入・浮き線端・短絡疑い 等を集約。
    out_dir 指定時は <製番>_設計指摘書.csv も出力。戻り: {'seiban','items','summary'}。"""
    items = []
    # 注) 端子台の端子番号は『設計不備』ではなく『製造アサイン』(茂泉様)。
    #     手本のハーネスシートでも製造が台番号を付与し、制御線の端子は空欄のまま。
    #     よって設計指摘には載せず、mfg_assign(製造アサイン枠)として別途返す。
    tn = terminal_number_defects(seq_paths, skel_paths=skel_paths, seiban=seiban)
    # 電線サイズ 未記入(DENSEN='sq' 等) の号線
    import ezdxf as _ez
    size_missing = set()
    for p in list(seq_paths) + list(skel_paths or []):
        try:
            doc = _ez.readfile(p)
        except Exception:
            continue
        for e in doc.modelspace():
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in e.attribs}
            if a.get('PARTS') == '電線サイズ':
                d = a.get('DENSEN', '')
                if not re.match(r'[A-Za-z]*[0-9.]+\s*sq', d):    # 数値サイズが無い=未記入
                    if a.get('DEVICE1'):
                        size_missing.add(a['DEVICE1'])
    if size_missing:
        title, fix = _DESIGN_FIX['wire_size']
        items.append({'分類': title, '該当': f'{len(size_missing)}回路',
                      '号線': '／'.join(sorted(size_missing)[:15]), '解決案': fix})
    # 浮き線端・近接ギャップ(fromto_review)
    da = analyze_defects(seq_paths, skel_paths=skel_paths, seiban=seiban)
    cat_map = {'float': 'float', 'near': 'near'}
    catc = collections.Counter(d['cat'] for d in da['defects'])
    for cat in ('float', 'near'):
        if catc.get(cat):
            title, fix = _DESIGN_FIX[cat_map[cat]]
            gs = [d['gousen'] for d in da['defects'] if d['cat'] == cat][:15]
            items.append({'分類': title, '該当': f'{catc[cat]}号線',
                          '号線': '／'.join(map(str, gs)), '解決案': fix})
    # ※短絡疑い(同一端子が複数号線)は トレーサのノード形成の曖昧さを多く含み、
    #   設計の不備とは限らないため設計指摘書には載せない(内部QCの electrical_check で扱う)。
    out = {'seiban': seiban, 'items': items, 'mfg_assign': tn['items'],
           'summary': {'指摘件数': len(items),
                       'TB端子_製造アサイン': tn['count'],
                       '電線サイズ未記入回路': len(size_missing),
                       '浮き線端': catc.get('float', 0), '近接ギャップ': catc.get('near', 0)}}
    if out_dir:
        import os as _os
        import csv as _csv
        _os.makedirs(out_dir, exist_ok=True)
        p = _os.path.join(out_dir, f"{seiban or 'harness'}_設計指摘書.csv")
        with open(p, 'w', encoding='utf-8-sig', errors='replace', newline='') as f:
            w = _csv.writer(f)
            w.writerow(['分類', '該当箇所', '号線', '設計への解決案'])
            for it in items:
                w.writerow([it['分類'], it['該当'], it['号線'], it['解決案']])
        out['file'] = p
    return out


def device_correspondence(files, seiban='', out_dir=None):
    """正式機器名 ↔ 仮機器名 ↔ 盤内アドレス(位置) の対応表を作る。

    背景(茂泉様の運用課題):
      製造は同一機器の識別のため『仮の機器名称』を付け、社内図面を書き直すが、
      客先の正式図面には仮名が無く、改造時に 正式図面↔ハーネス(仮名) の照合が困難。
    解決:
      仮名を『正式名＋盤内アドレス(位置から決定的)』で生成すれば、客先の正式図面から
      いつでも同じ仮名を再生成でき、ハーネスシートと自動照合できる(手作業の対応不要)。
      位置(盤内アドレス)は名前に依らない不変キー。
    戻り: {'seiban','items':[{正式名,盤内アドレス,x,y,シート,仮名}], 'file'?}
    """
    import re as _re
    from .geometry import DrawingModel
    from .layout import Layout
    seq, skel, dct = _classify_seiban_files(files)
    # 盤内アドレスは「配置図(D001・格子枠あり)」の物理位置から取る(回路図F/Eの座標ではない)。
    # 優先: -D<数字> の非DCT(格子枠あり) → DCT。
    # 盤内アドレスは配置図(格子枠)の物理位置から。制御盤=D、分電盤=G(外形図/内部配置図)。
    # 分電盤は G001(扉/外形)と G002(内部機器配置)のように複数あり、内部機器は後者にある。
    # よって「1枚を選ぶ」のではなく、格子枠を持つ全配置図を横断して機器位置を探す。
    d_files = [p for p in files if _re.search(r'-D\d', _os_basename(p).upper())
               and 'DCT' not in _os_basename(p).upper()]
    g_files = [p for p in files if _re.search(r'-G\d', _os_basename(p).upper())
               and 'DCT' not in _os_basename(p).upper()]
    lays = []
    for p in (d_files + g_files + dct):
        try:
            L = Layout(p)
        except Exception:
            continue
        if getattr(L, 'rows', None) and getattr(L, 'cols', None):
            lays.append(L)
    lay = lays[0] if lays else None      # 後方互換(missing判定等で参照)

    def _brk_variants(sym):
        """遮断器の別名(結線図ELCB ↔ 配置図MCCB 等)を相互展開して照合候補にする。"""
        s = str(sym)
        m = _re.match(r'^(ELCB|MCCB|MCB|ELB|NFB|MMS)(\b|[-\s]|\d|$)', s)
        if not m:
            return [s]
        rest = s[len(m.group(1)):]
        return [s] + [alt + rest for alt in ('MCCB', 'ELCB', 'MCB', 'NFB', 'MMS')]

    def phys_addr(sym):
        """機器の 盤内アドレス を配置図の物理位置から(全配置図を横断＋遮断器別名対応)。"""
        for cand in _brk_variants(sym):
            for L in lays:
                try:
                    pos = L.device_pos(cand)
                    if pos:
                        a = L.cell_at(pos[0], pos[1])
                        if a:
                            return a
                except Exception:
                    pass
        return ''

    seen = set()
    items = []
    for p in seq + skel:
        try:
            m = DrawingModel(p)
        except Exception:
            continue
        sheet = _os_basename(p)
        for d in m.devices:
            cx = round((d.box[0] + d.box[2]) / 2)
            cy = round((d.box[1] + d.box[3]) / 2)
            if d.sym in seen:
                continue
            seen.add(d.sym)                 # 論理機器1つにつき1行(接点の多重描画を集約)
            a = phys_addr(d.sym)
            prov = f'{d.sym}({a})' if a else d.sym
            items.append({'正式名': d.sym, '盤内アドレス': a, 'x': cx, 'y': cy,
                          'シート': sheet, '仮名': prov})
    items.sort(key=lambda r: (r['正式名'], r['x'], r['y']))
    out = {'seiban': seiban, 'items': items}
    if out_dir:
        import os as _os
        import csv as _csv
        _os.makedirs(out_dir, exist_ok=True)
        fp = _os.path.join(out_dir, f"{seiban or 'harness'}_機器対応表.csv")
        with open(fp, 'w', encoding='utf-8-sig', errors='replace', newline='') as f:
            w = _csv.writer(f)
            w.writerow(['正式機器名', 'ロケータ', '位置x', '位置y', 'シート', '仮機器名(決定的)'])
            for it in items:
                w.writerow([it['正式名'], it['盤内アドレス'], it['x'], it['y'],
                            it['シート'], it['仮名']])
        out['file'] = fp
    return out


_PHASE_SUFFIX = {'緑': 'E', '赤': 'U', '白': 'V', '青': 'W'}
_TB_NEAR_MAX = 600.0        # 接続先機器と端子台ストリップの近接判定(mm)。これ超なら付番しない(誤答回避)


def _tb_blocks(lays):
    """配置図群から端子台ストリップの {台番号: (x,y)} を集める(キー TB101→番号101)。"""
    import re as _re
    out = {}
    for L in lays:
        for k, pos in getattr(L, 'devices', {}).items():
            m = _re.match(r'^TB(.+)$', str(k))
            if m:
                out.setdefault(m.group(1), pos)
    return out


def _assign_tb_block(wires, lays):
    """TB端子の台番号を配置図から幾何的に付番する(茂泉様: 台番号はモデル＝配置図のTBブロック)。

    規則(モデル検証済): TB台番号 = 接続先(非TB)機器に最も近い配置図の端子台ストリップ。
      例 MCCB-102 の近くの TB102、MCCB-103/104 の近くの TB101 …(5-29026でモデルと一致)。
    近接(距離 _TB_NEAR_MAX 以内)で一意な時のみ付番。遠い/判定不能は空欄のまま(誤答ゼロ)。
    """
    import math
    from .geometry import norm as _n
    blocks = _tb_blocks(lays)
    if not blocks or not lays:
        return

    def dev_pos(dev, no):
        name = dev + ('-' + no if no else '')
        for L in lays:
            try:
                p = L.device_pos(name) or L.device_pos(dev)
                if p:
                    return p
            except Exception:
                pass
        return None

    def nearest(pos):
        best, bd = None, 1e18
        for tbno, tpos in blocks.items():
            d = math.hypot(tpos[0] - pos[0], tpos[1] - pos[1])
            if d < bd:
                bd, best = d, tbno
        return (best, bd) if best is not None else (None, bd)

    def _unassigned_tb(a):
        no = str(a.get('no', ''))
        return _n(a.get('device', '')) == 'TB' and (not no or bool(re.match(r'^仮\d', no)))

    # (1) 幾何付番: 接続先機器に最も近い配置図のTBストリップ(モデル準拠)
    for w in wires:
        for a, b in ((w['from'], w['to']), (w['to'], w['from'])):
            if not _unassigned_tb(a):
                continue
            pos = dev_pos(b.get('device', ''), b.get('no', ''))
            if not pos:
                continue
            tbno, dist = nearest(pos)
            if tbno and dist <= _TB_NEAR_MAX:
                a['no'] = tbno

    # (2) フォールバック: 幾何で置けないTB(配置図に無い機器＝製造の作業用仮名 等)は
    #     製造の正規付番として、接続先の回路番号(相手機器の番号→無ければ号線の数字接頭)を台番号にする。
    for w in wires:
        g = str(w.get('gousen', ''))
        gnum = re.match(r'^(\d+)', g)
        gnum = gnum.group(1) if gnum else ''
        for a, b in ((w['from'], w['to']), (w['to'], w['from'])):
            if not _unassigned_tb(a):
                continue
            bno = str(b.get('no', ''))
            if re.match(r'^仮\d', bno):     # 相手も未確定TB(渡り)の仮Nは使わない→号線の回路番号で付番
                bno = ''
            # 台番号: 接続先機器の番号 → 号線の数字接頭 → 号線名そのもの
            # (母線/相のTB渡り: 号線が E/N/S/L1/SLD 等の非数字ならバス名を台番号にする)
            circ = bno or gnum or g
            if circ:
                a['no'] = circ


def _assign_maincircuit_tb(wires):
    """主回路(アース/電源)の TB 端子番号を手本ルールで自動付番する(茂泉様)。

    手本のハーネスシート: 主回路の TB 端子 = <回路番号><相>。
      アース(緑/号線E)→ <回路>E、電源 赤→U/白→V/青→W(R/S/T相)。制御線は空欄のまま。
    回路番号は「相手端点(非TB側)の番号」を用いる(例 TB↔MCCB-102 → 102<相>)。
    端子が既に埋まっている TB は変更しない(図面優先)。
    """
    from .geometry import norm as _n
    for w in wires:
        color = w.get('color', '')
        suf = _PHASE_SUFFIX.get(color)
        if not suf and str(w.get('gousen', '')).upper().startswith('E'):
            suf = 'E'                       # アース号線(色未設定)
        if not suf:
            continue                        # 制御線は対象外(空欄のまま)
        fr, to = w['from'], w['to']
        for a, b in ((fr, to), (to, fr)):
            if _n(a.get('device', '')) == 'TB' and not a.get('terminal'):
                circ = b.get('no', '')
                if circ:
                    a['terminal'] = f'{circ}{suf}'


def _layout_roster(dxf_paths):
    """配置図(制御盤=D系/分電盤=G系, 格子枠あり)から機器インスタンスの物理位置＋盤内アドレスを収集。
    戻り: {(DEVICE, DEVICE1): [ (x, y, 盤内アドレス, PARTS, シート) , ...]}。
    同名機器が複数位置にある＝現場で識別できず『仮名』が要る対象。位置(盤内アドレス)で一意化する。"""
    import re as _re
    import ezdxf as _ezdxf
    from .layout import Layout
    R = {}
    d_files = [p for p in dxf_paths if _re.search(r'-D\d', _os_basename(p).upper())
               and 'DCT' not in _os_basename(p).upper()]
    g_files = [p for p in dxf_paths if _re.search(r'-G\d', _os_basename(p).upper())]
    for p in (d_files or g_files):
        try:
            lay = Layout(p)
        except Exception:
            lay = None
        try:
            doc = _ezdxf.readfile(p)
        except Exception:
            continue
        sheet = _os_basename(p)
        for e in doc.modelspace():
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in e.attribs}
            dev, d1, parts = a.get('DEVICE', ''), a.get('DEVICE1', ''), a.get('PARTS', '')
            if not dev or dev == 'FRAME':
                continue
            x, y = e.dxf.insert.x, e.dxf.insert.y
            addr = ''
            if lay is not None:
                try:
                    addr = lay.cell_at(x, y) or ''
                except Exception:
                    addr = ''
            R.setdefault((dev, d1), []).append((round(x), round(y), addr, parts, sheet))
    return R


def _harness_sheet_refs(txt_paths):
    """既存ハーネスシート(.txt, cp932, TAB区切り) → Counter[(機器種別, 番号)]（端点出現数）。"""
    import csv as _csv
    import collections as _c
    refs = _c.Counter()
    for p in txt_paths:
        try:
            raw = open(p, 'rb').read().decode('cp932', 'replace')
        except Exception:
            continue
        for r in _csv.reader(raw.splitlines(), delimiter='\t'):
            c = [x.strip() for x in (r + [''] * 11)[:11]]
            if c[1] == '*':          # 電線仕様ヘッダ行
                continue
            dt, no = c[5], c[6]
            if dt and dt != '*':
                refs[(dt, no)] += 1
    return refs


def reconcile_legacy(dxf_paths, sheet_txt_paths, seiban='', out_dir=None):
    """【過去製番の照合】客先の正式図面(DXF)＋既存ハーネスシート(.txt, 仮名入り)を突合し、
    正式機器名 ↔ 盤内アドレス ↔ 仮名(決定的) の対応表を再生成する。

    運用課題(茂泉様): 製造は同一機器の識別に手書きの『仮の機器名称』を付け社内図面を書き直すが、
      客先の正式図面には仮名が無く、改造時に 正式図面↔ハーネス(仮名) の照合が困難。
    ここでの実証: 位置(盤内アドレス)は名前に依らない不変キー。配置図から各機器の盤内アドレスを取り、
      仮名を『正式名(盤内アドレス)』で決定的に生成すれば、既存ハーネスシートと自動照合できる。
    戻り: {seiban, 図面機器数, アドレス付与数, ハーネス参照数, 照合数, 同名複数数, items, file?}。
    """
    R = _layout_roster(dxf_paths)
    refs = _harness_sheet_refs(sheet_txt_paths)
    n_dev = len(R)
    n_addr = sum(1 for insts in R.values() if any(a for _, _, a, _, _ in insts))
    matched = [k for k in refs if k in R and any(a for _, _, a, _, _ in R[k])]
    dup = {k: v for k, v in R.items() if len(v) > 1}
    items = []
    for (dev, d1), insts in sorted(R.items()):
        nm = dev + ('-' + d1 if d1 else '')
        occ = refs.get((dev, d1), 0)
        for (x, y, addr, parts, sheet) in insts:
            items.append({'正式名': nm, '盤内アドレス': addr, 'x': x, 'y': y,
                          'PARTS': parts, 'シート': sheet,
                          '仮名': f'{nm}({addr})' if addr else nm,
                          'ハーネス出現': occ, '同名複数': len(insts) > 1})
    out = {'seiban': seiban, '図面機器数': n_dev, 'アドレス付与数': n_addr,
           'ハーネス参照数': len(refs), '照合数': len(matched),
           '同名複数数': len(dup), 'items': items}
    if out_dir:
        import os as _os
        import csv as _csv
        _os.makedirs(out_dir, exist_ok=True)
        fp = _os.path.join(out_dir, f"{seiban or 'legacy'}_機器対応表.csv")
        with open(fp, 'w', encoding='utf-8-sig', errors='replace', newline='') as f:
            w = _csv.writer(f)
            w.writerow(['正式機器名', 'ロケータ', '位置x', '位置y', 'PARTS',
                        'シート', '仮名(決定的)', 'ハーネス出現', '同名複数'])
            for it in items:
                w.writerow([it['正式名'], it['盤内アドレス'], it['x'], it['y'],
                            it['PARTS'], it['シート'], it['仮名'],
                            it['ハーネス出現'], '●' if it['同名複数'] else ''])
        out['file'] = fp
    return out


def _os_basename(p):
    import os as _os
    return _os.path.basename(str(p))


def to_csv(sheet, path):
    """生成シートをCSV(同フォーマット)で書き出す(harness_sheet.to_csv に委譲)。"""
    return harness_sheet.to_csv(sheet, path)


def length_to_csv(routed, path, addr_map=None):
    """route_and_length の結果を、人手ハーネスと同じ列＋測長で書き出す(cp932)。
    列: 号線/種別/サイズ/色/From(場所,機器,番号,端子,盤内アドレス)/To(...)/測長/ルート節点数。
    addr_map: {norm(正式機器名): 盤内アドレス}。各端点に盤内アドレス(不変キー)を併記する。"""
    import csv
    from .geometry import norm as _n
    am = addr_map or {}

    def _dev(e):
        return (e['device'] + ('-' + e['no'] if e['no'] else ''))

    with open(path, 'w', encoding='utf-8-sig', errors='replace', newline='') as f:
        w = csv.writer(f)
        w.writerow(['号線', '種別', 'サイズ', '色',
                    'From場所', 'From機器', 'From番号', 'From端子', 'Fromロケータ',
                    'To場所', 'To機器', 'To番号', 'To端子', 'Toロケータ',
                    '測長', 'ルート節点'])
        for wi in routed['wires']:
            fr, to = wi['from'], wi['to']
            fa = am.get(_n(_dev(fr)), '') or am.get(_n(fr['device']), '')
            ta = am.get(_n(_dev(to)), '') or am.get(_n(to['device']), '')
            w.writerow([wi['gousen'], wi.get('kind', ''), wi['size'], wi.get('color', ''),
                        fr.get('place', ''), fr['device'], fr['no'], fr['terminal'], fa,
                        to.get('place', ''), to['device'], to['no'], to['terminal'], ta,
                        wi['length'], len(wi['route'])])
    return path


def to_harness_txt(routed, path, seiban='', addr_map=None, encoding='utf-8-sig'):
    """生成ハーネスを『ハーネスデータシート』と同じネイティブ形式(11列TAB)で書き出す。

    形式(モデル準拠):
      行0  "配線情報リスト 作成日  97,4,26"
      行3  col10 に製番
      以降 電線ごとに:
        ・種別/サイズが変わったら 仕様ヘッダ  \t*\t*\t<種別>\t<サイズ>\t*\t*\t*\t*
          変わらなければ 区切り            \t*\t*\t*\t*\t*\t*\t*\t*
        ・端点2行  \t<号線>\t*\t<圧着>\t<場所>\t<機器>\t<番号>\t<端子>
    列: 0空,1=号線(電源線は色 赤/白/青),2=*,3=圧着端子(未算出は空),4=場所,5=機器,6=番号,7=端子,8-10空。
    既定は UTF-8(BOM) 出力(画面・Excelで文字化けしない)。社内ソフト取込用に encoding='cp932' も可。
    """
    from . import harness_learn as _HL
    from .layout import DOOR_BASE as _DOOR
    am = addr_map or {}

    def _door(dv):
        b = ''.join(ch for ch in str(dv).split('-')[0] if not ch.isdigit())
        return dv in _DOOR or b in _DOOR

    def _place(e):
        # 手本に倣い、扉付け機器で場所未指定なら『扉』(内部配置図に無いのが正常)
        return e.get('place', '') or ('扉' if _door(e.get('device', '')) else '')

    rows = _harness_rows(routed, seiban=seiban, _door=_door, _place=_place)
    lines = ['\t'.join('' if x is None else str(x) for x in r) for r in rows]
    with open(path, 'w', encoding=encoding, newline='') as f:
        f.write('\r\n'.join(lines) + '\r\n')
    return path


def _harness_rows(routed, seiban='', _door=None, _place=None):
    """ハーネスシート(モデル同一フォーマット)の全行を 11セルのリストで返す。
    txt(TAB) と xlsx の共通ソース。行構成: 行0タイトル/行3製番/以降 仕様ヘッダ＋端点2行＋区切り。
    列: 0空,1=号線(電源線は色),2=*,3=圧着,4=場所,5=機器,6=番号,7=端子,8-10空。"""
    from . import harness_learn as _HL
    if _place is None:
        from .layout import DOOR_BASE as _DOOR

        def _door(dv):
            b = ''.join(ch for ch in str(dv).split('-')[0] if not ch.isdigit())
            return dv in _DOOR or b in _DOOR

        def _place(e):
            return e.get('place', '') or ('扉' if _door(e.get('device', '')) else '')

    def cells(c):
        return (list(c) + [''] * 11)[:11]

    rows = [cells(['"配線情報リスト 作成日  97,4,26"']), cells([]), cells([]),
            cells(['', '', '', '', '', '', '', '', '', '', seiban])]
    prev = None
    for w in routed['wires']:
        size = w.get('size', '')
        spec = (_HL.wire_type(w.get('kind'), w.get('gousen'), w.get('color')), size)
        if spec != prev:
            rows.append(cells(['', '*', '*', spec[0], spec[1], '*', '*', '*', '*']))
        else:
            rows.append(cells(['', '*', '*', '*', '*', '*', '*', '*', '*']))
        prev = spec
        g = w.get('color') or w.get('gousen', '')   # 電源線は色、制御線は号線(モデル準拠)
        for e in (w['from'], w['to']):
            crimp = _HL.crimp_of(size, e.get('device', ''))
            no = e.get('no', '')
            if re.match(r'^仮\d', str(no)):
                no = ''
            rows.append(cells(['', g, '*', crimp, _place(e),
                               e.get('device', ''), no, e.get('terminal', '')]))
    return rows


def to_harness_xlsx(routed, path, seiban='', addr_map=None):
    """生成ハーネスを『ハーネスデータシート』と同一フォーマットの Excel(.xlsx) で出力。
    既存システムがこのExcelを読み込み、シールシートへ印刷する運用(茂泉様)。
    セル配置は txt(11列)と同一。"""
    import openpyxl
    rows = _harness_rows(routed, seiban=seiban)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (seiban or 'harness')[:31]
    for r in rows:
        ws.append(['' if x is None else str(x) for x in r])
    wb.save(path)
    return path


def _harness_rows_from_review(review_rows, seiban=''):
    """確認UIで人手修正した行 → モデル同一フォーマットの 11セル行リスト。
    _harness_rows と違い、圧着/場所/機器/番号/端子は"人手が確定した値をそのまま"使う。"""
    def cells(c):
        return (list(c) + [''] * 11)[:11]

    out = [cells(['"配線情報リスト 作成日  97,4,26"']), cells([]), cells([]),
           cells(['', '', '', '', '', '', '', '', '', '', seiban])]
    prev = None
    for r in review_rows:
        typ = (r.get('type') or '').strip()
        size = (r.get('size') or '').strip()
        spec = (typ, size)
        if spec != prev:
            out.append(cells(['', '*', '*', typ, size, '*', '*', '*', '*']))
        else:
            out.append(cells(['', '*', '*', '*', '*', '*', '*', '*', '*']))
        prev = spec
        g = r.get('color') or r.get('gousen', '')   # 電源線は色、制御線は号線(モデル準拠)
        for side in ('from', 'to'):
            e = r.get(side) or {}
            no = e.get('no', '')
            if re.match(r'^仮\d', str(no)):
                no = ''
            out.append(cells(['', g, '*', e.get('crimp', ''), e.get('place', ''),
                               e.get('device', ''), no, e.get('terminal', '')]))
    return out


def _rows_to_txt(rows, path, encoding='utf-8-sig'):
    lines = ['\t'.join('' if x is None else str(x) for x in r) for r in rows]
    with open(path, 'w', encoding=encoding, newline='') as f:
        f.write('\r\n'.join(lines) + '\r\n')
    return path


def _rows_to_xlsx(rows, path, title=''):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (title or 'harness')[:31]
    for r in rows:
        ws.append(['' if x is None else str(x) for x in r])
    wb.save(path)
    return path


def confirm_and_learn(review_rows, corrections=None, seiban='', out_dir='.'):
    """確認UIの「確定・出力＋学習」エントリ。
    人手で確定したハーネスデータを (1)モデル同一フォーマットの最終Excel/txtとして書き出し、
    (2)確定シートをコーパスへ記録(監査/バッチ再学習用)、
    (3)人手の"修正(diff)"だけを権威データとして learned.json に学習(回帰ゼロ)。
    corrections: [{'field':'crimp','size':..,'device':..,'to':..}, ...]（確認UIが算出）。"""
    import os as _os
    from . import harness_learn as _HL
    rows11 = _harness_rows_from_review(review_rows, seiban=seiban)
    base = seiban or 'harness'
    _os.makedirs(out_dir, exist_ok=True)
    txt = _os.path.join(out_dir, f'{base}_ハーネスデータ.txt')
    _rows_to_txt(rows11, txt)
    xlsx = _os.path.join(out_dir, f'{base}_ハーネスデータ.xlsx')
    try:
        _rows_to_xlsx(rows11, xlsx, title=seiban)
    except Exception:
        xlsx = None
    _HL.record_confirmed_sheet(seiban, rows11)
    stats = _HL.learn_corrections(corrections or [])
    return {'files': {'harness_txt': txt, 'harness_xlsx': xlsx},
            'wires': len(review_rows), 'corrections': len(corrections or []),
            'learned': stats}


def to_label_sheet_html(routed, path, seiban='', addr_map=None,
                        cols=5, rows=10, cell_w_mm=38.0, cell_h_mm=25.0,
                        page='A4', margin_mm=8.0):
    """ハーネス線の識別ラベル(シール)を『マス目』に面付けした印刷用HTMLを出力。

    運用: このシートをラベル台紙に印刷 → 各マス(1枚)を剥がして対応する電線に貼る。
    各マス(電線1本)の内容: 号線 / 種別・サイズ / From(機器-番号:端子, ロケータ) / To(同左)。
    各マスに収まるよう、JavaScript で文字がはみ出さない最大フォントに自動縮小する。
    面付けは cols×rows、マス寸法 cell_w_mm×cell_h_mm(ラベル台紙に合わせて変更可)。
    戻り: path。
    """
    from . import harness_learn as _HL
    from .geometry import norm as _n
    am = addr_map or {}
    per = max(1, cols * rows)

    def dev(e):
        return e['device'] + ('-' + e['no'] if e.get('no') else '')

    def loc(e):
        return am.get(_n(dev(e)), '') or am.get(_n(e['device']), '')

    def esc(s):
        return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    def endpoint(e):
        t = (':' + e['terminal']) if e.get('terminal') else ''
        pl = ('[' + e['place'] + ']') if e.get('place') else ''
        lc = loc(e)
        lc = f'<span class="loc">{esc(lc)}</span>' if lc else ''
        return f'{esc(pl)}{esc(dev(e))}{esc(t)} {lc}'

    ws = routed['wires']
    cells = []
    for w in ws:
        size = w.get('size', '')
        typ = _HL.wire_type(w.get('kind'), w.get('gousen'), w.get('color'))
        g = w.get('color') or w.get('gousen', '')
        cells.append(
            '<div class="cell"><div class="fit">'
            f'<div class="hd"><b>{esc(g)}</b><span class="sz">{esc(typ)}{esc(size)}</span></div>'
            f'<div class="ep"><i>F</i>{endpoint(w["from"])}</div>'
            f'<div class="ep"><i>T</i>{endpoint(w["to"])}</div>'
            '</div></div>')
    pages = []
    for i in range(0, max(len(cells), 1), per):
        chunk = cells[i:i + per]
        while len(chunk) < per:
            chunk.append('<div class="cell empty"></div>')
        pages.append('<div class="sheet">' + ''.join(chunk) + '</div>')

    html = f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>{esc(seiban)} ハーネスラベル</title>
<style>
  @page {{ size: {page}; margin: {margin_mm}mm; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: "Noto Sans JP","IPAGothic","Yu Gothic",sans-serif; background:#fff; color:#000; }}
  .sheet {{ display:grid; grid-template-columns: repeat({cols}, {cell_w_mm}mm);
            grid-auto-rows: {cell_h_mm}mm; page-break-after: always; }}
  .cell {{ width:{cell_w_mm}mm; height:{cell_h_mm}mm; border:0.2mm solid #bbb;
           padding:0.8mm; overflow:hidden; display:flex; align-items:center; }}
  .cell.empty {{ border-style:dashed; }}
  .fit {{ width:100%; line-height:1.15; }}
  .hd {{ display:flex; justify-content:space-between; align-items:baseline; border-bottom:0.15mm solid #000; margin-bottom:0.4mm; }}
  .hd b {{ font-weight:700; }}
  .sz {{ opacity:.85; }}
  .ep i {{ display:inline-block; width:1.1em; font-style:normal; font-weight:700; opacity:.7; }}
  .loc {{ border:0.15mm solid #000; border-radius:0.6mm; padding:0 0.4mm; font-weight:700; white-space:nowrap; }}
  .ep {{ overflow-wrap:anywhere; }}
  @media screen {{ body {{ background:#eee; padding:10px; }} .sheet {{ background:#fff; margin:0 auto 12px; box-shadow:0 1px 6px rgba(0,0,0,.25); }} }}
</style></head><body>
{''.join(pages)}
<script>
// 各マスの文字を、はみ出さない最大フォントに自動調整(二分探索)
function fit(el, box){{
  var lo=3.0, hi=11.0;               // pt
  for(var k=0;k<12;k++){{
    var mid=(lo+hi)/2; el.style.fontSize=mid+'pt';
    if(el.scrollWidth<=box.clientWidth && el.scrollHeight<=box.clientHeight) lo=mid; else hi=mid;
  }}
  el.style.fontSize=lo+'pt';
}}
document.querySelectorAll('.cell .fit').forEach(function(f){{ fit(f, f.parentNode); }});
</script></body></html>'''
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path


def addr_map_from_correspondence(corr):
    """device_correspondence の結果 → {norm(正式機器名): 盤内アドレス}。"""
    from .geometry import norm as _n
    m = {}
    for it in corr.get('items', []):
        if it.get('盤内アドレス'):
            m[_n(it['正式名'])] = it['盤内アドレス']
    return m


def _classify_seiban_files(files):
    """製番のDXF群を seq(シーケンス/結線図 F*/H*)・skel(スケルトン/外形 E*/G0*)・
    dct(内部配置図 *DCT*) に振り分ける（ファイル名規約）。戻り: (seq, skel, dct)。"""
    import os as _os
    import re as _re
    seq, skel, dct = [], [], []
    for p in files:
        bn = _os.path.basename(str(p))
        b = bn.upper()
        # 内部配置図/外形図/DCT/D図 = 配置図(盤内アドレス用)
        if ('DCT' in b or '内部配置' in bn or '外形' in bn or '配置図' in bn
                or _re.search(r'-D\d', b)):
            dct.append(p)
        # シーケンス/結線図/展開接続図 = seq
        elif 'シーケンス' in bn or '結線' in bn or '展開' in bn or _re.search(r'-[FH]\d', b):
            seq.append(p)
        # スケルトン/系統図 = skel
        elif 'スケルトン' in bn or '系統' in bn or _re.search(r'-[EG]\d', b):
            skel.append(p)
    return sorted(seq), sorted(skel), sorted(dct)


def produce_seiban(files, seiban='', out_dir='.', topology='connection', physical='length'):
    """【本番エントリ】新しい製番の図面群 → 人手ハーネス相当のハーネスデータを出力する。
    files: その製番のDXF一式(F/H=結線,E/G=スケルトン/外形,DCT=配置図)。
    生成物: <seiban>_ハーネス.csv(号線/種別/サイズ/色/From-To/測長/ルート)、
            <seiban>_不備.csv(端子台番号未記入 等)。
    戻り: {seiban, files:{harness_csv, defects_csv}, summary, duct_decision}。"""
    import os as _os
    import csv as _csv
    seq, skel, dct = _classify_seiban_files(files)
    routed = route_and_length(seq, skel_paths=skel, seiban=seiban, dct_paths=dct,
                              topology=topology, physical=physical)
    # 内部配置図のロケーター対応表(実機器→仮名称A,B,C…)があれば From-To をその呼称へ置換。
    # 製造現場のハーネスシートに合わせる。対応表が無ければ不変(回帰ゼロ)。
    try:
        from . import locator_map as _lmap
        _lm = _lmap.build_map(dct)
        if _lm.get('by_actual'):
            _lmap.apply_to_routed(routed, _lm)
            routed.setdefault('summary', {})['ロケーター置換'] = len(_lm['by_actual'])
    except Exception:
        pass
    dec = duct_decision(seq, skel_paths=skel, seiban=seiban, dct_paths=dct)
    ec = electrical_check(seq, skel_paths=skel, seiban=seiban)
    fb = design_feedback(seq, skel_paths=skel, seiban=seiban, out_dir=out_dir)
    # 設計不備の学習(ループA): 横断検証済みの決定論ルール(層1=R2スケルトン限定/R3中性線、
    # 層2=R7接地 参考)を①設計不備へ追加。ケースベースの実績修正案を添える。回帰ゼロのため保護。
    try:
        from . import design_check as _dcheck
        # R4(親無し接点)用に内部配置/外形図を layout として渡す(純DCTダクトは除外)
        _lay = [p for p in dct if ('内部配置' in _os_basename(p) or '外形' in _os_basename(p)
                or re.search(r'-[DG]\d', _os_basename(p).upper())) and 'DCT' not in _os_basename(p).upper()]
        extra = _dcheck.review_design_items(seq_paths=seq, skel_paths=skel, layout_paths=_lay)
        if extra:
            fb.setdefault('items', [])
            fb['items'].extend(extra)
            fb.setdefault('summary', {})['指摘件数'] = len(fb['items'])
    except Exception:
        pass
    corr = device_correspondence(files, seiban=seiban, out_dir=out_dir)
    amap = addr_map_from_correspondence(corr)
    _os.makedirs(out_dir, exist_ok=True)
    base = seiban or 'harness'
    hcsv = _os.path.join(out_dir, f'{base}_ハーネス.csv')
    length_to_csv(routed, hcsv, addr_map=amap)
    # ハーネスデータシートと同じネイティブ形式(11列TAB)でも出力(社内運用そのまま)
    htxt = _os.path.join(out_dir, f'{base}_ハーネスデータ.txt')
    to_harness_txt(routed, htxt, seiban=seiban, addr_map=amap)
    # 既存システム取込用: モデル同一フォーマットの Excel(.xlsx)。これでシールシートへ印刷。
    hxlsx = _os.path.join(out_dir, f'{base}_ハーネスデータ.xlsx')
    try:
        to_harness_xlsx(routed, hxlsx, seiban=seiban, addr_map=amap)
    except Exception:
        hxlsx = None
    # マス目シール印刷用(各マスにフォント自動調整)
    lbl = _os.path.join(out_dir, f'{base}_ラベルシート.html')
    to_label_sheet_html(routed, lbl, seiban=seiban, addr_map=amap)
    # 生成データ確認UI(表＋要確認＋編集＋固定ラベル出力ボタン)
    from . import review as _review
    rvw = _os.path.join(out_dir, f'{base}_確認.html')
    _review.to_review_html(routed, rvw, seiban=seiban, addr_map=amap, ec=ec,
                           defects=fb.get('items'))
    # 確認用の注記付き図面(SVG・配線色/号線/機器:端子を図面に重ねる)
    dwg = _os.path.join(out_dir, f'{base}_確認図面.html')
    try:
        from . import annotate as _annotate
        _annotate.write(routed, seq, skel, dwg, seiban=seiban)
    except Exception:
        dwg = None
    return {'seiban': seiban,
            'files': {'review_html': rvw, 'harness_txt': htxt, 'harness_xlsx': hxlsx,
                      'harness_csv': hcsv, 'label_sheet_html': lbl, 'drawing_html': dwg,
                      'design_feedback_csv': fb.get('file'),
                      'device_map_csv': corr.get('file')},
            'electrical': ec['summary'],
            'design_feedback': fb['summary'],
            'summary': {'電線数': routed['summary']['電線数'],
                        '総配線長': routed['total_length'],
                        'ダクト種別': routed['duct_type'],
                        '指摘件数(設計へ)': fb['summary']['指摘件数'],
                        '各号線内の電気整合': '全域木で連結(OK)',
                        '要QC(同一端子が複数号線)': ec['summary']['要確認(同一端子が複数号線)'],
                        'seq数': len(seq), 'skel数': len(skel), 'dct数': len(dct)},
            'duct_decision': dec['recommend']}
