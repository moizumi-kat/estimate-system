#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""受変電(系統図)で 二重Vision+裁定。機器単位で Claude∩Gemini を突合。
一致→◎ / 食い違い→Claude精査で裁定 / 判別不能→△(人)。"""
import app, base64, json, re, os, fitz, difflib
from google import genai
from google.genai import types
GKEY=open('.gemini_key').read().strip(); gclient=genai.Client(api_key=GKEY)
EP=app.EXTRACT_PROMPT

def render(pdf, zoom=3.0):
    doc=fitz.open(pdf); out=[]
    for pg in doc: out.append(pg.get_pixmap(matrix=fitz.Matrix(zoom,zoom)).tobytes('png'))
    return out
def parse(t):
    t=re.sub(r'^```(json)?|```$','',(t or '').strip(),flags=re.M).strip()
    try: return json.loads(t)
    except: mm=re.search(r'\{.*\}',t,re.S); return json.loads(mm.group(0)) if mm else {'panels':[]}
def claude_ex(png):
    return app.extract(app.client(), png, 'image/png')   # EXTRACT_PROMPT使用
def gemini_ex(png):
    img=types.Part.from_bytes(data=png, mime_type='image/png')
    r=gclient.models.generate_content(model='gemini-2.5-pro', contents=[EP,img],
        config=types.GenerateContentConfig(response_mime_type='application/json'))
    d=parse(r.text)
    if isinstance(d, list): d={'panels': d}
    if 'panels' not in d: d={'panels': d.get('panels', [])}
    return d

def dn(s): return re.sub(r'[\s　()（）・]','',str(s or '')).upper()
def sim(a,b): return difflib.SequenceMatcher(None, dn(a), dn(b)).ratio()

def adjudicate(png, panel, desc):
    prompt=(f"この受変電図面(画像)について確認します。盤『{panel}』に関する次の点を、画像を精査して判断してください。\n{desc}\n"
            "正しい内容を1行で答える。画像から判別できない/読めない場合は UNSURE とだけ答える。説明不要。")
    src={'type':'base64','media_type':'image/png','data':base64.standard_b64encode(png).decode()}
    with app.client().messages.stream(model='claude-opus-4-8',max_tokens=200,
        messages=[{'role':'user','content':[{'type':'text','text':prompt},{'type':'image','source':src}]}]) as st:
        m=st.get_final_message()
    ans=''.join(x.text for x in m.content if x.type=='text').strip()
    return None if ('UNSURE' in ans.upper() or not ans) else ans.splitlines()[0].strip()

CACHE='cc_juhen.json'
if os.path.exists(CACHE):
    D=json.load(open(CACHE,encoding='utf-8')); pngs=render('amagasaki.pdf'); print('(読取キャッシュ利用)')
else:
    pngs=render('amagasaki.pdf'); Cp=[]; Gp=[]
    for i,png in enumerate(pngs):
        print(f'p{i+1}: Claude...'); Cp+=claude_ex(png).get('panels',[])
        print(f'p{i+1}: Gemini...'); Gp+=gemini_ex(png).get('panels',[])
    D={'C':Cp,'G':Gp}; json.dump(D, open(CACHE,'w',encoding='utf-8'), ensure_ascii=False)

def is_load(name):
    n=str(name or '')
    if re.match(r'^\s*(L|M|EM)-?\d', n): return True   # 幹線負荷 L-1C/M-1C 等
    if re.match(r'^\s*(EV|荷|直圧|PU-|PU\d)', n): return True
    if any(k in n for k in ['給水加圧','消火栓','散水栓']): return True
    return False
def items(panels):
    out=[]
    for p in panels:
        for it in p.get('items',[]):
            if it.get('load_detail') or it.get('cable'): continue   # 対象外
            nm=it.get('name','')
            if is_load(nm): continue                                 # 負荷明細=計上対象外
            out.append((p.get('panel',''), nm))
    return out
CI=items(D['C']); GI=items(D['G'])
print(f"\nClaude 機器{len(CI)} / Gemini 機器{len(GI)}")
def page_png(): return pngs[0] if pngs else None

# --- 強化した名称マッチ: 機器コード集合＋数値集合の一致で表記ゆれを吸収 ---
ALIAS={'TR':'T','SRX':'SR','VCS':'VMC'}   # 機器コードの別名を揃える
def toks(name):
    n=dn(name)
    codes={ALIAS.get(c,c) for c in re.findall(r'[A-Z]{1,6}',n)}
    return codes, set(re.findall(r'\d+\.?\d*',n))
def score(a,b):
    ca,na=toks(a); cb,nb=toks(b)
    base=difflib.SequenceMatcher(None,dn(a),dn(b)).ratio()
    codej=len(ca&cb)/len(ca|cb) if (ca|cb) else 0
    numj=len(na&nb)/len(na|nb) if (na|nb) else 1.0
    return max(base, 0.6*codej+0.4*numj)

from collections import defaultdict
G_by=defaultdict(list)
for gp,gn in GI: G_by[dn(gp)].append((gp,gn))
gkeys=list(G_by)
def match_panel(cp):
    best=-1;bk=None
    for k in gkeys:
        s=difflib.SequenceMatcher(None,dn(cp),k).ratio()
        if s>best:best=s;bk=k
    return bk if best>=0.5 else None

# --- 裁定キャッシュ(上限撤廃・再実行は無料) ---
AC='adj_cache.json'; adjc=json.load(open(AC,encoding='utf-8')) if os.path.exists(AC) else {}
def adj_cached(panel,name,desc):
    k=f"{dn(panel)}|{dn(name)}"
    if k in adjc: return adjc[k]
    d=adjudicate(page_png(),panel,desc); adjc[k]=d
    json.dump(adjc,open(AC,'w',encoding='utf-8'),ensure_ascii=False); return d

THRESH=0.55; usedG=set(); agree=adj_ok=human=0; log=[]
for cp,cn in CI:
    pk=match_panel(cp); cand=G_by.get(pk,[]) if pk else []
    best=-1;bj=None
    for j,(gp,gn) in enumerate(cand):
        if (pk,j) in usedG: continue
        s=score(cn,gn)
        if s>best:best=s;bj=j
    if bj is not None and best>=THRESH:
        usedG.add((pk,bj)); agree+=1
    else:
        dec=adj_cached(cp,cn,f"Claudeは『{cn}』を検出、もう一方は未検出/表記違いです。この機器は実在しますか。実在するなら正しい機器名を答えてください。")
        if dec is None: human+=1; log.append((cp,cn,'△人(判別不能)'))
        else: adj_ok+=1; log.append((cp,cn,f'裁定→{dec[:22]}'))
for pk in gkeys:                              # Gemini側のみ検出も裁定
    for j,(gp,gn) in enumerate(G_by[pk]):
        if (pk,j) in usedG: continue
        dec=adj_cached(gp,gn,f"Geminiは『{gn}』を検出、もう一方は未検出です。この機器は実在しますか。実在するなら正しい機器名を。")
        if dec is None: human+=1; log.append((gp,gn,'△人(判別不能/G)'))
        else: adj_ok+=1; log.append((gp,gn,f'裁定→{dec[:22]}(G)'))

print(f"\n=== 受変電 二重Vision+裁定(純化版) ===")
print(f"  ◎ 二重一致(両モデル同一機器) : {agree}")
print(f"  ○ Claude裁定で解決           : {adj_ok}")
print(f"  △ 判別不能→人が確認          : {human}")
print("\n--- △(人が確認すべき本当に曖昧なもの) ---")
for cp,cn,r in log:
    if r.startswith('△'): print(f"  [{cp[:12]:<12}] {cn[:30]:<31} {r}")
