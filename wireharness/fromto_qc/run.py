#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI: 電気図面 → 構造モデル抽出 → QCチェック → (任意)Vionトレース → From-To突合。

使い方:
  # 幾何のみ（APIキー不要）: QCチェックと機器レベルFrom-Toの突合
  python -m wireharness.fromto_qc.run --dxf seq.dxf skel.dxf --human harness.txt

  # タイル画像を出力（Vision入力用の確認）
  python -m wireharness.fromto_qc.run --dxf seq.dxf --tiles out/ --cols 2 --rows 3

  # Visionトレース（要 ANTHROPIC_API_KEY）→ 幾何と突合
  ANTHROPIC_API_KEY=sk-... python -m wireharness.fromto_qc.run --dxf seq.dxf --vision --tiles out/
"""
import os
import argparse
import json
from .geometry import DrawingModel
from . import qc, compare, render


def main(argv=None):
    ap = argparse.ArgumentParser(description='電気図面 QC + From-To 抽出（DXF×Vision ハイブリッド）')
    ap.add_argument('--dxf', nargs='+', required=True, help='DXF（シーケンス/スケルトン等・複数可）')
    ap.add_argument('--human', help='人手ハーネスデータ.txt（突合用・任意）')
    ap.add_argument('--tiles', help='タイル画像の出力フォルダ（指定時にレンダリング）')
    ap.add_argument('--cols', type=int, default=2)
    ap.add_argument('--rows', type=int, default=3)
    ap.add_argument('--vision', action='store_true', help='Visionトレースを実行（要 ANTHROPIC_API_KEY）')
    ap.add_argument('--alias', help='機器別名JSON（{"入102":"3102",...}）任意')
    args = ap.parse_args(argv)

    alias = json.load(open(args.alias, encoding='utf-8')) if args.alias else {}

    models = []
    for p in args.dxf:
        m = DrawingModel(p)
        models.append(m)
        print(f"[{os.path.basename(p)}] 端子{len(m.terminals)} / 電線{len(m.segments)} / ネット{len(m.nets)} / 号線{len(m.senban)}")

    # QCチェック
    print("\n=== 製造前 QCチェック ===")
    for p, m in zip(args.dxf, models):
        iss = qc.check(m)
        print(f"  [{os.path.basename(p)}] {qc.summarize(iss)}")

    # 機器レベル From-To 突合
    if args.human:
        hE = compare.human_device_edges(compare.parse_human(args.human), alias)
        mE = set()
        for m in models:
            mE |= compare.model_device_edges(m, alias)
        print("\n=== 機器レベル From-To 突合（幾何のみ）===")
        print("  ", compare.score_edges(hE, mE))

    # タイル出力
    tile_paths = []
    if args.tiles:
        os.makedirs(args.tiles, exist_ok=True)
        for i, m in enumerate(models):
            for j, region in enumerate(render.tile_grid(m, args.cols, args.rows)):
                out = os.path.join(args.tiles, f"dxf{i}_tile{j}.png")
                render.render_region(m, region, out)
                tile_paths.append((m, out))
        print(f"\nタイル画像 {len(tile_paths)} 枚を {args.tiles} に出力")

    # Visionトレース（全面タイル化＋号線でシート統合）
    if args.vision:
        from . import vision
        print("\n=== Visionトレース（全面タイル → 号線統合）===")
        per_sheet = []
        for p, m in zip(args.dxf, models):
            try:
                t = vision.trace_drawing(m, cols=args.cols, rows=args.rows)
            except Exception as e:
                print(f"  [{os.path.basename(p)}] Vision失敗 {e}")
                continue
            per_sheet.append(t)
            print(f"  [{os.path.basename(p)}] 号線 {len(t)} 本を読取")
        traced = vision.merge_sheets(*per_sheet) if per_sheet else {}
        # From-To（Vision・号線→機器）出力
        for sid in sorted(traced):
            print(f"    号線{sid}: {sorted(traced[sid]['devices'])}"
                  + ("  ※要確認" if traced[sid]['unclear'] else ""))
        # 人手正解と突合（あれば）
        if args.human:
            from .geometry import norm
            gt = {}
            for hn in compare.parse_human(args.human):
                if hn['kind'] == 'ctrl':
                    gt[hn['id'].replace('_', '')] = {alias.get(norm(e[0] + e[1]), norm(e[0] + e[1]))
                                                     for e in hn['ends'] if e[0]}
            ok = part = 0
            for sid, G in gt.items():
                V = traced.get(sid, {}).get('devices', set())
                if V >= G and V:
                    ok += 1
                elif V & G:
                    part += 1
            tot = len(gt) or 1
            print(f"\n  Vision突合: 号線{len(gt)}件中 完全{ok} 部分{part} "
                  f"→ 完全{ok/tot*100:.0f}% 検出{(ok+part)/tot*100:.0f}%")


if __name__ == '__main__':
    main()
