# -*- coding: utf-8 -*-
"""製造アドオン（フェーズB: Python＋Web UI）の From-To 確認・解決バックエンド。

方針（茂泉様）:
  幾何逆算だけでは From-To が頭打ち（~53%）。製造現場で「自動抽出＋作業者の対話確認」
  で残りを確定し 100%・誤配線ゼロにする。まずB(Python＋Web UI)で仕様を固め、確定後に
  A(BricsCADアドオン)へ移す。本モジュールはBのバックエンド。

生成物:
  build_review(...) → {
     'seiban', 'confirmed': [FromTo...],        # 自動で確定した結線（2機器以上）
     'review':   [ReviewItem...],               # 未確定（作業者が確認/接続する号線）
  }
  ReviewItem = {gousen, current(現在の端点), candidates(近い機器の候補), label(座標)}
  apply_resolutions(review, decisions) → 確定 From-To 一覧（＝Aアドオンが書き出す仕様）

FromTo(1号線) = {gousen, kind, endpoints:[{device,terminal,source}], size}
候補は「線端に近い機器（距離順）」＝現場が距離で判断していた作業をUI化するための提示。
"""
import math
from .geometry import DrawingModel, norm
from . import harness, logical_check as lc


def _fromto(gousen, net):
    eps = []
    for t in net.get('terminals', []):
        dev = t.device
        if '-' in dev:
            d, n = dev.rsplit('-', 1)
        else:
            d, n = dev, ''
        eps.append({'device': d, 'no': n, 'terminal': (t.name if t.name != '?' else ''),
                    'x': round(t.x, 1), 'y': round(t.y, 1), 'source': 'auto'})
    # 端子の無い機器（外形枠のみ）も端点として
    term_devs = {t.device for t in net.get('terminals', [])}
    for d in net.get('devices', []):
        if d not in term_devs and d != 'TB':
            if '-' in d:
                dd, nn = d.rsplit('-', 1)
            else:
                dd, nn = d, ''
            eps.append({'device': dd, 'no': nn, 'terminal': '', 'source': 'auto'})
    return {'gousen': gousen, 'kind': net.get('kind', 'ctrl'),
            'size': net.get('wire_size', ''), 'endpoints': eps}


def _nearest_devices(x, y, devices, k=5, maxdist=400):
    """(x,y)に近い機器を距離順に最大k個（現場の距離判断のための候補）。"""
    def box_dist(box):
        x0, y0, x1, y1 = box
        dx = max(x0 - x, 0, x - x1)
        dy = max(y0 - y, 0, y - y1)
        return math.hypot(dx, dy)
    cand = []
    for dv in devices:
        d = box_dist(dv.box)
        if d <= maxdist:
            cand.append((d, dv.sym, dv.box))
    cand.sort()
    out, seen = [], set()
    for d, sym, box in cand:
        if sym in seen:
            continue
        seen.add(sym)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        out.append({'device': sym, 'distance': round(d, 1),
                    'x': round(cx, 1), 'y': round(cy, 1),
                    'box': [round(v, 1) for v in box]})
        if len(out) >= k:
            break
    return out


