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
