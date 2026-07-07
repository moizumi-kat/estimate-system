#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-高圧セットコード24件 + 11-高圧LBS5件 を新設計で起こす。
 コード符号: 11 d3 d4 d5  (d3=計器[0普通/3広角/6マルチ], d4=盤種+VCB[3受電8KA/4受電12.5KA/7饋電8KA/8饋電12.5KA], d5=操作[1手動/2電動])
 単価=p7古川値。展開=p8「配電盤セットコード内容」。手動=確定 / 電動=p8は"電動引出"表記でp7"電動"と差異ありのため要確認。"""
import json
db = json.load(open('db.json', encoding='utf-8')); idx = {d['code']: d for d in db}

# VCB: (vcb, op) -> code
VCB = {('8KA','手動'):'43001', ('12.5KA','手動'):'43011', ('8KA','電動'):'43003', ('12.5KA','電動'):'43013'}
# 計器種別ごとの受電盤メータ群 / 饋電盤メータ群 (VS/AS=71001/71002共通, マルチ=42082)
MET_JU = {  # 受電盤: VM,AM,IWM,PFM,VS,AS
 '普通':[('42001','VM(普通角)',1),('42010','AM(普通角)',1),('42020','IWM(普通角)',1),('42022','PFM(普通角)',1),('71001','VS',1),('71002','AS',1)],
 '広角':[('42002','VM(広角)',1),('42012','AM(広角)',1),('42030','IWM(広角)',1),('42032','PFM(広角)',1),('71001','VS',1),('71002','AS',1)],
 'マルチ':[('42082','マルチ指示計',1)],
}
MET_KI = {  # 饋電盤: AM,AS
 '普通':[('42010','AM(普通角)',1),('71002','AS',1)],
 '広角':[('42012','AM(広角)',1),('71002','AS',1)],
 'マルチ':[('42082','マルチ指示計',1)],
}

def expand(role, meter, vcb, op):
    P = []
    P.append(('71091','FL-10W(ドアSW付)',1))
    P.append(('43424','DS 7.2kV 3P400A',1))
    if role=='受電':
        P += MET_JU[meter]
    else:
        P += MET_KI[meter]
    P.append(('71056','PL(R,G)',1))
    P.append((VCB[(vcb,op)], 'VCB 7.2kV %s %s' % (vcb, op), 1))
    P.append(('46001','OCR(静止型)',1))   # 11系は非引出=静止型(引出型46000は16系用)
    if role=='受電':
        P.append(('44030','VTT(VT試験端子)',1))
    P.append(('44130','CTT(CT試験端子)',1))
    if role=='受電':
        P.append(('44011?','VT 6kV ×2(VA要確認)',2))
    P.append(('44104?','CT 6kV ×2(変流比=受電容量連動・要確認)',2))
    if op=='電動':
        # p8脚注「※電動時には、CS×1、AUX-RY×4追加」
        P.append(('71021','CS',1))
        P.append(('73000','AUX-RY',4))
    return P

# p7 単価: code -> furukawa
PR = {'11031':615,'11041':682,'11071':489,'11081':556,'11331':655,'11341':722,'11371':495,'11381':562,
      '11631':637,'11641':704,'11671':553,'11681':620,
      '11032':743,'11042':842,'11072':617,'11082':716,'11332':783,'11342':882,'11372':623,'11382':722,
      '11632':765,'11642':864,'11672':681,'11682':780}
D3 = {'普通':0,'広角':3,'マルチ':6}
D4 = {('受電','8KA'):3,('受電','12.5KA'):4,('饋電','8KA'):7,('饋電','12.5KA'):8}
D5 = {'手動':1,'電動':2}

out = []
for meter in ['普通','広角','マルチ']:
    for role in ['受電','饋電']:
        for vcb in ['8KA','12.5KA']:
            for op in ['手動','電動']:
                code = '11%d%d%d' % (D3[meter], D4[(role,vcb)], D5[op])
                out.append({
                    'code':code,
                    'name':'高圧セット %s盤 %s %s %s' % (role, meter, vcb, op),
                    'kind':'配電盤','cat':'高圧セット',
                    'settype':'高圧','role':role+'盤','meter':meter,'vcb':vcb,'op':op,
                    'ax':'','volt':'高圧','shikyu':'','sp':'','af':'','cap':'',
                    'spec':'%s/%s/%s/%s' % (role,meter,vcb,op),
                    'furukawa':str(PR.get(code,'')),
                    'expand':[{'code':c,'name':n,'qty':q} for c,n,q in expand(role,meter,vcb,op)],
                    '_flag_dendou': (op=='電動'),
                })

# 11-高圧LBSセット
LBS = [
 ('11001','高圧LBSセット 普通 受電タイプ',368,'受電'),
 ('11002','高圧LBSセット 普通 饋電タイプ',286,'饋電'),
 ('11005','高圧LBSセット 広角 受電タイプ',380,'受電'),
 ('11006','高圧LBSセット 広角 饋電タイプ',292,'饋電'),
 ('11009','高圧LBSセット エネセーバ',903,'エネセーバ'),
]
for code,name,fk,role in LBS:
    exp=[('71091','FL-10W',1),('43321','LBS 3P200A(PF・AL)',1)]
    if role=='エネセーバ':
        exp=[('?','SC-TRIP',1),('71021','CS',1),('71056','PL(R,G)',1),('73000','AUX-RY',2)]
    out.append({'code':code,'name':name,'kind':'配電盤','cat':'高圧LBSセット',
                'settype':'高圧LBS','role':role,'meter':'','vcb':'','op':'','ax':'','volt':'高圧',
                'shikyu':'','sp':'','af':'','cap':'','spec':name,'furukawa':str(fk),
                'expand':[{'code':c,'name':n,'qty':q} for c,n,q in exp],'_flag_dendou':False})

json.dump(out, open('cand_11set.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
dup=[o['code'] for o in out if o['code'] in idx]
print('生成 %d件 (11系24 + LBS5) -> cand_11set.json' % len(out))
print('既存DB衝突:', dup or 'なし')
# 見本表示
for want in ['11031','11071','11032','11631']:
    o=[x for x in out if x['code']==want][0]
    print('\n■ %s %s 古川%s%s' % (o['code'],o['name'],o['furukawa'],'  ★電動=要確認' if o['_flag_dendou'] else ''))
    for p in o['expand']:
        print('    %-7s x%-3s %s' % (p['code'],p['qty'],p['name']))
