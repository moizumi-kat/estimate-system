#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, time, urllib.request, os, sys

def post_file(port, path):
    boundary="----b"+str(int(time.time()*1000))
    fn=os.path.basename(path); body=b""
    body+=("--"+boundary+"\r\n").encode()
    body+=(f'Content-Disposition: form-data; name="file"; filename="up.pdf"\r\n').encode()
    body+=b"Content-Type: application/pdf\r\n\r\n"+open(path,"rb").read()+("\r\n--"+boundary+"--\r\n").encode()
    req=urllib.request.Request(f"http://localhost:{port}/api/extract",data=body,
        headers={"Content-Type":"multipart/form-data; boundary="+boundary})
    return json.loads(urllib.request.urlopen(req,timeout=600).read())

def post_json(port,url,obj):
    req=urllib.request.Request(f"http://localhost:{port}{url}",data=json.dumps(obj).encode(),
        headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req,timeout=600).read())

PDFS=[("受変電","amagasaki.pdf"),("動力制御盤","seigyo.pdf"),
      ("電灯分電盤","bunden.pdf"),("弱電端子盤","jakuden.pdf")]

def noise_metrics(sel):
    rows=[r for p in sel['panels'] for r in p.get('rows',[])]
    uncoded=[r for r in rows if not r.get('code')]
    # 無意味候補: コード未確定なのに候補が付いている件数
    with_cand=[r for r in uncoded if r.get('candidates')]
    # 具体的な既知ノイズtriple
    NOISE={'43461','73000','68721'}
    noise_hits=sum(1 for r in uncoded if NOISE & {c.get('code') for c in r.get('candidates',[])[:3]})
    return len(rows),len(uncoded),len(with_cand),noise_hits

print(f"{'図面':<8}{'総行':>5}{'未確定':>6} | {'旧:候補付':>8}{'旧:ノイズ':>8} | {'新:候補付':>8}{'新:ノイズ':>8}")
print("-"*70)
tot=[0,0,0,0]
for title,pdf in PDFS:
    ex=post_file(8000,pdf)          # 抽出は新サーバで1回だけ
    panels={"panels":ex["panels"]}
    old=post_json(8001,"/api/select",panels)   # 旧ロジック(FIX_B=0)
    new=post_json(8000,"/api/select",panels)   # 新ロジック(FIX_B=1)
    n,u,ow,onoise=noise_metrics(old)
    _,_,nw,nnoise=noise_metrics(new)
    print(f"{title:<8}{n:>5}{u:>6} | {ow:>8}{onoise:>8} | {nw:>8}{nnoise:>8}")
    tot[0]+=ow; tot[1]+=onoise; tot[2]+=nw; tot[3]+=nnoise
print("-"*70)
print(f"{'合計':<8}{'':<11} | {tot[0]:>8}{tot[1]:>8} | {tot[2]:>8}{tot[3]:>8}")
print("\n※候補付=コード未確定なのに候補が出ている件数 / ノイズ=無関係な既知トリプル(避雷器等)を含む件数")