def build_review(seq_paths, skel_paths=None, layout_path=None, seiban='', dct_paths=None):
    """自動抽出し、確定(confirmed)と未確定(review)に仕分ける。
    review 項目には「線端に近い機器の候補」を付け、作業者が距離で確認できるようにする。"""
    skel_paths = skel_paths or []
    fused = harness.fuse(seq_paths, skel_paths, layout_path)
    models = [DrawingModel(p) for p in seq_paths + skel_paths]
    all_devices = [dv for m in models for dv in m.devices]
    # 図面の全号線ラベル位置（未確定の座標提示に使用）
    label_pos = {}
    for m in models:
        for (v, x, y), k in zip(m.senban, m.senban_kind):
            label_pos.setdefault(norm(v), (x, y, k))

    confirmed, review = [], []
    for sid, net in fused['nets'].items():
        g = norm(sid)
        devs = [d for d in net.get('devices', []) if d != 'TB']
        if len({norm(d) for d in devs}) >= 2:
            confirmed.append(_fromto(sid, net))
        else:
            # 未確定: 現在の端点＋近い機器候補
            lx, ly, lk = label_pos.get(g, (0, 0, net.get('kind', 'ctrl')))
            cands = _nearest_devices(lx, ly, all_devices) if (lx or ly) else []
            review.append({'gousen': sid, 'kind': net.get('kind', 'ctrl'),
                           'current': _fromto(sid, net)['endpoints'],
                           'candidates': cands, 'label': {'x': round(lx, 1), 'y': round(ly, 1)}})
    # 号線ラベルはあるがネット未形成のもの（完全未結線）も review へ
    have = {norm(f['gousen']) for f in confirmed} | {norm(r['gousen']) for r in review}
    for g, (x, y, k) in label_pos.items():
        if g in have:
            continue
        review.append({'gousen': g, 'kind': k, 'current': [],
                       'candidates': _nearest_devices(x, y, all_devices),
                       'label': {'x': round(x, 1), 'y': round(y, 1)}})
    return {'seiban': seiban, 'confirmed': confirmed, 'review': review,
            'summary': {'確定': len(confirmed), '要確認': len(review)}}


def _sidn(s):
    import re
    return re.sub(r'[^0-9A-Z]', '', str(s).upper())


# 要確認オレンジの分類（種類別の色分け＋確認すべきこと）
CATEGORIES = {
    'tb':     {'label': '端子台番号 未記入', 'color': '#d1453b',
               'reason': '線は端子台に来ていますが、端子の番号（何番か）が空です。番号を確認・入力してください。'},
    'bus':    {'label': '単線母線／相', 'color': '#7b3fb5',
               'reason': '単線の母線／相（R/S/T等）です。この母線に繋がる機器を確認してください。'},
    'watari': {'label': '渡り／別シート', 'color': '#1f74c4',
               'reason': '同じ号線が別位置／別シートにもあります。渡りで繋がる相手を確認してください。'},
    'near':   {'label': '近接ギャップ（ほぼ接続）', 'color': '#0f9488',
               'reason': '線端が機器端子のすぐ近くにあります（わずかに届いていない）。ここに繋がるか確認してください。'},
    'float':  {'label': '浮き線端', 'color': '#e0820a',
               'reason': '線端がどこにも届いていません。繋がる機器・端子を確認してください。'},
}


def _classify(g, kind, cur, x, y, net, label_count, near_unfilled_tb, suggest_of):
    """要確認号線を種類分け→(cat, reason)。"""
    import re
    ndev = len({e['device'] for e in cur}) if cur else 0
    is_power = bool(re.match(r'^\d*[RST]\d*$', g)) or g[-1:] in ('R', 'S', 'T')
    # ① 単線母線／相（主回路・電源）
    if kind == 'main' or is_power:
        return 'bus', CATEGORIES['bus']['reason']
    # ② 端子台に来ているが番号未記入
    pts = [(t.x, t.y) for t in (net.get('terminals', []) if net else [])] + [(x, y)]
    if any(near_unfilled_tb(px, py) for px, py in pts):
        return 'tb', CATEGORIES['tb']['reason']
    # ③ 渡り／別シート（同番号が複数箇所）
    if label_count.get(g, 0) >= 2 and ndev >= 1:
        return 'watari', CATEGORIES['watari']['reason']
    # ④ 近接ギャップ（線端のすぐ近くに相手の機器端子がある＝ほぼ接続・相手提示）
    sug = suggest_of.get(_sidn(g)) or []
    if sug:
        names = '／'.join(f"{k}（約{int(gap)}mm）" for k, gap in sug[:2])
        return 'near', f"線端のすぐ近くに {names} があります。ここに繋がるか確認してください（ほぼ接続）。"
    # ⑤ 浮き線端
    return 'float', CATEGORIES['float']['reason']


