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
from . import harness_sheet, fromto_review


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


def to_csv(sheet, path):
    """生成シートをCSV(同フォーマット)で書き出す(harness_sheet.to_csv に委譲)。"""
    return harness_sheet.to_csv(sheet, path)
