# ===== フェーズ2: 属性方式 選定エンジン（候補生成→ルール絞り込み）=====
def R(code,conf,note): return dict(code=code,name=byCode.get(code,{}).get('name','') if code else '',conf=conf,note=note)

def _norm2(s):
    import re as _re
    return _re.sub(r'[()（）.]','',norm(s))

def split_qty_suffix(raw):
    """品名中の「x2 ×2 X2」等を数量として分離"""
    import re as _re
    m=_re.search(r'[xX×]\s*(\d{1,3})\b', str(raw))
    if m: return (str(raw)[:m.start()]+str(raw)[m.end():]).strip(' ,、'), m.group(1)
    return str(raw), None

_OPTION_WORDS=['spd用','ct付','ax付','2e付','1e付','am付','as付','pf付','al付','zct付','sog付','g-ry付',
              'td付','広角','赤針付','spd付','th付','ドアsw付','検付','通信','方向性',
              '引出','電動','手動','コージェネ','標準','高性能','漏電アラーム']
def split_main_opt(name):
    """メイン機器名と付属(オプション)を分離"""
    import re as _re
    s=str(name); options=[]
    for p in _re.findall(r'[（(]([^（）()]*)[）)]', s): options.append(p.strip())
    main_str=_re.sub(r'[（(][^（）()]*[）)]','', s).strip()
    for tok in _re.split(r'[\s\u3000,、]', main_str):
        if tok.endswith('付') and len(tok)>1:
            options.append(tok); main_str=main_str.replace(tok,'').strip()
    nf=norm(s)
    for ow in _OPTION_WORDS:
        if norm(ow) in nf and ow not in [norm(o) for o in options]: options.append(ow)
    return main_str.strip(), options

def _voltband(volt, name):
    n=norm(name)
    if volt in ('HV','400V','200V','100V'): return volt
    if any(k in n for k in ['6kv','6.6kv','7.2kv','12.5']): return 'HV'
    if '415' in n or '440' in n: return '400V'
    if '210' in n or '220' in n: return '200V'
    if '105' in n or '100v' in n: return '100V'
    return ''
def _dbvolt(v):
    if not v: return ''
    if 'kv' in v.lower(): return 'HV'
    for x in ('400','200','100'):
        if x in v: return x+'V'
    return ''
def _get_kw(n):
    import re as _re
    m=_re.search(r'(\d+\.\d+|\d+)\s*kw', n); return m.group(1) if m else None
def _get_kva(n):
    import re as _re
    m=_re.search(r'(\d+\.\d+|\d+)\s*kva[r]?', n); return m.group(1) if m else None

# メイン機器キー (kw, label, prefix一致が必要か)
_MAIN_KEYS=[
 ('t/u','T/U',True),('リモコンsw','リモコンSW',True),
 ('mdf','MDF',True),('端子盤','端子盤',True),
 ('分配器','分配器',True),('増幅器','増幅器',True),('混合器','混合器',True),
 ('uhf','UHFアンテナ',False),('bs-110','BS-110CSアンテナ',False),('アンテナ','アンテナ',False),
 ('インターホン','インターホン',True),('ネットワークカメラ','ネットワークカメラ',False),('カメラ','カメラ',False),
 ('カードリーダー','カードリーダー',True),('tvbox','TV BOX',False),('tv-','TV',False),
 ('自動力率調整','自動力率調整器',True),('自動力率','自動力率調整器',True),('apfc','自動力率調整器',False),
 ('マルチ指示計','マルチ指示計',True),('マルチt/d','マルチT/D',True),
 ('電圧計','電圧計',True),('電流計','電流計',True),('電力計','電力計',True),('力率計','力率計',True),
 ('vm','VM',True),('am','AM',True),('vs','VS',True),('as','AS',True),
 ('whm','WHM',False),('mgs','MGS',True),('pas','PAS',True),('vcb','VCB',True),('vct','VCT',True),
 ('lbs','LBS',True),('vgb','VCB',True),('ds','DS',True),('ch','CH',True),('l-s','L-S',False),('インバータ','INV',False),('inv','INV',False),
 ('ocr','OCR',True),('dgr','DGR',True),('rpr','RPR',True),('ovgr','OVGR',True),
 ('lg-ry','LG-RY',True),('lgr','LGR',True),('zpd','ZPD',True),('zctt','ZCTT',True),('zct','ZCT',True),
 ('vtt','VTT',True),('ctt','CTT',True),
 ('m)lug','M)LUG',True),('m)mcb','M)MCB',True),('m)elb','M)ELB',True),('b)mcb','B)MCB',True),('b)elb','B)ELB',True),
 ('mcb','MCB',True),('elb','ELB',True),('vt','VT',True),('ct','CT',True),('pf','PF',True),('la','LA',True),('sc','SC',True),('sr','SR',True),('tr','TR',True),
 ('fl-10w','FL-10W',True),('pbs','PBS',True),('spd','SPD',True),('mctt','MCTT',True),
 ('換気扇','換気扇',True),('コンセント','コンセント',True),('伝送','伝送',True),
 ('リモコンリレー','R.RY',False),('リモコントランス','R.TR',False),('端子台','TB',True)]

