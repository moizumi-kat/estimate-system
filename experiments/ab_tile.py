#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""電灯分電盤で 1x1(現行) vs 2x2(タイル) を各2回。機器検出数の安定性と確定率を比較。"""
import app, time
cli=app.client()
data=open('bunden.pdf','rb').read()

def summarize(panels_sel):
    c={'◎':0,'◉':0,'○':0,'△':0}
    for p in panels_sel:
        for r in p['rows']:
            if r.get('load_detail') or r['conf'] not in c: continue
            c[r['conf']]+=1
    tot=sum(c.values()) or 1
    return c, round((c['◎']+c['◉']+c['○'])/tot*100)

def run(grid):
    app.TILE_GRID=grid
    t=time.time()
    ex=app.extract_pdf_hires(cli, data)            # gridに応じて分割/カスケード
    nitems=sum(len(p.get('items',[])) for p in ex['panels'])
    sel=app.select_from_extracted({'panels':ex['panels']})
    c,rate=summarize(sel)
    return nitems, len(ex['panels']), c, rate, time.time()-t

print(f"{'方式':<6}{'試行':>4}{'盤':>4}{'機器':>6}{'◎':>5}{'◉':>4}{'○':>4}{'△':>4}{'確定率':>7}{'秒':>6}")
print("-"*52)
res={}
for grid in ['1x1','2x2']:
    res[grid]=[]
    for tr in (1,2):
        n,np_,c,rate,sec=run(grid)
        res[grid].append((n,rate))
        print(f"{grid:<6}{tr:>4}{np_:>4}{n:>6}{c['◎']:>5}{c['◉']:>4}{c['○']:>4}{c['△']:>4}{str(rate)+'%':>7}{int(sec):>5}s")
print("-"*52)
for grid in ['1x1','2x2']:
    ns=[x[0] for x in res[grid]]; rs=[x[1] for x in res[grid]]
    print(f"  {grid}: 機器数 {ns} (変動{max(ns)-min(ns)}) / 確定率 {rs}")
