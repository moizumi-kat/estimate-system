# -*- coding: utf-8 -*-
"""積算コード表 p21「50-制御盤主幹、51-(AX付)」等に基づき、AX付(補助接点付)の遮断器コードを
非AXコードから機械生成する。規則: 上2桁の下位を0→1(40→41/50→51/60→61)、name末尾に「(AX付)」、
ax='有'、furukawa(単価)は空欄(既存AXコードの規約に合わせる=積算ソフト側で単価解決)。
対象はMCB/ELB遮断器のみ(LUGは端子で補助接点が無く、実見積書でもAX制御盤で非AXのLUGを使用=対象外)。
既存コードは上書きしない(冪等)。"""
import json, sys

DB='db.json'
def main():
    d=json.load(open(DB, encoding='utf-8'))
    byC={r['code']:r for r in d}
    gen=[]
    for r in d:
        c=r['code']
        if len(c)!=5 or c[:2] not in ('40','50','60') or c[1]!='0': continue
        nm=r.get('name','')
        if not (nm.startswith('M)') or nm.startswith('B)')): continue
        if 'MCB' not in nm and 'ELB' not in nm: continue      # 遮断器のみ(LUG/TB除外)
        if r.get('ax','') not in ('なし','',None): continue    # 既にAXは対象外
        axc=c[0]+'1'+c[2:]
        if axc in byC: continue                                # 既存はスキップ(冪等)
        nr=dict(r)
        nr['code']=axc
        nr['name']=nm+'(AX付)'
        nr['ax']='有'
        nr['furukawa']=''                                      # 単価は積算ソフト側(既存AX規約)
        gen.append(nr)
    if not gen:
        print('生成対象なし(既に全て存在)'); return
    d.extend(gen)
    json.dump(d, open(DB,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
    print('生成 %d件 追加 / 総件数 %d'%(len(gen), len(d)))
    from collections import Counter
    cc=Counter(g['code'][:2] for g in gen)
    for k in sorted(cc): print('  %s系: %d件'%(k,cc[k]))

if __name__=='__main__':
    main()