_PARTS = [
    {'name':'T/U','aliases':['t/u'],'prefix':True},
    {'name':'リモコンSW','aliases':['リモコンsw'],'prefix':True},
    {'name':'MDF','aliases':['mdf'],'prefix':True},
    {'name':'端子盤','aliases':['端子盤'],'prefix':True},
    {'name':'分配器','aliases':['分配器'],'prefix':True},
    {'name':'増幅器','aliases':['増幅器'],'prefix':True},
    {'name':'混合器','aliases':['混合器'],'prefix':True},
    {'name':'UHFアンテナ','aliases':['uhf'],'prefix':False},
    {'name':'BS-110CSアンテナ','aliases':['bs-110'],'prefix':False},
    {'name':'アンテナ','aliases':['アンテナ'],'prefix':False},
    {'name':'インターホン','aliases':['インターホン'],'prefix':True},
    {'name':'ネットワークカメラ','aliases':['ネットワークカメラ'],'prefix':False},
    {'name':'カメラ','aliases':['カメラ'],'prefix':False},
    {'name':'カードリーダー','aliases':['カードリーダー'],'prefix':True},
    {'name':'TV BOX','aliases':['tvbox'],'prefix':False},
    {'name':'TV','aliases':['tv-'],'prefix':False},
    {'name':'自動力率調整器','aliases':['自動力率調整', '自動力率', 'apfc'],'prefix':True},
    {'name':'マルチ指示計','aliases':['マルチ指示計'],'prefix':True},
    {'name':'マルチT/D','aliases':['マルチt/d'],'prefix':True},
    {'name':'電圧計','aliases':['電圧計'],'prefix':True},
    {'name':'電流計','aliases':['電流計'],'prefix':True},
    {'name':'電力計','aliases':['電力計'],'prefix':True},
    {'name':'力率計','aliases':['力率計'],'prefix':True},
    {'name':'VM','aliases':['vm'],'prefix':True},
    {'name':'AM','aliases':['am'],'prefix':True},
    {'name':'VS','aliases':['vs'],'prefix':True},
    {'name':'AS','aliases':['as'],'prefix':True},
    {'name':'WHM','aliases':['whm'],'prefix':False},
    {'name':'MGS','aliases':['mgs'],'prefix':True},
    {'name':'PAS','aliases':['pas'],'prefix':True},
    {'name':'VCB','aliases':['vcb', 'vgb'],'prefix':True},
    {'name':'VCT','aliases':['vct'],'prefix':True},
    {'name':'LBS','aliases':['lbs'],'prefix':True},
    {'name':'DS','aliases':['ds'],'prefix':True},
    {'name':'CH','aliases':['ch'],'prefix':True},
    {'name':'L-S','aliases':['l-s'],'prefix':False},
    {'name':'INV','aliases':['インバータ', 'inv'],'prefix':False},
    {'name':'OCR','aliases':['ocr'],'prefix':True},
    {'name':'DGR','aliases':['dgr'],'prefix':True},
    {'name':'RPR','aliases':['rpr'],'prefix':True},
    {'name':'OVGR','aliases':['ovgr'],'prefix':True},
    {'name':'LG-RY','aliases':['lg-ry'],'prefix':True},
    {'name':'LGR','aliases':['lgr'],'prefix':True},
    {'name':'ZPD','aliases':['zpd'],'prefix':True},
    {'name':'ZCTT','aliases':['zctt'],'prefix':True},
    {'name':'ZCT','aliases':['zct'],'prefix':True},
    {'name':'VTT','aliases':['vtt'],'prefix':True},
    {'name':'CTT','aliases':['ctt'],'prefix':True},
    {'name':'M)LUG','aliases':['m)lug'],'prefix':True},
    {'name':'M)MCB','aliases':['m)mcb'],'prefix':True},
    {'name':'M)ELB','aliases':['m)elb'],'prefix':True},
    {'name':'B)MCB','aliases':['b)mcb'],'prefix':True},
    {'name':'B)ELB','aliases':['b)elb'],'prefix':True},
    {'name':'MCB','aliases':['mcb'],'prefix':True},
    {'name':'ELB','aliases':['elb'],'prefix':True},
    {'name':'VT','aliases':['vt'],'prefix':True},
    {'name':'CT','aliases':['ct'],'prefix':True},
    {'name':'PF','aliases':['pf'],'prefix':True},
    {'name':'LA','aliases':['la'],'prefix':True},
    {'name':'SC','aliases':['sc'],'prefix':True},
    {'name':'SR','aliases':['sr'],'prefix':True},
    {'name':'TR','aliases':['tr'],'prefix':True},
    {'name':'FL-10W','aliases':['fl-10w'],'prefix':True},
    {'name':'PBS','aliases':['pbs'],'prefix':True},
    {'name':'SPD','aliases':['spd'],'prefix':True},
    {'name':'MCTT','aliases':['mctt'],'prefix':True},
    {'name':'換気扇','aliases':['換気扇'],'prefix':True},
    {'name':'コンセント','aliases':['コンセント'],'prefix':True},
    {'name':'伝送','aliases':['伝送'],'prefix':True},
    {'name':'R.RY','aliases':['リモコンリレー'],'prefix':False},
    {'name':'R.TR','aliases':['リモコントランス'],'prefix':False},
    {'name':'TB','aliases':['端子台'],'prefix':True},
]

