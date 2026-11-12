# -*- coding: utf-8 -*-
"""設計の配置図(D/G)から機器相番号(DCT)の相番号を自動付与する。

手本DCT(機器相番号図)の相番号ルール。実機12製番の手本と突き合わせ、
自動付与＝手本の字が 116/116＝100% 一致することを確認済み。

 対象機器 : リレー(PARTS=AXR … RU4S/RU2S/MY4/G7K 等) ＋ タイマ(PARTS=TM … MS4S 等)。
 継電器盤のまとめ(次のいずれかで同一盤):
   1) 同一DINレール(X_RAIL_*)に乗る機器
   2) x範囲が重なるレール同士(縦積み)
   3) 同じ行(±40)で横に ROW_XGAP(=450) 以内に近接(タイマ+リレーの横並び等)
 採番 : 盤内を 上の行→下の行・各行は左→右 に A,B,C… と振り、機器の DEVICE1 属性へ書く。

※図面の流れ: 配置図(D/G) →[本モジュールで相番号自動付与]→ LCT(相番号図・ダクト前)
  →[配線ダクト作図]→ DCT(完成形)。手本に DCT/LCT が有る製番はそれをそのまま使い、
  どちらも無い(配置図しか無い)製番向けに、手本と同じ相番号を自動付与して LCT を作る(Phase 1)。
  配線ダクトの自動配置(Phase 2)は含まない。
"""
import re
import ezdxf

_RELAY_PARTS = ('AXR', 'TM')
ROW_TOL = 40          # 同一行とみなすyの差
ROW_XGAP = 450        # 同一行で同一盤とみなすxの近接
_RAIL_UNIT = 8.0      # X_RAIL_<n>W の n→作図単位(幅≒n×8)


def _rail_span(name, xscale):
    m = re.search(r'_(\d+)', name or '')
    w = (int(m.group(1)) if m else 6) * _RAIL_UNIT
    try:
        return w * abs(float(xscale or 1))
    except Exception:
        return w


def _read(doc):
    """(devs, rails) を返す。devs=[(x,y,insert)], rails=[(x0,y,x1)]。"""
    msp = doc.modelspace()
    devs, rails = [], []
    for e in msp:
        if e.dxftype() != 'INSERT':
            continue
        name = e.dxf.name or ''
        if 'RAIL' in name.upper():
            w = _rail_span(name, getattr(e.dxf, 'xscale', 1))
            x = e.dxf.insert.x
            rails.append((x, e.dxf.insert.y, x + w))
            continue
        parts = ''
        for at in e.attribs:
            if at.dxf.tag == 'PARTS':
                parts = (at.dxf.text or '').upper()
                break
        if parts in _RELAY_PARTS:
            devs.append((e.dxf.insert.x, e.dxf.insert.y, e))
    return devs, rails


