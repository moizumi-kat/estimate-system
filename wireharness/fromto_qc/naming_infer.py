# -*- coding: utf-8 -*-
"""機器名の読み替え（図面名→ハーネス名）を推測して、現場確認用リストを作る。

前提: 図面とハーネスで機器名の付け方が違う（例 図面3-102↔ハーネス切102/入102、
      図面ALX-1↔ハーネスA）。読み替えルールは明文化されておらず人により差もある。
方針: 複数セットの実データから推測し、根拠・確信度つきの候補リストを出す。現場で確認。

推論シグナル:
  1) 同一（正規化名が一致）… 読み替え不要。確信度=高。
  2) 号線共起 … 同じ号線に載る、図面だけ/ハーネスだけの機器を対応候補に。票が多いほど高。
  3) 属性ヒント … 図面セレクタ(DEVICE=3, CMNTJ1=切/入)→ ハーネス 切N/入N 等。
出力: {図面名: [(ハーネス名, 確信度, 根拠, 票), ...]}（候補・複数可）。
"""
import collections
import ezdxf
from .geometry import norm
from . import harness, logical_check as lc


def drawing_device_names(dxf_paths):
    """図面の機器名（DEVICE+DEVICE1 正規化）と属性ヒントを収集。"""
    names = set()
    hints = {}     # 図面名(norm) -> ハーネス名候補(属性由来)
    for p in dxf_paths:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for e in doc.modelspace():
            if e.dxftype() == 'INSERT' and e.attribs:
                a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
                dev = a.get('DEVICE', '').strip()
                if not dev or dev.upper() in ('LUG', 'CABLE', 'CABLE1', 'CABLE2', 'TB'):
                    pass
                if not dev:
                    continue
                d1 = a.get('DEVICE1', '').strip()
                nm = norm(dev + d1)
                if not nm:
                    continue
                names.add(nm)
                # 属性ヒント: 扉セレクタ 3-xxx で CMNTJ1 に 切/入 → ハーネス 切xxx/入xxx
                cm = a.get('CMNTJ1', '').strip()
                if dev == '3' and cm in ('切', '入', '自動', '手動', '遠方'):
                    hints.setdefault(nm, set()).add(norm(cm + d1))
    return names, hints


def harness_device_names(harness_paths):
    from .compare import parse_human
    names = set()
    for p in harness_paths:
        for n in parse_human(p):
            for e in n['ends']:
                if e[0]:
                    nm = norm(e[0] + e[1])
                    if nm and nm not in ('LUG', 'WAGO'):
                        names.add(nm)
    return names


def infer(sets):
    """sets: [{'name','wire_dxf':[...],'skel_dxf':[...],'harness':[...],'all_dxf':[...]}]
    戻り: 提案リスト（行の list）。各行 = dict(draw, harness, conf, basis, votes)。
    """
    pooled_votes = collections.Counter()
    draw_all = set()
    harn_all = set()
    hints_all = collections.defaultdict(set)
    for s in sets:
        # 号線共起の投票
        fused = harness.fuse(s['wire_dxf'], s.get('skel_dxf', []), None)
        dg = lc.drawing_nets_by_gousen(fused)
        hg = lc.human_ctrl_nets_by_gousen(s['harness'][0]) if len(s['harness']) == 1 else _merge_harness(s['harness'])
        _, votes = lc.auto_alias(dg, hg)
        for (h, d), v in votes.items():   # h=ハーネス名, d=図面名
            pooled_votes[(d, h)] += v
        # 名称集合・属性ヒント
        dn, hints = drawing_device_names(s.get('all_dxf', s['wire_dxf'] + s.get('skel_dxf', [])))
        draw_all |= dn
        for k, vs in hints.items():
            hints_all[k] |= vs
        harn_all |= harness_device_names(s['harness'])

    rows = []
    done = set()
    # 1) 同一
    for d in sorted(draw_all & harn_all):
        rows.append(dict(draw=d, harness=d, conf='高', basis='同一(読替不要)', votes=''))
        done.add(d)
    # 2) 属性ヒント（セレクタ等）
    for d, cand in sorted(hints_all.items()):
        for h in sorted(cand):
            if h in harn_all:
                rows.append(dict(draw=d, harness=h, conf='中', basis='属性(セレクタ位置)', votes=''))
                done.add(d)
    # 3) 号線共起（同一・属性で未確定の図面名のみ、最多得票を採用）
    best = {}
    for (d, h), v in pooled_votes.items():
        if d in done:
            continue
        if d not in best or v > best[d][1]:
            best[d] = (h, v)
    for d, (h, v) in sorted(best.items(), key=lambda x: -x[1][1]):
        conf = '中' if v >= 4 else '低'
        rows.append(dict(draw=d, harness=h, conf=conf, basis='号線共起', votes=v))
        done.add(d)
    # 4) 未対応（図面にあるがハーネスに対応不明）
    for d in sorted(draw_all - done):
        rows.append(dict(draw=d, harness='?', conf='要確認', basis='対応不明', votes=''))
    return rows


def _merge_harness(paths):
    out = {}
    for p in paths:
        g = lc.human_ctrl_nets_by_gousen(p)
        for k, v in g.items():
            out.setdefault(k, set()).update(v)
    return out


def to_markdown(rows):
    L = ['# 機器名 読み替え候補（推測・現場確認用）', '',
         '| 図面名 | ハーネス名(推測) | 確信度 | 根拠 | 票 |',
         '|---|---|---|---|---|']
    order = {'高': 0, '中': 1, '低': 2, '要確認': 3}
    for r in sorted(rows, key=lambda r: (order.get(r['conf'], 9), str(r['draw']))):
        L.append(f"| {r['draw']} | {r['harness']} | {r['conf']} | {r['basis']} | {r['votes']} |")
    return '\n'.join(L)