def _detect_main(main_str):
    import re as _re
    n=norm(main_str)
    # 最優先: B)/M) 接頭辞付きの遮断器(MCB/ELB/MCCB/ELCB/LUG)は、負荷名称より先に判定
    m=_re.match(r'(b\)|m\))?(mccb|mcb|elcb|elb|lug)', n)
    if m:
        kind=m.group(2)
        label={'mccb':'MCB','mcb':'MCB','elcb':'ELB','elb':'ELB','lug':'LUG'}[kind]
        pre=(m.group(1) or '')
        return kind, (pre.upper()+label if pre else label), True
    # 部品→別名リストで照合（同じ部品の英語/日本語/略称をまとめて判定）
    for part in _PARTS:
        for alias in part['aliases']:
            nk=norm(alias)
            if len(nk)<=2:
                # 短い別名(as/vs/am/vm/ct/vt等)は単語境界でのみマッチ
                if _re.search(r'(?<![a-z])'+_re.escape(nk)+r'(?![a-z])', n):
                    return alias, part['name'], part['prefix']
            else:
                if nk in n:
                    return alias, part['name'], part['prefix']
    return None,None,False

def _set_code(name, vb):
    """L-S/INVのセットコード(電圧別 200V=22系/400V=26系)を返す"""
    n=norm(name)
    is400=(vb=='400V'); pfx='26' if is400 else '22'; vl='400V' if is400 else '200V'
    sk=('支給' in str(name))
    if ('l-s' in n) or ('ls' in n and 'kw' in n):
        kw=_get_kw(n)
        suf={'2.2':'000','3.7':'001','5.5':'011','7.5':'021','11':'031','15':'041','18.5':'061','22':'071','30':'081','37':'091','45':'111','55':'121','75':'131'}
        if kw in suf:
            code=pfx+suf[kw]
            if code in byCode: return code,f'L-Sセット{kw} {vl}(構成品内包)'
            return '',f'L-S {kw} {vl}・該当コード要確認'
    if ('インバータ' in str(name)) or ('inv' in n and 'mcb' not in n and '盤' not in str(name)):
        kw=_get_kw(n)
        suf={'0.75':'993','1.5':'983','2.2':'053','3.7':'003','5.5':'013','7.5':'023','11':'033','15':'043','18.5':'063','22':'073','30':'083','37':'093','45':'113','55':'123','75':'133','90':'143'}
        if kw in suf:
            s=suf[kw]
            if sk: s=s[:-1]+'5'
            code=pfx+s
            if code in byCode: return code,f'INVセット{kw} {vl}{"(支給)" if sk else ""}(構成品内包)'
            return '',f'INV {kw} {vl}・該当コード要確認'
    return None,None

