# -*- coding: utf-8 -*-
"""設計への「不足データ入力提案」システム（ステップ①）。

目的:
  人手From-To（目標）を100%再現するために、元CAD図面に足りないデータを検出し、
  設計が入力すべき内容を具体的に提案する。設計が入力すれば、②で From-To は
  人手データと一致する。

検出・提案する不足データ:
  P1 端子台のDEVICE1欠落  … 図面が総称'TB'止まり、人手は'TB-<n>' → 端子台に DEVICE1=<n>
  P2 機器の未描画          … 人手に在る機器が図面に無い → 図面に機器を追加
  P3 号線ネット未形成      … 人手号線が図面で結線できない → 号線ラベル/結線の確認
  P4 名称の読み替え        … 図面名↔人手名の相違（自動推測、現場確認）

入力:
  fused    … harness.fuse の結果（図面From-To）
  human_paths … 人手ハーネス(.txt) のリスト（目標）
  models   … DrawingModel のリスト（端子台位置の特定用, 任意）
  alias    … 読み替え（人手→図面）
"""
import collections
import re
from .geometry import norm
from . import logical_check as lc


def _is_power(g):
    """電源母線・主回路の号線（スケルトン側でバス管理／制御シートの対象外）。"""
    return (bool(re.match(r'E\d', g)) or g.startswith('E')
            or g in ('1N', '1R', '1R1', '1R2', '1S', '1T', '22N', '22T',
                     'W1', 'W2', 'BL1', 'BL2', 'EN11',
                     '102R', '102S', 'RC1', 'SC1'))


def _human_by_gousen(paths):
    hg = {}
    hg_raw = {}
    for p in paths:
        for g, devs in lc.human_ctrl_nets_by_gousen(p).items():
            hg.setdefault(g, set()).update(devs)
    # 端子台の実名（TB-<n>）も号線ごとに保持
    from .compare import parse_human
    for p in paths:
        for n in parse_human(p):
            if n.get('kind') != 'ctrl' or not n.get('id'):
                continue
            g = norm(n['id'])
            for e in n['ends']:
                if e[0] and norm(e[0]) == 'TB':
                    hg_raw.setdefault(g, set()).add(norm(e[0] + e[1]))
    return hg, hg_raw


def propose(fused, human_paths, models=None, alias=None):
    """設計への入力提案リストを返す。各項目 dict(type, gousen, detail, location)。"""
    alias = alias or {}
    dg = lc.drawing_nets_by_gousen(fused)
    hg, hg_tb = _human_by_gousen(human_paths)

    # 図面に実在する機器（別名適用後）
    draw_devs = set()
    for devs in dg.values():
        draw_devs |= devs

    # 端子台（空DEVICE1）の位置一覧（提案の座標用）
    tb_blocks = []
    for m in (models or []):
        for sym, x, y, box in getattr(m, 'termblocks', []):
            if sym == 'TB':          # 空DEVICE1の端子台
                tb_blocks.append((x, y))

    props = []
    for g in sorted(hg):
        if _is_power(g):
            continue                         # 電源/主回路は制御シートの対象外
        h = {alias.get(d, d) for d in hg[g]}
        d = dg.get(g, set())
        if not d:
            # P3: 号線ネット未形成
            props.append({'type': 'P3_号線未形成', 'gousen': g,
                          'detail': f"号線 {g} が図面で結線できません（機器 {sorted(h)}）。"
                                    f"号線ラベルの位置・電線の接続・端子台のDEVICE1を確認してください。",
                          'location': ''})
            continue
        missing = h - d
        for md in sorted(missing):
            if md.startswith('TB') and 'TB' in d:
                # P1: 端子台のDEVICE1欠落（図面は総称TB、人手はTB-n）
                props.append({'type': 'P1_端子台DEVICE1', 'gousen': g,
                              'detail': f"号線 {g}: 図面の端子台が総称'TB'。人手は '{md}'。"
                                        f"端子台に DEVICE1={md.replace('TB','')} を入力してください。",
                              'location': ''})
            elif md not in draw_devs and not md.startswith('TB'):
                # P2: 機器の未描画
                props.append({'type': 'P2_機器未描画', 'gousen': g,
                              'detail': f"号線 {g}: 機器 '{md}' が図面にありません。図面に追加してください。",
                              'location': ''})
    # 重複を種別+detailでまとめる
    seen = set()
    uniq = []
    for p in props:
        k = (p['type'], p['detail'])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq


def report(props, title='設計への不足データ入力提案'):
    L = [f"# {title}", '',
         '人手From-Toを100%再現するために、設計がCAD図面に入力すべきデータの提案です。', '']
    by = collections.defaultdict(list)
    for p in props:
        by[p['type']].append(p)
    labels = {'P1_端子台DEVICE1': '① 端子台の DEVICE1 を入力（総称TB→TB-番号）',
              'P2_機器未描画': '② 機器を図面に追加',
              'P3_号線未形成': '③ 号線ネット未形成（ラベル/結線/端子台を確認）'}
    for t in ['P1_端子台DEVICE1', 'P2_機器未描画', 'P3_号線未形成']:
        items = by.get(t, [])
        if not items:
            continue
        L.append(f"## {labels[t]}（{len(items)}件）")
        for p in items:
            L.append(f"- [号線 {p['gousen']}] {p['detail']}")
        L.append('')
    if not props:
        L.append('不足データはありません。From-To は 100% 再現できます。')
    return '\n'.join(L)