def build_floor_data(seq_paths, skel_paths=None, layout_path=None, seiban='', dct_paths=None):
    """現場ツール(実図面上で確認)用データ。シート毎に 図面SVG＋未確定号線マーカー＋
    近傍機器の候補ボックス を同一座標系で返す。
    戻り: {seiban, sheets:[{name,w,h,xmin,ymax,body,review:[...],confirmed:[gousen...]}], summary}
    """
    import math
    from . import dxf_svg
    from .geometry import UNFILLED_DEVICE1
    skel_paths = skel_paths or []
    fused = harness.fuse(seq_paths, skel_paths, layout_path)
    # 端子台ラグ（_LU 等）の座標＋番号記入状況を全シートから集める
    tb_lugs = []              # (x, y)
    tb_lugs_full = []         # (x, y, filled?)  filled=DEVICE1が入っている
    label_count = {}          # 号線ラベルの出現箇所数（渡り/別シート判定）
    for p in seq_paths + skel_paths:
        dm = DrawingModel(p)
        for (sym, x, y, box) in getattr(dm, 'termblocks', []):
            tb_lugs.append((x, y))
        for e in dm.msp:
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            nm = e.dxf.name
            a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
            if nm.startswith('_LU') or a.get('DEVICE', '') == 'TB':
                d1 = a.get('DEVICE1', '')
                filled = bool(d1) and d1 not in UNFILLED_DEVICE1
                tb_lugs_full.append((e.dxf.insert.x, e.dxf.insert.y, filled))
        for (v, x, y), k in zip(dm.senban, dm.senban_kind):
            g = _sidn(v)
            if g:
                label_count[g] = label_count.get(g, 0) + 1

    def _near_unfilled_tb(px, py, tol=140):
        for (lx, ly, filled) in tb_lugs_full:
            if not filled and math.hypot(px - lx, py - ly) < tol:
                return True
        return False

    def touches_tb(net, tol=130):
        for t in net.get('terminals', []):
            for (lx, ly) in tb_lugs:
                if math.hypot(t.x - lx, t.y - ly) < tol:
                    return True
        return False

    # 作図規約トレーサ（ドット交差・T字・端子台ラグを規約どおり結線）で号線→機器＋近接候補を得る
    from . import tracer
    traced_detail = tracer.trace_seiban_detail(seq_paths + skel_paths)
    traced = {g: d['devices'] for g, d in traced_detail.items()}
    suggest_of = {_sidn(g): d['suggest'] for g, d in traced_detail.items() if d['suggest']}

    # 号線を 自動確定 / 要確認 に仕分け（fusedベース＋規約トレーサ）
    # 「機器2つ以上」または「機器1つ＋端子台ラグに接続(＝device→TB の1本)」を結線済とする
    confirmed_g, net_of, tb_g = set(), {}, set()
    for sid, net in fused['nets'].items():
        net_of[_sidn(sid)] = net
        devs = {norm(d) for d in net.get('devices', []) if d != 'TB'}
        if len(devs) >= 2:
            confirmed_g.add(_sidn(sid))
        elif len(devs) >= 1 and touches_tb(net):
            confirmed_g.add(_sidn(sid))
            tb_g.add(_sidn(sid))       # device→端子台（端子台番号は別途確認）
    # 規約トレーサで結線できた号線も確定に追加（ドットのある交差・T字・端子台を反映）
    for g, devs in traced.items():
        if tracer.is_formed(devs):
            gg = _sidn(g)
            if gg not in confirmed_g:
                confirmed_g.add(gg)
                if {d for d in devs if d != 'TB'} and 'TB' in devs and len(
                        {d for d in devs if d != 'TB'}) < 2:
                    tb_g.add(gg)

    sheets = []
    total_review = 0
    files = [('制御', p) for p in seq_paths] + [('主回路', p) for p in skel_paths]
    for idx, (grp, p) in enumerate(files):
        m = DrawingModel(p)
        rend = dxf_svg.render([p])
        devs = m.devices
        seen_g = set()
        review = []
        for (v, x, y), k in zip(m.senban, m.senban_kind):
            g = _sidn(v)
            if not g or g in seen_g:
                continue
            seen_g.add(g)
            if g in confirmed_g:
                continue
            net = net_of.get(g)
            cur = _fromto(v, net)['endpoints'] if net else []
            cat, reason = _classify(g, k, cur, x, y, net, label_count,
                                    _near_unfilled_tb, suggest_of)
            review.append({'gousen': v, 'kind': k,
                           'label': {'x': round(x, 1), 'y': round(y, 1)},
                           'current': cur, 'cat': cat, 'reason': reason,
                           'suggest': [s[0] for s in (suggest_of.get(g) or [])],
                           'candidates': _nearest_devices(x, y, devs)})
        total_review += len(review)
        confirmed_here = sorted({net_of[g]['id'] for (v, x, y) in
                                 [(vv, xx, yy) for (vv, xx, yy), kk in zip(m.senban, m.senban_kind)]
                                 for g in [_sidn(v)] if g in confirmed_g and g in net_of})
        # 図面上でタップできるよう全機器ボックスも持たせる（候補外でも選べる）
        dev_boxes = []
        seen_dev = set()
        for dv in devs:
            if dv.sym in seen_dev:
                continue
            seen_dev.add(dv.sym)
            dev_boxes.append({'sym': dv.sym, 'box': [round(v, 1) for v in dv.box]})
        sheets.append({'name': f"{grp}{idx+1}", 'group': grp,
                       'w': rend['w'], 'h': rend['h'],
                       'xmin': rend['xmin'], 'ymax': rend['ymax'],
                       'body': rend['body'], 'review': review,
                       'devices': dev_boxes, 'confirmed': confirmed_here})
    catcount = {}
    for s in sheets:
        for r in s['review']:
            catcount[r['cat']] = catcount.get(r['cat'], 0) + 1
    return {'seiban': seiban, 'sheets': sheets,
            'categories': {k: {'label': v['label'], 'color': v['color']}
                           for k, v in CATEGORIES.items()},
            'summary': {'自動確定': len(confirmed_g), '要確認': total_review,
                        'うち機器→端子台': len(tb_g), '種類別': catcount,
                        'シート': len(sheets)}}


