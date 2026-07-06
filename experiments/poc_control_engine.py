#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""制御盤PoC: パターンマスタ(主回路A-L/操作1-11) + 適用表 → 展開 → BOM。
Vision結果はpc_cache.jsonにキャッシュ(展開ロジックの反復を安価に)。"""
import app, base64, json, re, fitz, os
from collections import Counter
cli=app.client()
CACHE='pc_cache.json'
cache=json.load(open(CACHE,encoding='utf-8')) if os.path.exists(CACHE) else {}
def save(): json.dump(cache, open(CACHE,'w',encoding='utf-8'), ensure_ascii=False, indent=1)

def crop(pdf,pi,x0,x1,y0,y1,target=2000):
    doc=fitz.open(pdf); pg=doc[pi]; r=pg.rect
    clip=fitz.Rect(r.width*x0,r.height*y0,r.width*x1,r.height*y1)
    z=target/max(clip.width,clip.height)
    return pg.get_pixmap(matrix=fitz.Matrix(z,z),clip=clip).tobytes('png')

def vision(key,png,prompt):
    if key in cache: return cache[key]
    src={'type':'base64','media_type':'image/png','data':base64.standard_b64encode(png).decode()}
    with cli.messages.stream(model='claude-opus-4-8',max_tokens=16000,
        messages=[{'role':'user','content':[{'type':'text','text':prompt},{'type':'image','source':src}]}]) as st:
        m=st.get_final_message()
    t=''.join(b.text for b in m.content if b.type=='text').strip(); t=re.sub(r'^```(json)?|```$','',t,flags=re.M).strip()
    try: d=json.loads(t)
    except: mm=re.search(r'\{.*\}',t,re.S); d=json.loads(mm.group(0)) if mm else {'raw':t[:300]}
    cache[key]=d; save(); return d

MAIN_P='''動力制御盤の「主回路パターン集」。A〜Lの各枠の電気部品を種類+数量で。
電動機M(=負荷)は含めない。部品種類: MCCB/ELB,電磁接触器MC,サーマルTHR,変流器CT,電流計A,INV。
出力 {"patterns":[{"id":"C","title":"直入始動","parts":[{"kind":"MCCB","qty":1},...]}]} のみ。'''
CTRL_P='''動力制御盤の「操作回路パターン集」。1〜11の各枠の電気部品を種類+数量で。
部品種類の例: 補助リレー,タイマ,表示灯,押ボタン,切換スイッチ,端子台,ブザー,フロートスイッチ。
出力 {"patterns":[{"id":"3","title":"手動-遠方","parts":[{"kind":"補助リレー","qty":2},...]}]} のみ。'''
TAB_P='''動力制御盤の「適用表(リスト)」。各負荷行を漏れなく抽出。
列: panel(制御盤名M-1A等,繰返), load_no, load, kw, kind(●=MCCB/○=ELB/空), main(主回路A〜L), ctrl(操作1〜11), breaker(備考の3P xxxAF/xxxAT), note。
予備行も load='予備' で記録。読めない値は空+unclear:true。出力 {"rows":[...]} のみ。'''

# --- 1. パターンマスタ ---
mp=vision('main_patterns', crop('seigyo.pdf',0,0.02,0.47,0.07,0.30), MAIN_P)
cp=vision('ctrl_patterns', crop('seigyo.pdf',0,0.02,0.47,0.30,0.72), CTRL_P)
main_master={p['id']:p.get('parts',[]) for p in mp.get('patterns',[])}
ctrl_master={str(p['id']):p.get('parts',[]) for p in cp.get('patterns',[])}
print(f"パターンマスタ: 主回路{len(main_master)}種 / 操作回路{len(ctrl_master)}種")

# --- 2. 適用表(3クロップ: p1右, p2左列, p2右列) ---
rows=[]
for key,(pdf,pi,x0,x1,y0,y1) in {
    't_p1':('seigyo.pdf',0,0.47,1.0,0.05,0.72),
    't_p2L':('seigyo.pdf',1,0.02,0.50,0.05,0.95),
    't_p2R':('seigyo.pdf',1,0.50,1.0,0.05,0.95)}.items():
    rows+=vision(key, crop(pdf,pi,x0,x1,y0,y1), TAB_P).get('rows',[])
loads=[r for r in rows if (r.get('load') or '')!='予備' and (r.get('main') or r.get('breaker'))]
print(f"適用表: 全{len(rows)}行 / 実負荷(予備除く){len(loads)}行 / 盤{len(set(r.get('panel') for r in loads))}面")

# --- 3. 展開 → BOM ---
bom=Counter()
for r in loads:
    kind='ELB' if r.get('kind')=='○' else 'MCCB'
    brk=(r.get('breaker') or '').strip()
    mainp=main_master.get(r.get('main'))
    if mainp:
        for pp in mainp:
            k=pp.get('kind',''); q=pp.get('qty',1)
            if '電動機' in k or k=='M': continue
            if 'MCCB' in k or 'ELB' in k:
                bom[f'{kind} {brk}'.strip()]+=q
            else: bom[k]+=q
    else:
        bom[f'{kind} {brk}'.strip()]+=1  # パターン不明でも主幹遮断器は計上
    ctrlp=ctrl_master.get(str(r.get('ctrl') or ''))
    if ctrlp:
        for pp in ctrlp: bom[pp.get('kind','')]+=pp.get('qty',1)
bom[f'制御盤本体(自立鋼板)']=len(set(r.get('panel') for r in loads))

print("\n=== 動力制御盤 BOM(展開結果) ===")
for k,v in sorted(bom.items(), key=lambda x:-x[1]):
    if k.strip(): print(f"  {v:>4} x {k}")