def gen_candidates(name, volt='', panel=''):
    name=re.sub(r'(?i)vgb','VCB',str(name))  # VGBはVCBの別表記
    """属性で候補を生成。戻り: (meta, [DB行,...])"""
    import re as _re
    qname,qty=split_qty_suffix(name)
    vb=_voltband(volt,qname)
    main_str,opts=split_main_opt(qname)
    main_kw,main_label,prefix=_detect_main(main_str)
    n=norm(qname)
    kw=_get_kw(n); kva=_get_kva(n)
    mr=_re.search(r'(\d+)/5a',n); ratio=mr.group(1) if mr else None
    af=_re.search(r'(\d+)af',n); af=af.group(1) if af else None
    dvals=attr_value_set(qname, '')
    meta=dict(qty=qty,main=main_label,main_kw=main_kw,opts=opts,vb=vb,kw=kw,kva=kva,ratio=ratio,af=af,dvals=dvals)
    out=[]
    for d in db:
        dn=norm(d['name'])
        if main_kw:
            if prefix:
                if not dn.startswith(norm(main_kw)): continue
            else:
                if norm(main_kw) not in dn: continue
        sc=10
        dvb=_dbvolt(d['volt'])
        if vb and dvb:
            if vb==dvb: sc+=6
            else: continue
        # 値ベース属性照合（極数・容量・AF・変流比・電圧を値で突合）
        vsc,_cv=value_score(dvals, d)
        sc+=vsc
        sc+=option_bonus(opts, d['name'], qname)
        # 図面にオプション語が無い場合は「標準/既定」を優先（特殊型を後ろへ）
        if not opts and any(k in d['name'] for k in ['スペース','(SP)','コージェネ','高性能']):
            sc-=3
        out.append((sc,d))
    out.sort(key=lambda x:-x[0])
    return meta,[d for s,d in out[:8]]

# 第2段: 候補をルールで1つに絞り、信頼度を付ける
def refine(meta, cands, name, panel, prev_is_main=False, volt=''):
    n=norm(name); vb=meta['vb']
    H=lambda *xs: all(norm(x) in n for x in xs)

    # --- 会社確認: TB文脈判定 ---
    is_tb=(meta['main']=='TB')
    if is_tb:
        if prev_is_main:
            amp=_get_amp(n)
            ctrl=any(k in norm(panel) for k in ['制御','動力','pac','m1','m2','m3','m4'])
            if ctrl:
                cmap={'50':'50901','100':'50902','200':'50903','225':'50903','400':'50904'}
                if amp in cmap and cmap[amp] in byCode: return R(cmap[amp],'○','主幹用TB(制御盤二次TB)単独計上(会社確認)')
            mtb={'50':'68509','100':'68109','225':'68209','200':'68209','400':'68409','600':'68609'}
            if amp in mtb and mtb[amp] in byCode: return R(mtb[amp],'○','主幹用TB(M)TB)単独計上(会社確認)')
            return R('','△','主幹用TB 単独計上・容量要確認(会社確認)')
        else:
            return R('','○','分岐B)系のTBは本体内包・計上せず(会社確認)')

    # --- SPD用MCCB: 容量はSPDの種類で決まる。容量表記が無ければ△(要確認)で目安提示 ---
    if 'spd用' in n and ('mcb' in n or 'mccb' in n):
        af,at=_amp_af(n)
        if not af:
            cls='クラスII' if re.search(r'ii|2', n) else ''
            return R('','△','SPD用MCCB 容量表記なし→要確認(目安: 低圧分電盤クラスII=2P/3P 20〜50A, 主幹近く=3P 50〜100A)')
        # 容量表記があれば通常のMCBとして選定(下のMCBロジックへ)

    # --- 主幹/分岐 MCB・ELB（MCBが主部品。M)=主幹, B)=分岐）---
    if meta['main'] in ('M)MCB','M)LUG','M)ELB','B)MCB','B)ELB','MCB','ELB'):
        code=_mcb_code(name, panel, meta)
        if code: return R(code,'◎' if code in byCode else '△', _mcb_note(name,panel))
        return R('','△','MCB/ELB 容量・盤種別要確認')
    # --- セット系(L-S/INV) 最優先 ---
    sc_code,sc_note=_set_code(name,vb)
    if sc_code: return R(sc_code,'△',sc_note)
    if sc_code=='' and sc_note: return R('','△',sc_note)

    # --- 高圧/低圧CT 変流比判定 ---
    if meta['main']=='CT' and meta['ratio']:
        r=int(meta['ratio'])
        if vb=='HV':
            code='44121' if r<=40 else '44122' if r<=75 else '44123' if r<=200 else ''
            if code: return R(code,'○',f'高圧CT 変流比{meta["ratio"]}/5A')
            return R('','△','高圧CT 変流比範囲外・要確認')
        if vb in('200V','400V','100V'):
            lv={'10':'72000','15':'72001','100':'72002','200':'72003','300':'72004','400':'72005','500':'72006','600':'72007'}
            if meta['ratio'] in lv and lv[meta['ratio']] in byCode: return R(lv[meta['ratio']],'○',f'低圧CT {meta["ratio"]}/5A')
        return R('','△','CT 電圧帯不明・要確認')

    # --- メイン機器が辞書で特定できない場合は△（誤った確定を出さない）---
    if not meta.get('main'):
        if cands:
            return R(cands[0]['code'],'△',f'機器未特定・候補{len(cands)}件から要確認')
        return R('','△','該当機器が辞書に無い・要確認')
    # --- 候補数で信頼度を決定 ---
    if not cands:
        return R('','△','該当コードなし・要確認')
    if len(cands)==1:
        return R(cands[0]['code'],'◎','属性一致(単一候補)')
    # 複数候補の最終判定
    top=cands[0]
    dvals=meta.get('dvals') or set()
    opts=meta.get('opts') or []
    tv=attr_value_set(top['name'], top.get('volt',''))
    matched=dvals & tv
    missing=dvals - tv
    # CT範囲を考慮（変流比のmissは範囲内なら解消）
    ratio_miss=[v for v in missing if v.endswith('/5A')]
    if ratio_miss and _ct_range_match(dvals, top['name']):
        missing=missing-set(ratio_miss)
    # 極数(P)のmissは、DB品名に極数表記が無い機器(PAS/VT/DGR等)では不問にする
    if any(m.endswith('P') for m in missing) and not re.search(r'[234]P', top['name']):
        missing={m for m in missing if not m.endswith('P')}
    # 電圧の具体値(100V/200V等)は、TR等で結線仕様にすぎない場合 missから除外
    # (相数φとKVAが一致していれば確定とみなす)
    if any(m.endswith('φ') for m in matched) and any(m.endswith('KVA') for m in matched):
        missing={m for m in missing if not (m.endswith('V'))}
    # オプション語がトップに一致しているか
    opt_hit = any(norm(o) and norm(o) in norm(top['name']) for o in opts)
    # 単一候補→◎
    if len(cands)==1:
        return R(top['code'],'◎','単一候補で確定')
    # 2位とのスコア差が大きい/オプション一致/属性過不足なし → ○
    if not missing and matched:
        return R(top['code'],'○',f'属性値一致({len(matched)}項)')
    if opt_hit and not missing:
        return R(top['code'],'○','オプション語一致で絞込')
    # 既定型(静止型/標準/手動)がトップで、図面に特別仕様の記述が無ければ○
    if not missing and any(k in top['name'] for k in ['静止型','標準','(手動)']):
        return R(top['code'],'○','標準/既定型として確定')
    if missing:
        return R(top['code'],'△',f'属性不足{sorted(missing)}・要確認')
    return R(top['code'],'△',f'候補{len(cands)}件・要確認')

