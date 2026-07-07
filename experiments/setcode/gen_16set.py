#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""16-段積セットコードを新設計で起こす。
 組合せ: 受電盤=単独 / 二段積=一段積×2 / 三段積=一段積×3 / 母連+一段=母連+一段積 (茂泉様確認済)
 中身=p8 col3(受電)/col4(一段)/col5(母連)/col6(VCS)。VCB=電動引出。
 コード規則: 広角=基準(d3=0), 普通角=+100(d3=1), マルチ=+200(d3=2)。単価は広角のみp7記載。"""
import json
db = json.load(open('db.json', encoding='utf-8')); idx = {d['code']: d for d in db}

VCB = {'8KA':'43004','12.5KA':'43014'}   # 電動引出
VCS = {'電磁':'43101','電磁引出PF':'43103'}  # 200A
MET_JU = {'広角':[('42002','VM(広角)',1),('42012','AM(広角)',1),('42030','IWM(広角)',1),('42032','PFM(広角)',1),('71001','VS',1),('71002','AS',1)],
          '普通':[('42001','VM(普通角)',1),('42010','AM(普通角)',1),('42020','IWM(普通角)',1),('42022','PFM(普通角)',1),('71001','VS',1),('71002','AS',1)],
          'マルチ':[('42082','マルチ指示計',1)]}
MET_KI = {'広角':[('42012','AM(広角)',1),('71002','AS',1)],
          '普通':[('42010','AM(普通角)',1),('71002','AS',1)],
          'マルチ':[('42082','マルチ指示計',1)]}

def unit_juden(meter, vcb):
    P=[('71091','FL-10W',1),('43424','DS 7.2kV 3P400A',1)]
    P+=MET_JU[meter]
    P+=[('71056','PL(R,G)',1),('71021','COS',1),('73220','T-RY',1),('73000','AUX-RY',6),
        (VCB[vcb],'VCB 7.2kV %s 電動引出'%vcb,1),('46000','OCR(引出型)',1),
        ('44030','VTT',1),('44130','CTT',1),('44011?','VT 6kV(要確認)',2),('44104?','CT 6kV(要確認)',2)]
    return P
def unit_ichidan(meter, vcb):
    P=[('71091','FL-10W',1)]
    P+=MET_KI[meter]
    P+=[('71056','PL(R,G)',1),('71021','COS',1),('73220','T-RY',1),('73000','AUX-RY',6),
        (VCB[vcb],'VCB 7.2kV %s 電動引出'%vcb,1),('46000','OCR(引出型)',1),
        ('44130','CTT',1),('44104?','CT 6kV(要確認)',2)]
    return P
def unit_boren(vcb):
    return [('71091','FL-10W',1),('71056','PL(R,G)',1),('71021','COS',1),('73220','T-RY',3),
            ('73000','AUX-RY',12),(VCB[vcb],'VCB 7.2kV %s 電動引出'%vcb,1)]
def unit_vcs(vcs):
    return [('71091','FL-10W',1),('71056','PL(R,G)',1),('71021','COS',1),('73220','T-RY',1),
            ('73000','AUX-RY',4),(VCS[vcs],'VCS 7.2kV 200A %s'%vcs,1)]

def merge(*lists):
    from collections import OrderedDict
    agg=OrderedDict()
    for L in lists:
        for c,n,q in L:
            if c in agg: agg[c]=(agg[c][0],agg[c][1]+q)
            else: agg[c]=(n,q)
    return [(c,v[0],v[1]) for c,v in agg.items()]

# 段構成 -> 展開(広角/vcb指定で計算) ; SP=1段スペース(部品は一段積-1本分, ここでは简明にSP注記)
def compose(kind, meter, vcb):
    if kind=='受電盤': return unit_juden(meter,vcb)
    if kind=='一段積': return unit_ichidan(meter,vcb)
    if kind=='二段積': return merge(unit_ichidan(meter,vcb),unit_ichidan(meter,vcb))
    if kind=='二段積(1SP)': return merge(unit_ichidan(meter,vcb),[('—','スペース1段(機器なし)',0)])
    if kind=='三段積': return merge(*([unit_ichidan(meter,vcb)]*3))
    if kind=='三段積(1SP)': return merge(unit_ichidan(meter,vcb),unit_ichidan(meter,vcb),[('—','スペース1段(機器なし)',0)])
    if kind=='母線連絡': return unit_boren(vcb)
    if kind=='母線連絡+一段積': return merge(unit_boren(vcb),unit_ichidan(meter,vcb))
    return []

# p7 広角 単価: (kind, vcb) -> (基準コード, 古川)
VCBROWS = [
 ('受電盤','8KA','16013',913),('受電盤','12.5KA','16014',1005),
 ('二段積','8KA','16023',1310),('二段積','12.5KA','16024',1494),
 ('二段積(1SP)','8KA','16033',789),('二段積(1SP)','12.5KA','16034',881),
 ('三段積','8KA','16043',1965),('三段積','12.5KA','16044',2241),
 ('三段積(1SP)','8KA','16053',1444),('三段積(1SP)','12.5KA','16054',1628),
 ('一段積','12.5KA','16064',747),('母線連絡','12.5KA','16074',658),('母線連絡+一段積','12.5KA','16084',1390),
]
# VCS (計器種別変種なし)
VCSROWS = [
 ('二段積','電磁','16025',628),('二段積','電磁引出PF','16026',1046),
 ('二段積(1SP)','電磁','16035',400),('二段積(1SP)','電磁引出PF','16036',609),
 ('三段積','電磁引出PF','16046',1569),('三段積(1SP)','電磁引出PF','16056',1132),
 ('一段積','電磁','16065',314),('一段積','電磁引出PF','16066',523),
]
def compose_vcs(kind, vcs):
    u=unit_vcs(vcs)
    if kind=='一段積': return u
    if kind=='二段積': return merge(u,u)
    if kind=='二段積(1SP)': return merge(u,[('—','スペース1段(機器なし)',0)])
    if kind=='三段積': return merge(u,u,u)
    if kind=='三段積(1SP)': return merge(u,u,[('—','スペース1段(機器なし)',0)])
    return u

D3={'広角':0,'普通':1,'マルチ':2}
out=[]
# VCB段積: 広角=単価付, 普通/マルチ=コード生成(単価要算定)
for kind,vcb,base,fk in VCBROWS:
    for meter in ['広角','普通','マルチ']:
        code = base[:2] + str(D3[meter]) + base[3:]   # d3差替え(+100/+200相当)
        exp = compose(kind,meter,vcb)
        out.append({'code':code,'name':'段積セット %s %s VCB%s 電動引出'%(kind,meter,vcb),
            'kind':'配電盤','cat':'段積セット','settype':'段積','role':kind,'meter':meter,'vcb':vcb,'op':'電動引出',
            'ax':'','volt':'高圧','shikyu':'','sp':'','af':'','cap':'','spec':'%s/%s/%s'%(kind,meter,vcb),
            'furukawa':str(fk) if meter=='広角' else '要算定',
            'expand':[{'code':c,'name':n,'qty':q} for c,n,q in exp]})
# VCS段積: 計器変種なし
for kind,vcs,code,fk in VCSROWS:
    exp=compose_vcs(kind,vcs)
    out.append({'code':code,'name':'段積セット %s VCS %s'%(kind,vcs),
        'kind':'配電盤','cat':'段積セット','settype':'段積VCS','role':kind,'meter':'','vcb':'','op':vcs,
        'ax':'','volt':'高圧','shikyu':'','sp':'','af':'','cap':'','spec':'%s/VCS/%s'%(kind,vcs),
        'furukawa':str(fk),'expand':[{'code':c,'name':n,'qty':q} for c,n,q in exp]})

json.dump(out, open('cand_16set.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
dup=[o['code'] for o in out if o['code'] in idx]
print('生成 %d件 -> cand_16set.json' % len(out))
print('既存DB衝突:', dup or 'なし')
print('広角(単価付):', sum(1 for o in out if o['furukawa']!='要算定'), ' / 要算定(普通・マルチ):', sum(1 for o in out if o['furukawa']=='要算定'))
for want in ['16024','16064','16084','16026']:
    o=[x for x in out if x['code']==want][0]
    print('\n■ %s %s 古川%s' % (o['code'],o['name'],o['furukawa']))
    for p in o['expand']:
        print('    %-7s x%-3s %s' % (p['code'],p['qty'],p['name']))
