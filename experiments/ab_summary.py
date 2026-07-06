#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, time, urllib.request, os
def post_file(port, path):
    boundary="----b"+str(int(time.time()*1000)); body=b""
    body+=("--"+boundary+"\r\n").encode()
    body+=('Content-Disposition: form-data; name="file"; filename="up.pdf"\r\n').encode()
    body+=b"Content-Type: application/pdf\r\n\r\n"+open(path,"rb").read()+("\r\n--"+boundary+"--\r\n").encode()
    req=urllib.request.Request(f"http://localhost:{port}/api/extract",data=body,
        headers={"Content-Type":"multipart/form-data; boundary="+boundary})
    return json.loads(urllib.request.urlopen(req,timeout=600).read())
def sel(port,panels):
    req=urllib.request.Request(f"http://localhost:{port}/api/select",data=json.dumps({"panels":panels}).encode(),
        headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req,timeout=600).read())
PDFS=[("受変電","amagasaki.pdf"),("動力制御盤","seigyo.pdf"),("電灯分電盤","bunden.pdf"),("弱電端子盤","jakuden.pdf")]
print(f"{'図面':<8}| {'旧 ◎/○/△  確定率':<22}| {'新 ◎/○/△  確定率':<22}")
print("-"*58)
for title,pdf in PDFS:
    ex=post_file(8000,pdf); p=ex["panels"]
    o=sel(8001,p)["summary"]; n=sel(8000,p)["summary"]
    os_=f"{o['ok']}/{o['warn']}/{o['chk']}  {o['rate']}%"
    ns_=f"{n['ok']}/{n['warn']}/{n['chk']}  {n['rate']}%"
    print(f"{title:<8}| {os_:<22}| {ns_:<22}")