def _get_amp(n):
    import re as _re
    m=_re.search(r'(\d+)a', n); return m.group(1) if m else ''

# 統合: 1機器を選定
def select_one(name, panel='', prev_is_main=False, volt='', symbol='', kw='', group=''):
    # 動力盤: 主回路記号があれば記号方式を最優先
    if symbol:
        shikyu=('支給' in str(name))
        kwv = kw or (_get_kw(norm(name)) or '')
        parts=select_power_symbol(symbol, kwv, volt or '200V', shikyu)
        # 主部品(1つ目)を主選定とし、残りは候補/内訳として保持
        first=parts[0]
        code=first[0]; note=first[2]; qty=first[1]
        conf = '◎' if (code and len(parts)==1) else ('○' if code else '△')
        sel=R(code,conf,f'[動力記号{symbol}] '+note)
        sel['parts']=[{'code':c,'qty':q,'note':nt,'name':byCode.get(c,{}).get('name','') if c else ''} for c,q,nt in parts]
        sel['set_qty']=qty
        return sel
    meta,cands=gen_candidates(name,volt,panel)
    sel=refine(meta,cands,name,panel,prev_is_main,volt)
    sel['candidates']=[{'code':c['code'],'name':c['name'],'volt':c['volt']} for c in cands[:5]]
    # 保護セット: 付属(group有でリレー本体以外)は、親リレーが特定された文脈で
    # コードが一意に決まれば確定度を上げる（ZCT/CT/VT等が単独で△に落ちるのを救う）
    if group and cands:
        my=norm(meta.get('main_kw') or name)
        # 親リレー名と自分が異なる=付属。候補が容量等で1つに絞れていれば○へ
        if norm(group) not in my and sel['conf']=='△' and sel['code']:
            sel['conf']='○'; sel['note']=f'保護セット[{group}]の付属として確定: '+sel['note']
        sel['group']=group
    return sel

def _amp_af(n):
    """AF(フレーム)とAT(トリップ)を抽出。AFを優先的に枠として使う"""
    import re as _re
    af=_re.search(r'(\d+)af', n); at=_re.search(r'(\d+)at', n)
    # 「225af/200at」「3p225/150」等。AFが無ければ最初の数値群
    af_v=af.group(1) if af else None
    at_v=at.group(1) if at else None
    return af_v, at_v

def _pole(n):
    import re as _re
    m=_re.search(r'(\d)p', n)
    return m.group(1) if m else '3'

