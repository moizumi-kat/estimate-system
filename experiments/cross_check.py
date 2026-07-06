#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""二重Visionクロスチェック突合エンジンの実証。
2つの独立抽出(将来: Claude と Gemini)を突き合わせ、一致→◎/不一致→△。
今回は仕組み検証のため Claude で2回読み(A/B)して突合する。"""
import app, base64, json, re, fitz, sys

def crop(pdf,pi,x0,x1,y0,y1,target=2000):
    doc=fitz.open(pdf); pg=doc[pi]; r=pg.rect
    clip=fitz.Rect(r.width*x0,r.height*y0,r.width*x1,r.height*y1)
    z=target/max(clip.width,clip.height)
    return pg.get_pixmap(matrix=fitz.Matrix(z,z),clip=clip).tobytes('png')

TAB_P='''動力制御盤の「適用表(リスト)」。各負荷行を抽出。
列: panel(制御盤名M-1A等,繰返), load_no, load, kw, kind(●/○/空), main(主回路A〜L), ctrl(操作1〜11), breaker(3P xxxAF/xxxAT)。
予備行も load='予備'。出力 {"rows":[...]} のみ。'''

def claude_read(png, tag):
    src={'type':'base64','media_type':'image/png','data':base64.standard_b64encode(png).decode()}
    with app.client().messages.stream(model='claude-opus-4-8',max_tokens=16000,
        messages=[{'role':'user','content':[{'type':'text','text':TAB_P},{'type':'image','source':src}]}]) as st:
        m=st.get_final_message()
    t=''.join(b.text for b in m.content if b.type=='text').strip(); t=re.sub(r'^```(json)?|```$','',t,flags=re.M).strip()
    try: return json.loads(t).get('rows',[])
    except: mm=re.search(r'\{.*\}',t,re.S); return (json.loads(mm.group(0)).get('rows',[]) if mm else [])

# TODO: gemini_read(png) を実装(要 Gemini APIキー)。同じ{"rows":[...]}を返せば下の突合はそのまま使える。

def norm(v): return re.sub(r'\s','',str(v or '').replace('KW','').replace('kW','')).upper()
def key_of(r):
    ln=norm(r.get('load_no'))
    return (norm(r.get('panel')), ln if ln else norm(r.get('load'))[:6])

def numeq(a,b):
    """数値フィールドは 3.7==3.70 として比較。数値化できなければ文字列比較。"""
    try: return abs(float(re.sub(r'[^\d.]','',str(a) or '0') or 0)-float(re.sub(r'[^\d.]','',str(b) or '0') or 0))<1e-6
    except: return norm(a)==norm(b)

FIELDS=['kw','kind','main','ctrl','breaker']
def field_eq(f,a,b):
    if f=='kw': return numeq(a.get(f), b.get(f))
    return norm(a.get(f))==norm(b.get(f))
def reconcile(A, B):
    """2抽出を(panel,load_no,load)で対応付け、各フィールド一致で◎/不一致で△。"""
    from collections import defaultdict
    ib=defaultdict(list)
    for r in B: ib[key_of(r)].append(r)
    used=set(); out=[]
    for a in A:
        k=key_of(a); cand=ib.get(k,[])
        b=None
        for i,x in enumerate(cand):
            if (k,i) not in used: b=x; used.add((k,i)); break
        if b is None:
            out.append(('△', a, None, ['片側のみ検出(B欠)'])); continue
        diffs=[f'{f}:{a.get(f)}≠{b.get(f)}' for f in FIELDS if not field_eq(f,a,b)]
        out.append(('◎' if not diffs else '△', a, b, diffs))
    # Bにしか無い行
    for k,cand in ib.items():
        for i,x in enumerate(cand):
            if (k,i) not in used: out.append(('△', None, x, ['片側のみ検出(A欠)']))
    return out

if __name__=='__main__':
    png=crop('seigyo.pdf',0,0.47,1.0,0.05,0.72)
    A=claude_read(png,'A'); B=claude_read(png,'B')
    print(f"抽出A {len(A)}行 / 抽出B {len(B)}行")
    res=reconcile(A,B)
    ok=sum(1 for c,_,_,_ in res if c=='◎'); ng=len(res)-ok
    print(f"突合: ◎一致 {ok} / △要確認 {ng} / 一致率 {round(ok/(len(res) or 1)*100)}%\n")
    print("--- 不一致(要確認)の例 ---")
    for c,a,b,d in res:
        if c=='△':
            who=(a or b); print(f"  △ {who.get('panel',''):<6}{who.get('load_no',''):<10} {' , '.join(d)[:60]}")
