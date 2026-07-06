#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, time, urllib.request, os
BASE="http://localhost:8000"
def post_file(path):
    b="----b"+str(int(time.time()*1000)); body=b""
    body+=("--"+b+"\r\n").encode()
    body+=('Content-Disposition: form-data; name="file"; filename="up.pdf"\r\n').encode()
    body+=b"Content-Type: application/pdf\r\n\r\n"+open(path,"rb").read()+("\r\n--"+b+"--\r\n").encode()
    req=urllib.request.Request(BASE+"/api/extract",data=body,headers={"Content-Type":"multipart/form-data; boundary="+b})
    return json.loads(urllib.request.urlopen(req,timeout=600).read())
def sel(panels):
    req=urllib.request.Request(BASE+"/api/select",data=json.dumps({"panels":panels}).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req,timeout=600).read())
FILES=[("受変電設備","amagasaki.pdf"),("動力制御盤","seigyo.pdf"),("電灯分電盤","bunden.pdf"),("弱電端子盤","jakuden.pdf")]
print(f"{'図面':<10}{'盤':>3}{'機器':>5}{'品質':>6}{'不明瞭':>7} | {'◎':>4}{'◉':>4}{'○':>4}{'△':>4}{'確定率':>7}")
print("-"*66)
tot={'ok':0,'uniq':0,'warn':0,'chk':0,'n':0}
for title,pdf in FILES:
    ex=post_file(pdf)
    q=ex.get('quality'); ur=int(ex.get('unclear_ratio',0)*100)
    s=sel(ex['panels'])['summary']
    print(f"{title:<10}{ex['npanels']:>3}{ex['count']:>5}{q:>6}{str(ur)+'%':>7} | {s['ok']:>4}{s.get('uniq',0):>4}{s['warn']:>4}{s['chk']:>4}{str(s['rate'])+'%':>7}")
    for k in ('ok','uniq','warn','chk'): tot[k]+=s[k] if k!='uniq' else s.get('uniq',0)
    tot['n']+=s['total']
print("-"*66)
r=round((tot['ok']+tot['uniq']+tot['warn'])/(tot['n'] or 1)*100)
print(f"{'合計':<10}{'':>8}{'':>13} | {tot['ok']:>4}{tot['uniq']:>4}{tot['warn']:>4}{tot['chk']:>4}{str(r)+'%':>7}")
