# -*- coding: utf-8 -*-
"""設計への「不足データ入力提案」システム（ステップ①・図面のみ／人手データ不要）。

目的:
  本番には人手ハーネスが無い。図面“だけ”から「あるべきものが無い／不整合」を
  自己検出し、設計がCADに入力すべきデータを提案する。人手From-Toとの照合はしない。
  （御社仕様書の検図 E3孤立端子・E5線番不整合・E6機器情報不備 に対応）

図面だけで検出する不足データ:
  P1 端子台のDEVICE1欠落   … 端子台記号(_LU)があるのに DEVICE1 が空 → 番号を入力
  P2 機器記号(DEVICE)欠落   … 機器ブロックに DEVICE が空 → 機器名を入力
  P3 号線の未結線          … 号線ラベルがあるのに機器2つ以上に結線できない
                              （浮き線端／端子台DEVICE1欠落／ラベル位置ずれ 等）
  P4 号線ラベルの無い電線   … 機器に繋がる電線ネットに号線が付いていない → 号線を入力

入力:
  models … DrawingModel のリスト（対象図面すべて）
  fused  … harness.fuse の結果（号線マージ済ネット。P3判定に使用）
"""
import collections
import re
from .geometry import norm
from . import logical_check as lc


def _is_power(g):
    """電源母線・主回路の号線（バス管理・制御対象外）。"""
    return (bool(re.match(r'E\d', g)) or g.startswith('E')
            or g in ('1N', '1R', '1R1', '1R2', '1S', '1T', '22N', '22T',
                     'W1', 'W2', 'BL1', 'BL2', 'EN11',
                     '102R', '102S', 'RC1', 'SC1'))


