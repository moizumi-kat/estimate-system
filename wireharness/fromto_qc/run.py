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
    ap.add_argument('--vision', action='store_true', help='Claude Visionトレースを実行（要 ANTHROPIC_API_KEY）')
    ap.add_argument('--gemini', action='store_true', help='Gemini Visionも実行しアンサンブル（要 GEMINI_API_KEY）')
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
    if args.vision or args.gemini:
        from . import vision, ensemble
        from .geometry import norm

        def run_tracer(tracer, label):
            per = []
            for p, m in zip(args.dxf, models):
                try:
                    t = vision.trace_drawing(m, cols=args.cols, rows=args.rows, tracer=tracer)
                except Exception as e:
                    print(f"  [{os.path.basename(p)}] {label}失敗 {e}")
                    continue
                per.append(t)
            merged = vision.merge_sheets(*per) if per else {}
            return {sid: slot['devices'] for sid, slot in merged.items()}

        sources = {'幾何': {}}
        for m in models:
            for sid, dv in ensemble.geom_by_senban(m, alias).items():
                sources['幾何'].setdefault(sid, set()).update(dv)
        if args.vision:
            print("\n=== Claude Visionトレース ===")
            sources['Claude'] = run_tracer(vision.trace_tile, 'Claude')
            print(f"  号線 {len(sources['Claude'])} 本")
        if args.gemini:
            print("\n=== Gemini Visionトレース ===")
            sources['Gemini'] = run_tracer(vision.trace_tile_gemini, 'Gemini')
            print(f"  号線 {len(sources['Gemini'])} 本")

        rows = ensemble.ensemble(sources)
        print("\n=== アンサンブル判定（幾何×Claude×Gemini）===")
        for r in rows:
            tag = {'confirmed': '◎自動確定', 'majority': '○多数決', 'split': '△要確認'}[r['decision']]
            print(f"  {tag} 号線{r['senban']:6s} 結果={r['result']} ({r['agree']}/{r['of']}一致) {r['votes']}")
        print("\n  サマリ:", ensemble.summarize(rows))

        # 人手正解と突合（アンサンブル結果 vs 正解）
        if args.human:
            gt = {}
            for hn in compare.parse_human(args.human):
                if hn['kind'] == 'ctrl':
                    gt[hn['id'].replace('_', '')] = {alias.get(norm(e[0] + e[1]), norm(e[0] + e[1]))
                                                     for e in hn['ends'] if e[0]}
            res = {r['senban']: set(r['result']) for r in rows}
            ok = part = 0
            for sid, G in gt.items():
                V = res.get(sid, set())
                if V >= G and V:
                    ok += 1
                elif V & G:
                    part += 1
            tot = len(gt) or 1
            print(f"  正解突合: 号線{len(gt)}件中 完全{ok} 部分{part} "
                  f"→ 完全{ok/tot*100:.0f}% 検出{(ok+part)/tot*100:.0f}%")


if __name__ == '__main__':
    main()