# AF枠→コード末尾2桁前(代表AF: 50/100/225/400/600/800/1000...)
_AF_KEY={'50':'5','100':'1','225':'2','200':'2','400':'4','600':'6','800':'8','1000':'70','1200':'73','1600':'76'}

def _mcb_code(name, panel, meta):
    n=norm(name); pn=norm(panel)
    is_main = n.startswith('m)') or ('主幹' in name) or (meta.get('main','').startswith('M)'))
    is_elb = ('elb' in n) or (meta.get('main')=='ELB' or meta.get('main')=='M)ELB' or meta.get('main')=='B)ELB')
    af,at=_amp_af(n)
    if not af:
        import re as _re
        m=_re.search(r'(\d{2,4})', n.replace('3p','').replace('2p','').replace('4p',''))
        af=m.group(1) if m else None
    if not af: return ''
    # AF枠の標準化: 標準枠(50/100/225/400/600/800)以外なら、ATを収容する最小枠へ
    STD_AF=[50,100,225,400,600,800,1000,1200,1600,2000,2500,3200]
    try:
        afi=int(af)
        if afi not in STD_AF:
            # ATがあればAT、無ければAF値を収容する最小の標準枠
            base_val=int(at) if at else afi
            af=str(next((s for s in STD_AF if s>=base_val), STD_AF[-1]))
    except: pass
    pole=_pole(n)
    # 盤種別判定: 制御盤=50系 / 分電盤=60系(欠相保護有が標準) / 配電盤=40系
    if any(k in pn for k in ['制御','動力']): kind='ctrl'
    elif any(k in pn for k in ['分電','電灯','ел']): kind='bunden'
    elif any(k in pn for k in ['配電','受電','高圧']): kind='haiden'
    else: kind='bunden'  # 既定は分電盤
    # 主幹MCB 3P
    afmap_main={
      'ctrl':{'50':'50503','100':'50103','225':'50203','200':'50203','400':'50403','600':'50603','800':'50803'},
      # 分電盤主幹は欠相保護無(50系)を既定とする（会社確認: 欠相保護有なら60系）
      'bunden':{'50':'50503','100':'50103','225':'50203','200':'50203','400':'50403','600':'50603'},
      'haiden':{'50':'40503','100':'40103','225':'40203','200':'40203','400':'40403','600':'40603','800':'40803'},
    }
    if is_main and not is_elb and pole=='3':
        code=afmap_main.get(kind,{}).get(af,'')
        if code in byCode: return code
    # 分岐MCB/ELB(B)系): 極数×AF×盤種別で実在コードを探す
    if not is_main:
        for cand in _branch_candidates(pole, af, is_elb, kind):
            if cand in byCode: return cand
    return ''

def _branch_candidates(pole, af, is_elb, kind):
    """極数・AF・盤種別から分岐コード候補を実在順に返す。
    末尾規則: 2P MCB=22/ELB=25, 3P MCB=33/ELB=36, 4P ELB=46。
    AF桁: 50→5,100→1,225→2,400→4,600→6,800→8。盤: 配電40/制御50/分電60。"""
    afdig={'50':'5','100':'1','225':'2','200':'2','400':'4','600':'6','800':'8','30':'3'}.get(af,'')
    if not afdig: return []
    # 盤種別の基番号（複数試す。配電40/分電60/制御50）
    bases = ['40','60','50'] if kind!='ctrl' else ['50','40','60']
    out=[]
    for base in bases:
        if pole=='2':
            if is_elb: out.append(base+afdig+'25')   # 2P ELB 例:50525/60525
            else: out.append(base+afdig+'22')        # 2P MCB 例:40522/60522
        elif pole=='4':
            out.append(base+afdig+'46')              # 4P ELB
        else:  # 3P
            if is_elb: out.append(base+afdig+'36')   # 3P ELB 例:40536
            else: out.append(base+afdig+'33')        # 3P MCB 例:40533
    return out

def _mcb_note(name, panel):
    n=norm(name)
    role='主幹' if (n.startswith('m)') or '主幹' in name) else '分岐'
    typ='ELB' if 'elb' in n else 'MCB'
    return f'{role}{typ}(盤種別・AF枠で選定)'

# ===== 動力盤：主回路記号(A〜L)方式 =====
_LS_SUF={'2.2':'000','3.7':'001','5.5':'011','7.5':'021','11':'031','15':'041','18.5':'061','22':'071','30':'081','37':'091','45':'111','55':'121','75':'131'}
_SD_SUF={'7.5':'022','11':'032','15':'042','18.5':'062','22':'072','30':'082','37':'092','45':'112','55':'122','75':'132'}
_INV_SUF={'0.75':'993','1.5':'983','2.2':'053','3.7':'003','5.5':'013','7.5':'023','11':'033','15':'043','18.5':'063','22':'073','30':'083','37':'093','45':'113','55':'123','75':'133','90':'143'}

