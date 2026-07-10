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

    # Visionトレース
    if args.vision:
        from . import vision
        print("\n=== Visionトレース → 幾何突合 ===")
        for m, png in (tile_paths or []):
            try:
                v = vision.trace_tile(png)
            except Exception as e:
                print(f"  {png}: Vision失敗 {e}")
                continue
            for row in vision.cross_check(v, m, alias):
                print(f"  {os.path.basename(png)} 号線{row['senban']}: {row['status']} "
                      f"vision={row['vision']} geom={row['geom']}")


if __name__ == '__main__':
    main()
