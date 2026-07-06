#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不明瞭対策の実証: A3ページ全体1枚 vs 2x2タイル分割 で読取品質を比較"""
import fitz, app, io

def count(res):
    items=[it for p in res.get('panels',[]) for it in p.get('items',[])]
    unclear=sum(1 for it in items if it.get('unclear'))
    return len(res.get('panels',[])), len(items), unclear

cli=app.client()
doc=fitz.open('jakuden.pdf'); page=doc[0]; r=page.rect

# --- ベースライン: ページ全体を3倍で1枚 ---
pix=page.get_pixmap(matrix=fitz.Matrix(3,3))
base=app.extract(cli, pix.tobytes('png'), 'image/png')
bp,bi,bu=count(base)
print(f'【全体1枚(実効~95dpi)】 盤{bp} 機器{bi} 不明瞭{bu}')

# --- タイル: 2x2 に10%オーバーラップで分割、各タイル長辺~1568pxへ ---
nx,ny=2,2; ov=0.10
tot_items=0; tot_unclear=0; tiles=0
for iy in range(ny):
    for ix in range(nx):
        x0=r.width*(ix/nx - ov); x1=r.width*((ix+1)/nx + ov)
        y0=r.height*(iy/ny - ov); y1=r.height*((iy+1)/ny + ov)
        clip=fitz.Rect(max(0,x0),max(0,y0),min(r.width,x1),min(r.height,y1))
        # タイル長辺が1568pxになるズーム倍率
        zoom=1568/max(clip.width,clip.height)
        pix=page.get_pixmap(matrix=fitz.Matrix(zoom,zoom), clip=clip)
        eff=zoom*72
        res=app.extract(cli, pix.tobytes('png'), 'image/png')
        _,ti,tu=count(res)
        tot_items+=ti; tot_unclear+=tu; tiles+=1
        print(f'  タイル[{ix},{iy}] {pix.width}x{pix.height}px 実効{eff:.0f}dpi -> 機器{ti} 不明瞭{tu}')
print(f'【2x2タイル(実効~{1568/(r.width/nx)*72:.0f}dpi)】 {tiles}タイル計 機器{tot_items} 不明瞭{tot_unclear}  ※境界重複で機器数は増(要マージ)')
