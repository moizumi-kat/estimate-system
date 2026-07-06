#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude × Gemini 二重Vision 実突合。適用表を両モデルで読み、一致→◎/不一致→△。"""
import json, re
from cross_check import crop, claude_read, reconcile, TAB_P, key_of, FIELDS, norm
from google import genai
from google.genai import types

GKEY=open('.gemini_key').read().strip()
gclient=genai.Client(api_key=GKEY)

def gemini_read(png):
    img=types.Part.from_bytes(data=png, mime_type='image/png')
    r=gclient.models.generate_content(model='gemini-2.5-pro',
        contents=[TAB_P, img],
        config=types.GenerateContentConfig(response_mime_type='application/json'))
    t=(r.text or '').strip()
    try: d=json.loads(t)
    except: mm=re.search(r'\{.*\}',t,re.S); d=json.loads(mm.group(0)) if mm else {}
    return d.get('rows',[])

import os
png=crop('seigyo.pdf',0,0.47,1.0,0.05,0.72)
CC='cc_reads.json'
if os.path.exists(CC):
    d=json.load(open(CC,encoding='utf-8')); C,G=d['C'],d['G']; print('(キャッシュ利用)')
else:
    print('Claude 読み取り中...'); C=claude_read(png,'claude')
    print('Gemini 読み取り中...'); G=gemini_read(png)
    json.dump({'C':C,'G':G}, open(CC,'w',encoding='utf-8'), ensure_ascii=False)
print(f"\nClaude {len(C)}行 / Gemini {len(G)}行")
res=reconcile(C,G)   # A=Claude, B=Gemini
ok=sum(1 for c,_,_,_ in res if c=='◎'); ng=len(res)-ok
print(f"突合: ◎一致 {ok} / △要確認 {ng} / 一致率 {round(ok/(len(res) or 1)*100)}%\n")
print("--- 食い違い(=要確認 △) ---")
n=0
for c,a,b,d in res:
    if c=='△':
        who=(a or b); n+=1
        print(f"  △ {who.get('panel',''):<6}{who.get('load_no',''):<10} {' / '.join(d)[:70]}")
if n==0: print("  (なし=全項目で両モデル一致)")
