#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, json, time, urllib.request, urllib.parse, mimetypes, os
BASE="http://localhost:8000"

def post_file(path):
    boundary="----b"+str(int(time.time()))
    fn=os.path.basename(path)
    body=b""
    body+=("--"+boundary+"\r\n").encode()
    body+=(f'Content-Disposition: form-data; name="file"; filename="{fn}"\r\n').encode()
    body+=b"Content-Type: application/pdf\r\n\r\n"
    body+=open(path,"rb").read()
    body+=("\r\n--"+boundary+"--\r\n").encode()
    req=urllib.request.Request(BASE+"/api/extract",data=body,
        headers={"Content-Type":"multipart/form-data; boundary="+boundary})
    with urllib.request.urlopen(req,timeout=600) as r:
        return json.loads(r.read())

def post_json(url,obj):
    data=json.dumps(obj).encode()
    req=urllib.request.Request(BASE+url,data=data,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=600) as r:
        return r.read()

label=sys.argv[1]; pdf=sys.argv[2]; out=sys.argv[3]
t0=time.time()
ex=post_file(pdf)
if 'error' in ex:
    print(f"[{label}] 抽出ERROR: {ex['error']}"); sys.exit(1)
t1=time.time()
sel=json.loads(post_json("/api/select",{"panels":ex["panels"]}))
if 'error' in sel:
    print(f"[{label}] 選定ERROR: {sel['error']}"); sys.exit(1)
t2=time.time()
xls=post_json("/api/excel",{"panels":sel["panels"]})
open(out,"wb").write(xls)
t3=time.time()
s=sel.get("summary",{})
print(f"[{label}] 盤{ex.get('npanels')} 機器{ex.get('count')} | "
      f"合計{s.get('total')} ◎{s.get('ok')} ○{s.get('warn')} △{s.get('chk')} 確定率{s.get('rate')}% | "
      f"抽出{t1-t0:.0f}s 選定{t2-t1:.0f}s Excel{t3-t2:.0f}s -> {out}")
# 盤ごとの内訳を保存
json.dump(sel, open(out+".json","w",encoding="utf-8"), ensure_ascii=False)
if ex.get("warnings"): print(f"      warnings: {ex['warnings']}")