def _round_cap(kw, suf):
    caps=sorted([float(k) for k in suf], key=float)
    try: v=float(kw)
    except: return None
    for c in caps:
        if v<=c: return ('%g'%c)
    return ('%g'%caps[-1])

def select_power_symbol(symbol, kw, volt='200V', shikyu=False):
    """主回路記号＋容量＋電圧 → [(code, qty, note),...]"""
    pfx='26' if volt=='400V' else '22'
    sym=(symbol or '').upper().strip()
    def ls():
        kk=_round_cap(kw,_LS_SUF); c=pfx+_LS_SUF.get(kk,'') if kk else ''
        return (c,kk) if c in byCode else ('',kk)
    def sd():
        kk=_round_cap(kw,_SD_SUF); c=pfx+_SD_SUF.get(kk,'') if kk else ''
        return (c,kk) if c in byCode else ('',kk)
    def inv():
        kk=_round_cap(kw,_INV_SUF)
        if not kk: return ('',None)
        s=_INV_SUF.get(kk,'')
        if shikyu and s: s=s[:-1]+'5'
        c=pfx+s
        return (c,kk) if c in byCode else ('',kk)
    if sym in ('A','B'):
        return [('',1,f'記号{sym}:電源供給のみ(モーター制御部なし)')]
    if sym=='C':
        c,kk=ls(); return [(c,1,f'直入L-S {kw}→{kk}kW枠')] if c else [('',1,f'L-S {kw}kW要確認')]
    if sym=='D':
        c,kk=sd(); return [(c,1,f'Y-Δ {kw}→{kk}kW枠')] if c else [('',1,f'Y-Δ {kw}kW要確認')]
    if sym in ('E','G'):
        c,kk=ls(); return [(c,2,f'直入L-S {kw}→{kk}kW ×2(記号{sym})')] if c else [('',2,f'L-S×2要確認')]
    if sym in ('F','H'):
        c,kk=sd(); return [(c,2,f'Y-Δ {kw}→{kk}kW ×2(記号{sym})')] if c else [('',2,f'Y-Δ×2要確認')]
    if sym=='I':
        c,kk=inv(); return [(c,1,f'INV {kw}→{kk}kW')] if c else [('',1,f'INV {kw}kW要確認')]
    if sym=='L':
        c,kk=inv(); return [(c,2,f'INV {kw}→{kk}kW ×2(二重化)')] if c else [('',2,f'INV×2要確認')]
    if sym in ('J','K'):
        ic,ik=inv(); bc,bk=(ls() if sym=='J' else sd())
        out=[]
        out.append((ic,1,f'INV {ik}kW') if ic else ('',1,'INV要確認'))
        out.append((bc,1,f'{"直入" if sym=="J" else "Y-Δ"}バイパス {bk}kW') if bc else ('',1,'バイパス要確認'))
        return out
    return [('',1,f'記号{sym}不明・要確認')]

