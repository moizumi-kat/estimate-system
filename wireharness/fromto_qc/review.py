# -*- coding: utf-8 -*-
"""現場レビュー工程（システム検知 → 製造の人間が確認 → 修正案を設計へ）。

流れ:
  1) システムが不具合を検知（design_proposal / defect_rules）
  2) 製造（現場）がその検知結果をレビューし、各件を判定
       採用  … 妥当な指摘 → 設計へ回す
       却下  … 誤検知     → 学習で次回抑制
       保留  … 判断つかず → 設計にも学習にも回さない（要相談）
  3) 採用分だけを「設計への修正提案書」として設計へ戻す
  4) 却下/採用の判定は学習（kenzu_store）に記録され次回に反映

本モジュールは 2)3)4) を担う。現場の判定 decisions は本番ではUI由来。
"""
ACTIONS = ('採用', '却下', '保留')

try:
    import kenzu_store
except Exception:
    kenzu_store = None


def submit(run_id, findings, decisions, user='現場'):
    """現場レビューを反映。
      findings : pipeline の proposals（各 fid 付き）
      decisions: {fid: {'action':'採用'|'却下'|'保留', 'note':...}}
    戻り: {'to_design':[採用findings], 'rejected':[...], 'held':[...]}。
    却下は学習(false_positive)へ、採用は妥当指摘として記録。
    """
    to_design, rejected, held = [], [], []
    for f in findings:
        fid = str(f.get('fid', ''))
        dec = decisions.get(fid, {'action': '保留'})
        act = dec.get('action', '保留')
        note = dec.get('note', '')
        item = dict(f)
        item['review_note'] = note
        if act == '採用':
            to_design.append(item)
            _fb(run_id, 'fixed', f.get('type'), fid, note, user)     # 妥当指摘として記録
        elif act == '却下':
            rejected.append(item)
            _fb(run_id, 'false_positive', f.get('type'), fid, note, user)  # 誤検知→学習
        else:
            held.append(item)
    return {'to_design': to_design, 'rejected': rejected, 'held': held}


def _fb(run_id, disposition, rule, fid, note, user):
    if kenzu_store is None:
        return
    try:
        kenzu_store.add_feedback(run_id, disposition, rule=rule, fid=fid, note=note, user=user)
    except Exception:
        pass


def design_report(to_design, seiban='', reviewer='現場'):
    """採用された指摘を『設計への修正提案書』として整形。"""
    L = [f"# 設計への修正提案書（製番 {seiban}）", '',
         f"製造（{reviewer}）が検図結果をレビューし、採用した指摘です。図面の修正をお願いします。",
         f"件数: {len(to_design)}", '']
    for i, f in enumerate(to_design, 1):
        loc = f"  座標{f.get('location','')}" if f.get('location') else ''
        note = f"\n    現場コメント: {f['review_note']}" if f.get('review_note') else ''
        L.append(f"{i}. [{f.get('type','')}] {f.get('detail','')}{loc}{note}")
    if not to_design:
        L.append('採用された指摘はありません（現場確認で全件クリア）。')
    return '\n'.join(L)
