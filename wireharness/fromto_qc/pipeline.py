# -*- coding: utf-8 -*-
"""通しパイプライン: 図面 → ①不足検出 → ②From-To抽出 → ③ハーネスシート生成。

1本で ①design_proposal ②geometry/logical ③rules_engine+layout+main_circuit を
オーケストレーションし、設計提案・From-To・ハーネスシートを出す。
"""
from .geometry import DrawingModel
from . import harness, design_proposal, rules_engine, layout as layout_mod

# 学習ストア（検図フィードバック）。リポジトリ直下の kenzu_store を任意で利用。
try:
    import os
    import sys
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    import kenzu_store
except Exception:
    kenzu_store = None


def _logical_from_fused(fused):
    """harness.fuse の結果 → 論理ネット {号線: {'endpoints':[(dev,no,term)],'kind','size'}}。"""
    out = {}
    for sid, net in fused['nets'].items():
        eps = []
        for t in net.get('terminals', []):
            dev = t.device
            if '-' in dev:
                d, n = dev.rsplit('-', 1)
            else:
                d, n = dev, ''
            eps.append((d, n, t.name if t.name and t.name != '?' else ''))
        if len(eps) >= 2:
            out[sid] = {'endpoints': eps, 'kind': net.get('kind', 'ctrl'),
                        'size': net.get('wire_size', '')}
    return out


def run(seq_paths, skel_paths=None, layout_path=None, seiban=''):
    """通しパイプライン実行。戻り: dict(proposals, logical, harness_rows, defects, summary, run_id)。
    検図の指摘は kenzu_store に記録され run_id が発行される。設計の判定
    （kenzu_store.add_feedback / save_naming）が次回の学習に反映される。"""
    skel_paths = skel_paths or []
    # 学習の反映（誤検知の抑制・確定名称）
    learned = {}
    if kenzu_store is not None:
        try:
            learned = kenzu_store.learned()
        except Exception:
            learned = {}
    suppress = learned.get('suppress', set())
    # ② 抽出
    fused = harness.fuse(seq_paths, skel_paths, layout_path)
    models = [DrawingModel(p) for p in seq_paths + skel_paths]
    # ① 設計への不足データ提案（図面のみ・学習で抑制を反映）
    proposals = design_proposal.propose(models, fused, suppress=suppress)
    # 実行を記録（設計の判定＝フィードバックを後で紐付けられるよう run_id を発行）
    run_id = None
    if kenzu_store is not None:
        try:
            findings = [{'rule': p['type'], 'detail': p['detail'],
                         'location': p['location']} for p in proposals]
            run_id, saved = kenzu_store.save_run(
                {'seiban': seiban, 'seq': seq_paths}, findings)
            for p, s in zip(proposals, saved):
                p['fid'] = s['fid']
        except Exception:
            pass
    # ② 論理 From-To
    logical = _logical_from_fused(fused)
    # ③ 物理ハーネス生成
    lay = None
    if layout_path:
        try:
            lay = layout_mod.Layout(layout_path)
        except Exception:
            lay = None
    rows, defects = rules_engine.generate(logical, lay)
    return {'proposals': proposals, 'logical': logical,
            'harness_rows': rows, 'defects': defects, 'run_id': run_id,
            'learned': {'suppressed': sorted(suppress),
                        'naming_count': len(learned.get('naming', {}))},
            'summary': {'号線': len(logical), '生成電線': len(rows),
                        '設計提案': len(proposals), '検図(配置図不足)': len(defects),
                        **rules_engine.summary(rows)}}


def report(res, title='ハーネス生成 通しレポート'):
    L = [f"# {title}", '', f"概要: {res['summary']}", '']
    # ① 設計提案
    L.append('## ① 設計への不足データ入力提案（図面自己検出）')
    if res['proposals']:
        L.append(design_proposal.report(res['proposals']).split('\n', 4)[-1])
    else:
        L.append('不足なし。')
    # ③ ハーネスシート（先頭20行）
    L.append('')
    L.append('## ③ 生成ハーネスシート（先頭20行）')
    L.append('| 号線 | 種別/サイズ | From | To | マーカー | ダクト |')
    L.append('|---|---|---|---|---|---|')
    for r in res['harness_rows'][:20]:
        f, t = r['from'], r['to']
        frm = f"{f[0]}-{f[1]}({f[2]})"
        to = f"{t[0]}-{t[1]}({t[2]})"
        duct = f"{r.get('dir_from','')}/{r.get('dir_to','')}".strip('/')
        L.append(f"| {r['gousen']} | {r['wtype']}/{r['size']} | {frm} | {to} | {r['marker']} | {duct} |")
    if res['defects']:
        L.append('')
        L.append(f"## 検図: 配置図に無い機器（設計へ）: {', '.join(res['defects'])}")
    return '\n'.join(L)