def propose(models, fused, suppress=None, aliases=None):
    """図面のみから設計への入力提案リストを返す。各項目 dict(type, detail, location)。
    suppress: 誤検知が多く学習で抑制された指摘種別(P1_...等)の集合。該当種別は出さない。
    aliases: locator.alias_map（実機器名→ロケータ別名）。リレーの結線判定に用いる。
    """
    suppress = suppress or set()
    props = []

    # P1: DEVICE1 が空/未入力('*'等)の端子台。番号が無いと結線先を特定できず未結線になる。
    from .geometry import UNFILLED_DEVICE1
    for m in models:
        for sym, x, y, box in getattr(m, 'termblocks', []):
            if sym == 'TB':
                props.append({'type': 'P1_端子台DEVICE1',
                              'detail': f"端子台に DEVICE1（TB番号）が入っていません。座標付近の端子台に番号を入力してください。",
                              'location': f"({x:.0f},{y:.0f})"})
        # _LU/端子台ブロックで DEVICE1 が '*' 等のプレースホルダ（未入力）
        for e in m.msp:
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            if not (e.dxf.name.startswith('_LU') or
                    {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}.get('DEVICE', '') == 'TB'):
                continue
            a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
            if a.get('DEVICE1', '') in UNFILLED_DEVICE1:
                props.append({'type': 'P1_端子台DEVICE1',
                              'detail': f"端子台の DEVICE1 が '{a.get('DEVICE1')}'（未入力）です。TB番号を入力してください（結線先の特定に必須）。",
                              'location': f"({e.dxf.insert.x:.0f},{e.dxf.insert.y:.0f})"})

    # P2: DEVICE が空の機器ブロック（配線に接する記号で機器名が無い）
    for m in models:
        for e in m.msp:
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            nm = e.dxf.name
            if nm.startswith('_LU') or nm == '_crossPoint1' or nm.startswith('V_') or nm.startswith('H_'):
                continue
            a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
            # TB/PMT を持つ=機器記号なのに DEVICE 空
            if (a.get('TB', '').strip() or a.get('PMT', '').strip()) and not a.get('DEVICE', '').strip():
                props.append({'type': 'P2_機器記号DEVICE',
                              'detail': f"機器記号(DEVICE)が空のブロックがあります。機器名を入力してください。",
                              'location': f"({e.dxf.insert.x:.0f},{e.dxf.insert.y:.0f})"})

    # P3/P5: 号線ラベル vs 結線（From-To 100%被覆のため、全ラベルの未結線を漏れなく提案）
    dg = lc.drawing_nets_by_gousen(fused, aliases=aliases)   # 号線→機器（ロケータ別名込）
    # 図面中の全号線ラベル（制御 ctrl と 主回路/母線 main の SOU）を種別付きで収集
    labels = {}
    for m in models:
        for (v, x, y), k in zip(m.senban, m.senban_kind):
            g = norm(v)
            if g:
                labels.setdefault(g, (x, y, k))
    for g, (x, y, k) in sorted(labels.items()):
        devs = dg.get(g, set())
        if len(devs) >= 2:
            continue
        if k == 'main' or _is_power(g):
            props.append({'type': 'P5_母線未結線', 'gousen': g,
                          'detail': f"母線/電源 号線 {g} が機器2つ以上に結線できていません"
                                    f"（現在: {sorted(devs) if devs else '無し'}）。"
                                    f"スケルトンの結線（SOU/母線→機器端子）を確認・入力してください。",
                          'location': f"({x:.0f},{y:.0f})"})
        else:
            props.append({'type': 'P3_号線未結線', 'gousen': g,
                          'detail': f"号線 {g} が機器2つ以上に結線できていません"
                                    f"（現在: {sorted(devs) if devs else '無し'}）。"
                                    f"浮き線端・端子台のDEVICE1・ラベル位置を確認してください。",
                          'location': f"({x:.0f},{y:.0f})"})

    # 学習した不具合ルール（自己整合では出せない過去の不具合）を適用
    try:
        from . import defect_rules
        dev_syms = set()
        for m in models:
            for e in m.msp:
                if e.dxftype() == 'INSERT' and e.attribs:
                    a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
                    dv = a.get('DEVICE', '').strip()
                    if dv:
                        dev_syms.add(dv + ('-' + a.get('DEVICE1', '') if a.get('DEVICE1', '').strip() else ''))
        gnets = lc.drawing_nets_by_gousen(fused)
        for f in defect_rules.check(dev_syms, gnets):
            props.append({'type': f['type'], 'detail': f['detail'], 'location': ''})
    except Exception:
        pass

    # 重複除去＋学習による抑制（誤検知の多い種別は出さない）
    seen = set()
    uniq = []
    for p in props:
        if p['type'] in suppress:
            continue
        key = (p['type'], p['detail'], p['location'])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def report(props, title='設計への不足データ入力提案（図面自己検出）'):
    L = [f"# {title}", '',
         '図面だけから「あるべきデータが無い／結線できない」箇所を検出しました。',
         '設計でCADに入力すると、From-To が正しく生成できます。', '']
    labels = {'P1_端子台DEVICE1': '① 端子台の DEVICE1（番号）を入力',
              'P2_機器記号DEVICE': '② 機器記号(DEVICE)を入力',
              'P3_号線未結線': '③ 号線が結線できない（浮き線端／端子台DEVICE1／ラベル位置を確認）',
              'P5_母線未結線': '④ 母線/電源号線が結線できない（スケルトンのSOU/母線→端子を確認）'}
    by = collections.defaultdict(list)
    for p in props:
        by[p['type']].append(p)
    for t in ['P1_端子台DEVICE1', 'P2_機器記号DEVICE', 'P3_号線未結線', 'P5_母線未結線']:
        items = by.get(t, [])
        if not items:
            continue
        L.append(f"## {labels[t]}（{len(items)}件）")
        for p in items:
            loc = f"  座標{p['location']}" if p['location'] else ''
            L.append(f"- {p['detail']}{loc}")
        L.append('')
    if not props:
        L.append('不足データはありません。From-To を生成できます。')
    return '\n'.join(L)
