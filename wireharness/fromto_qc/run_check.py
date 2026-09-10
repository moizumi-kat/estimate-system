# -*- coding: utf-8 -*-
"""設計次工程で走らせる図面不具合チェッカー CLI。

使い方:
  python -m wireharness.fromto_qc.run_check \
      --seq シーケンス.dxf [シーケンス2.dxf ...] \
      --skel スケルトン.dxf \
      --layout 内部配置図.dxf \
      [--out report.txt]

出力: 各指摘を {場所/問題/提案/根拠} 様式で列挙し、設計への申し送りに使える形にする。
判断は設計（確認ゲート）。実装ルール: R1配置図漏れ / R2容量逆転 / R3中性線端子(V→N) /
R4変更漏れ / H1-H5 ハーネス化検図。
"""
import argparse
import collections
from .geometry import DrawingModel
from . import defect_check as dc
from . import harness_qc


def run(seq_paths, skel_paths, layout_path):
    seq = [DrawingModel(p) for p in (seq_paths or [])]
    skel = [DrawingModel(p) for p in (skel_paths or [])]
    layout = DrawingModel(layout_path) if layout_path else None
    physical = list(skel) + ([layout] if layout else [])

    findings = []
    # R1 配置図漏れ（回路図→配置図）
    if seq and layout:
        findings += dc.rule_R1_layout_missing(seq + skel, layout)
    # R2 容量逆転（スケルトン）
    for m in skel:
        findings += dc.rule_R2_capacity(m)
    # R3 中性線端子(V→N)（スケルトン/結線図）
    for m in skel:
        findings += dc.rule_R3_neutral(m)
    # R4 変更漏れ（制御図の接点 vs 物理図）
    if seq and physical:
        findings += dc.rule_R4_orphan_contact(seq, physical)
    # H1-H5 ハーネス化検図（各シート）
    for m in seq + skel:
        for iss in harness_qc.check(m):
            findings.append({'rule': iss['id'], 'severity': iss['severity'], 'confidence': 'med',
                             '場所': str(iss.get('xy')), '問題': iss['message'],
                             '提案': '号線の付与/結線/端子台中継を確認してください。',
                             '根拠': 'ハーネス化検図(harness_qc)'})
    return findings


def summary(findings):
    c = collections.Counter(f['rule'] for f in findings)
    return dict(c)


def main(argv=None):
    ap = argparse.ArgumentParser(description='設計次工程 図面不具合チェッカー')
    ap.add_argument('--seq', nargs='*', default=[], help='シーケンス図 DXF')
    ap.add_argument('--skel', nargs='*', default=[], help='スケルトン図 DXF')
    ap.add_argument('--layout', default=None, help='内部配置図 DXF')
    ap.add_argument('--out', default=None, help='レポート出力先(省略時は標準出力)')
    a = ap.parse_args(argv)
    findings = run(a.seq, a.skel, a.layout)
    report = f"=== 図面不具合チェック 結果 ===\n件数: {len(findings)}  内訳: {summary(findings)}\n\n" \
        + dc.format_report(findings)
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(report)
        print(f"レポート出力: {a.out}  ({len(findings)}件)")
    else:
        print(report)
    return findings


if __name__ == '__main__':
    main()