def apply_resolutions(reviewdata, decisions):
    """作業者の解決を反映し、確定 From-To 一覧を返す（＝Aアドオンが書き出すべきデータ）。
    decisions: {gousen: {'endpoints':[{device,no,terminal}], 'note':...}} 作業者が確定した端点。
    戻り: {'fromto': [FromTo...], 'unresolved': [gousen...]}。
    """
    out = list(reviewdata.get('confirmed', []))
    unresolved = []
    dec = decisions or {}
    for item in reviewdata.get('review', []):
        g = item['gousen']
        d = dec.get(g) or dec.get(norm(g))
        if d and d.get('endpoints'):
            eps = [{'device': e.get('device', ''), 'no': e.get('no', ''),
                    'terminal': e.get('terminal', ''), 'source': 'confirmed'}
                   for e in d['endpoints']]
            out.append({'gousen': g, 'kind': item.get('kind', 'ctrl'),
                        'size': d.get('size', ''), 'endpoints': eps})
        else:
            unresolved.append(g)
    return {'fromto': out, 'unresolved': unresolved,
            'summary': {'確定計': len(out), '未解決': len(unresolved)}}


def export_spec():
    """From-To 出力データ仕様（Aアドオンが図面へ書き出す形式）を返す（ドキュメント用）。"""
    return {
        'per_wire_xdata': {
            'GOUSEN': '号線（線番/相番号）',
            'FROM': '接続元 機器記号-端子（例 52-102-4）',
            'TO': '接続先 機器記号-端子（例 TB-102）',
            'WTYPE': '電線種別（KIV/HIV）', 'SIZE': '電線サイズ',
            'COLOR': '色', 'DUCT': 'ダクト方向（上/下/左/右/扉）',
        },
        'per_terminal_attr': {'CONNECT_GOUSEN': 'この端子が接続する号線'},
        'locator': {'DEVICE1': "'レール-文字'（既存 -DCT と同一）"},
        'note': 'B(Python+Web UI)で確定した From-To と同一スキーマを、A(BricsCADアドオン)が'
                '図面のXData/属性へ書き出す。読み手(Python後段)は同じ構造を消費する。',
    }
