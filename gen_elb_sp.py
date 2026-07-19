# -*- coding: utf-8 -*-
"""B)ELB スペース(SP)コードを生成。既存の B)MCB スペース と対になる欠落表(積算コード表p34の
「※SPは下2桁に9」規則)。素の標準B)ELB(高性能/電子式/コンパクト除く・極数が4桁目=標準構造)に限定し、
B)MCB SP が存在する(系,極,容量,AX)の組合せだけを対象(=『・のみ』をMCB表のカバレッジで踏襲)。
コード変換=4桁目→9。衝突するコード(400xx系等)は生成しない。既存SP規約に合わせる:
spec='スペース', sp='1', shikyu='1', furukawa=''(単価は積算ソフト側), 名称は(スペース)を(AX付)の前に挿入。"""
import json, re
from collections import Counter

DB='db.json'
def parse(nm):
    m=re.search(r'B\)(MCB|ELB).*?(\d)P\s*(\d+)A', nm)
    return (m.group(2),m.group(3),'(AX付)' in nm) if m else None

def sp_name(base):
    # (スペース)を(AX付)の直前に挿入。AX無しなら末尾に付加。
    if base.endswith('(AX付)'):
        return base[:-len('(AX付)')]+'(スペース)(AX付)'
    return base+'(スペース)'

def main():
    d=json.load(open(DB, encoding='utf-8'))
    byC={r['code']:r for r in d}
    mcb_sp=set()
    for r in d:
        if 'スペース' in r.get('name','') and r['name'].startswith('B)MCB'):
            k=parse(r['name'].replace('(スペース)','').replace('(AX付)','')+('(AX付)' if '(AX付)' in r['name'] else ''))
            k=parse(r['name'].replace('(スペース)',''))
            if k: mcb_sp.add((r['code'][:2],k))
    cand=[]
    for r in d:
        c=r['code']; nm=r.get('name','')
        if not nm.startswith('B)ELB') or 'スペース' in nm: continue
        if len(c)!=5 or c[3] not in ('2','3','4'): continue                  # 標準構造(極数が4桁目)
        if any(w in nm for w in ['高性能','電子式','漏電','コンパクト']): continue    # 素のみ
        k=parse(nm)
        if not k or (c[:2],k) not in mcb_sp: continue
        spc=c[:3]+'9'+c[4]
        if spc in byC: continue
        cand.append((c,spc,r))
    tgt=Counter(x[1] for x in cand)
    gen=[]
    for c,spc,r in cand:
        if tgt[spc]!=1: continue                                            # 衝突は除外
        nr=dict(r); nr['code']=spc; nr['name']=sp_name(r['name'])
        nr['spec']='スペース'; nr['sp']='1'; nr['shikyu']='1'; nr['furukawa']=''
        gen.append(nr)
    if not gen:
        print('生成対象なし'); return
    d.extend(gen)
    json.dump(d, open(DB,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
    print('B)ELB SP 生成 %d件 / 総件数 %d'%(len(gen), len(d)))
    for g in sorted(gen, key=lambda x:x['code']):
        print('  %s %s'%(g['code'], g['name']))

if __name__=='__main__':
    main()