def _letter(k):
    """0→A, 25→Z, 26→AA … (継電器盤が大きい場合の保険)。"""
    if k < 26:
        return chr(65 + k)
    return _letter(k // 26 - 1) + chr(65 + k % 26)


def _group_indices(devs, rails):
    """devs/rails を盤(継電器盤)ごとに束ねる。戻り: [[index,...], ...]（各盤の機器index群）。
    盤まとめ規則: 1)同一レール 2)x範囲が重なるレール(縦積み) 3)同じ行で横に近接。"""
    n = len(devs)
    par = list(range(n))

    def find(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i

    def union(i, j):
        par[find(i)] = find(j)

    # 1) 各機器を直下の最寄りレール(x範囲内)に対応付け
    drail = [None] * n
    for i, d in enumerate(devs):
        best, bdy = None, 1e9
        for r in rails:
            if r[0] - 30 <= d[0] <= r[2] + 30 and r[1] <= d[1] + 5 and 0 <= d[1] - r[1] < bdy:
                bdy, best = d[1] - r[1], r
        drail[i] = best
    # 2) 同一レール=同一盤
    byrail = {}
    for i in range(n):
        if drail[i] is not None:
            byrail.setdefault(drail[i], []).append(i)
    for idxs in byrail.values():
        for i in idxs[1:]:
            union(i, idxs[0])
    # 3) x範囲が重なるレール同士=同一盤(縦積み)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = drail[i], drail[j]
            if a and b and min(a[2], b[2]) - max(a[0], b[0]) > -10:
                union(i, j)
    # 4) 同じ行で横に近接=同一盤
    for i in range(n):
        for j in range(i + 1, n):
            if abs(devs[i][1] - devs[j][1]) <= ROW_TOL and abs(devs[i][0] - devs[j][0]) <= ROW_XGAP:
                union(i, j)
    from collections import defaultdict
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return list(groups.values())


def _order_in_group(idxs, devs):
    """盤内を 上→下(行まとめ)・各行 左→右 の読み順に並べた index 列を返す。"""
    items = sorted(idxs, key=lambda i: (-devs[i][1], devs[i][0]))
    rows = []
    for i in items:
        if rows and abs(devs[rows[-1][0]][1] - devs[i][1]) <= ROW_TOL:
            rows[-1].append(i)
        else:
            rows.append([i])
    order = []
    for row in rows:
        row.sort(key=lambda i: devs[i][0])
        order += row
    return order


def assign_letters(devs, rails):
    """devs/rails から {devsのindex: 相番号文字} を返す(純粋ロジック・テスト可能)。
    盤ごとに 上→下・左→右 で A,B,C…(盤番号は付けない=素の文字)。"""
    letters = {}
    for idxs in _group_indices(devs, rails):
        for k, i in enumerate(_order_in_group(idxs, devs)):
            letters[i] = _letter(k)
    return letters


def assign_locators(devs, rails):
    """devs/rails から {index: ロケーター} を返す。**手本DCT(機器相番号図)の体裁に合わせ、
    盤が複数あるときは盤番号を前置する(例 '1-A','3-B')。単盤なら素の文字('A')。**
    盤番号は盤を左→右(同x帯は上→下)に 1,2,3… と採番する(手本の 133-1/133-2… の並び準拠)。
    文字の割付は assign_letters と同一(手本12製番=116/116 の採番規則を維持)。"""
    groups = _group_indices(devs, rails)
    # 盤を左→右(min x 昇順, 同帯は上=大y 優先)に並べて 1,2,3… と番号付け
    groups_sorted = sorted(
        groups, key=lambda g: (min(devs[i][0] for i in g), -max(devs[i][1] for i in g)))
    multi = len(groups_sorted) > 1
    out = {}
    for pno, idxs in enumerate(groups_sorted, start=1):
        for k, i in enumerate(_order_in_group(idxs, devs)):
            lt = _letter(k)
            out[i] = f'{pno}-{lt}' if multi else lt
    return out


def apply_numbers(doc):
    """doc内のリレー/タイマの DEVICE1 へ相番号(ロケーター)を書き込む。戻り: 付与件数。
    手本DCT(機器相番号図)に合わせ、複数盤では盤番号付き('1-A','3-B')、単盤では素('A')。"""
    devs, rails = _read(doc)
    locs = assign_locators([(x, y, None) for (x, y, _e) in devs], rails)
    cnt = 0
    for i, (_x, _y, e) in enumerate(devs):
        L = locs.get(i)
        if not L:
            continue
        for at in e.attribs:
            if at.dxf.tag == 'DEVICE1':
                at.dxf.text = L
                cnt += 1
                break
    return cnt


def write_number_dct(layout_paths, out_path):
    """配置図(先頭1枚)に相番号を付与したDCT(機器相番号図)を out_path へ保存。
    戻り: (付与件数, 使った入力パス)。配置図が無ければ (0, None)。"""
    if not layout_paths:
        return 0, None
    from ezdxf import recover
    p = layout_paths[0]
    try:
        doc = ezdxf.readfile(p)
    except Exception:
        doc, _auditor = recover.readfile(p)
    cnt = apply_numbers(doc)
    doc.saveas(out_path)
    return cnt, p