# ===== 値ベース属性照合（値が種類を語る） =====
def attr_value_set(text, dbvolt=''):
    """テキストから属性値の集合を作る。値そのものが種類(極数/電圧/容量/AF/変流比)を表す。"""
    import re as _re
    n=unicodedata.normalize('NFKC',str(text))
    vals=set()
    for m in _re.findall(r'(\d)P(?![a-zA-Z0-9])', n): vals.add(m+'P')        # 極数(1桁)
    for m in _re.findall(r'(\d{2,3})P(?![a-zA-Z])', n): vals.add(m+'P')       # ポート数(2-3桁:20P/50P/100P)
    for m in _re.findall(r'(\d+)\s*AF', n): vals.add(m+'AF')               # フレーム
    for m in _re.findall(r'(\d+)\s*AT', n): vals.add(m+'AT')               # トリップ
    if _re.search(r'(MCB|ELB|LUG|MCCB)', n):                               # MCB系のA枠
        for m in _re.findall(r'(\d+)\s*A(?![a-zA-Z]|F|T)', n): vals.add(m+'AF')
    if _re.search(r'7\.2kV|6\.6kV|6kV', n, _re.I): vals.add('HV')
    if '415' in n or '440' in n: vals.add('400V')
    if _re.search(r'210|220', n): vals.add('200V')
    if _re.search(r'105|100V', n): vals.add('100V')
    if dbvolt:
        if 'kv' in dbvolt.lower(): vals.add('HV')
        for x in ('400','200','100'):
            if x in dbvolt: vals.add(x+'V')
    for m in _re.findall(r'(\d+\.?\d*)\s*kW', n, _re.I): vals.add(m+'kW')  # 容量
    for m in _re.findall(r'(\d+\.?\d*)\s*kvar', n, _re.I): vals.add(m+'kvar')
    for m in _re.findall(r'(\d+\.?\d*)\s*kVA', n): vals.add(m+'kVA')
    # 変流比（具体値）。範囲(100/5A~200/5A)はDB側で別途判定
    for m in _re.findall(r'(\d+)/5A', n): vals.add(m+'/5A')
    # 高圧機器の定格電流(A)：VCB/DS/LBS/PAS等。kV/kvar/VA/ATと紛れないものだけ
    for m in _re.findall(r'(?<![/\d])(\d{2,4})A(?![A-Za-z]|F|T)', n):
        vals.add(m+'A')
    # kA(遮断容量)・kV
    for m in _re.findall(r'(\d+\.?\d*)kA', n, _re.I): vals.add(m.lower().replace('.0','')+'kA')
    # 弱電の型式・分配数: 2D(2分配)/AMP/MIX 等
    for m in _re.findall(r'(\d)D(?![a-z])', n): vals.add(m+'D')
    if 'amp' in n.lower(): vals.add('AMP')
    if 'mix' in n.lower(): vals.add('MIX')
    # T/U(リモコン制御ユニット)の回路数・型式
    for m in _re.findall(r'(\d+)回路', n): vals.add(m+'回路')
    if '片切' in n: vals.add('片切')
    if '両切' in n: vals.add('両切')
    if '6a' in n.lower(): vals.add('6A')
    if '調光' in n: vals.add('調光')
    if '接点入力' in n: vals.add('接点入力')
    # 変圧器: 相数(1φ/3φ)とKVA容量
    for m in _re.findall(r'([13])[φΦ]', n): vals.add(m+'φ')
    for m in _re.findall(r'(\d+)\s*KVA', n, _re.I): vals.add(m+'KVA')
    # LBSのPFヒューズ定格: PF=30A / PF30A 等
    for m in _re.findall(r'PF[=]?(\d+)A', n, _re.I): vals.add('PF'+m+'A')
    # SPDのクラス: クラスI/II、保護レベルI/II(=クラス)
    if re.search(r'クラスi(?!i)|保護レベルi(?!i)|classi(?!i)', n, _re.I): vals.add('クラスi')
    if re.search(r'クラスii|保護レベルii|classii', n, _re.I): vals.add('クラスii')
    # KA容量(SPD): 25KA/20KA等は既にkAで拾うが大文字KA表記も
    for m in _re.findall(r'(\d+)KA', n): vals.add(m.lower()+'ka')
    return vals

def _ct_range_match(draw_vals, dbname):
    """高圧CTの範囲(20/5A~40/5A)に、図面の具体値(150/5A)が入るか"""
    import re as _re
    mr=_re.search(r'(\d+)/5A\s*[~〜\-]\s*(\d+)/5A', dbname)
    if not mr: return False
    lo,hi=int(mr.group(1)),int(mr.group(2))
    for v in draw_vals:
        m=_re.match(r'(\d+)/5A', v)
        if m and lo<=int(m.group(1))<=hi: return True
    return False

def value_score(draw_vals, d):
    """図面の属性値集合と、DBコードの属性値集合を突き合わせてスコア化"""
    cv=attr_value_set(d['name'], d.get('volt',''))
    if not draw_vals: return 0, cv
    inter=draw_vals & cv
    miss=draw_vals - cv
    score=len(inter)*10
    # CT範囲の特別扱い：図面の変流比がDB範囲に入れば加点（missから変流比を除外）
    ratio_miss=[v for v in miss if v.endswith('/5A')]
    if ratio_miss and _ct_range_match(draw_vals, d['name']):
        score+=10; miss=miss-set(ratio_miss)
    score-=len(miss)*8
    return score, cv

def option_bonus(opts, dbname, draw_name=''):
    """オプション語がDB品名に含まれれば加点。図面に無い特別仕様(引出/SP等)は減点し標準を優先。"""
    n=norm(dbname); dn=norm(draw_name); b=0
    for o in opts:
        on=norm(o)
        if on and on in n: b+=5
    # 特別仕様語: 図面に無いのにDB側が該当なら減点（標準/既定を優先）
    SPECIAL=['引出','スペース','(sp)','ｓｐ','電動','コージェネ','高性能','可逆']
    for sp in SPECIAL:
        if norm(sp) in n and norm(sp) not in dn:
            b-=4
    # 標準系: 図面に特記が無いとき、標準/静止型を優先的に加点
    DEFAULTS=['静止型','標準','(手動)']
    has_special_in_draw = any(norm(s) in dn for s in ['引出','電動','可逆','スペース'])
    if not has_special_in_draw:
        for df in DEFAULTS:
            if norm(df) in n: b+=4
    return b
