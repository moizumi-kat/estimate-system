#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""二重Vision + 裁定: Claude∩Gemini。一致→◎ / 不一致→Claudeが精査して裁定 / 判別不能→△。
制御盤の全適用表(p1右+p2左+p2右=9盤)で実行。"""
import app, base64, json, re, os
from cross_check import crop, claude_read, reconcile, TAB_P, key_of, FIELDS, field_eq
from google import genai
from google.genai import types

GKEY=open('.gemini_key').read().strip(); gclient=genai.Client(api_key=GKEY)
def gemini_read(png):
    img=types.Part.from_bytes(data=png, mime_type='image/png')
    r=gclient.models.generate_content(model='gemini-2.5-pro', contents=[TAB_P,img],
        config=types.GenerateContentConfig(response_mime_type='application/json'))
    t=(r.text or '').strip()
    try: return json.loads(t).get('rows',[])
    except: mm=re.search(r'\{.*\}',t,re.S); return json.loads(mm.group(0)).get('rows',[]) if mm else []

CROPS=[('p1',0,0.47,1.0,0.05,0.72),('p2L',1,0.02,0.50,0.05,0.95),('p2R',1,0.50,1.0,0.05,0.95)]

def adjudicate(png, panel, load_no, field, a, b):
    """不一致をClaudeが精査。正しい値を返す。判別不能はNone。"""
    prompt=(f"この動力制御盤の適用表(画像)で、盤『{panel}』の負荷『{load_no}』の項目『{field}』について、"
            f"読み①『{a}』と読み②『{b}』のどちらが正しいか、画像を精査して判断してください。"
            f"正しい方の値だけを1行で答える。どちらとも判別できない/読めない場合は UNSURE とだけ答える。説明不要。")
    src={'type':'base64','media_type':'image/png','data':base64.standard_b64encode(png).decode()}
    with app.client().messages.stream(model='claude-opus-4-8',max_tokens=200,
        messages=[{'role':'user','content':[{'type':'text','text':prompt},{'type':'image','source':src}]}]) as st:
        m=st.get_final_message()
    ans=''.join(x.text for x in m.content if x.type=='text').strip()
    if 'UNSURE' in ans.upper() or not ans: return None
    return ans.splitlines()[0].strip()

# --- 読み取り(キャッシュ) ---
CACHE='cc_full.json'
if os.path.exists(CACHE):
    data=json.load(open(CACHE,encoding='utf-8')); print('(読取キャッシュ利用)')
else:
    data={}
    for tag,pi,x0,x1,y0,y1 in CROPS:
        png=crop('seigyo.pdf',pi,x0,x1,y0,y1)
        print(f'{tag}: Claude...'); C=claude_read(png,tag)
        print(f'{tag}: Gemini...'); G=gemini_read(png)
        data[tag]={'C':C,'G':G}
    json.dump(data, open(CACHE,'w',encoding='utf-8'), ensure_ascii=False)

# --- 突合 + 裁定 ---
tot_ok=tot_adj=tot_human=0; adj_log=[]
for tag,pi,x0,x1,y0,y1 in CROPS:
    C=data[tag]['C']; G=data[tag]['G']
    res=reconcile(C,G)
    png=None
    for conf,a,b,diffs in res:
        if conf=='◎': tot_ok+=1; continue
        who=(a or b); panel=who.get('panel',''); ln=who.get('load_no','') or who.get('load','')
        if a and b:   # 両方に在るが値が食い違い → フィールド単位で裁定
            if png is None: png=crop('seigyo.pdf',pi,x0,x1,y0,y1)
            resolved=True
            for f in FIELDS:
                if not field_eq(f,a,b):
                    dec=adjudicate(png,panel,ln,f,a.get(f),b.get(f))
                    if dec is None: resolved=False; adj_log.append((panel,ln,f,a.get(f),b.get(f),'△人'));
                    else: adj_log.append((panel,ln,f,a.get(f),b.get(f),f'裁定→{dec}'))
            if resolved: tot_adj+=1
            else: tot_human+=1
        else:         # 片側のみ検出 → 存在自体を裁定
            if png is None: png=crop('seigyo.pdf',pi,x0,x1,y0,y1)
            dec=adjudicate(png,panel,ln,'この行の存在',(a.get('load') if a else '(なし)'),(b.get('load') if b else '(なし)'))
            if dec is None: tot_human+=1; adj_log.append((panel,ln,'存在','C側' if a else '—','G側' if b else '—','△人'))
            else: tot_adj+=1; adj_log.append((panel,ln,'存在','C' if a else '-','G' if b else '-',f'裁定→{dec[:20]}'))

print(f"\n=== 二重Vision+裁定 結果(制御盤 全適用表) ===")
print(f"  ◎ 二重一致(即確定)   : {tot_ok}")
print(f"  ○ Claude裁定で解決    : {tot_adj}")
print(f"  △ 判別不能(人が確認)  : {tot_human}")
if adj_log:
    print("\n--- 裁定ログ ---")
    for panel,ln,f,a,b,r in adj_log[:20]:
        print(f"  {panel:<5}{str(ln)[:12]:<13}{f:<8} C『{a}』/ G『{b}』 → {r}")
