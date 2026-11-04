# -*- coding: utf-8 -*-
"""405工程が使う「データファイル」を数え、試作環境に作る表の一覧を出す。

  ■ 考え方
    1) 405工程の起動CL から、呼び出し／COPY をたどる（work_map.json の izon）。
    2) たどり着いた COBOL の ASSIGN 句を読む。
         DA-… ＝ ディスク上のデータファイル        → 表にする
         GS-… ＝ 画面・帳票（DSPF／DSPFILE／XU04LP）→ 表にしない
         LP-… ＝ プリンタ                           → 表にしない
       （実ソースで確認した4,375件の内訳。DA 3,954／GS 237＋169／LP 15）
    3) ASSIGN に書かれるのは**論理ファイル名**なので、レコードリストの
       「物理ファイル名論理ファイル名」で物理名に直す。
    4) レコードリストにも定義体にも無いものは、推測せず「定義不明」として残す。

  使い方
    python3 tables.py work_map.json koutei405.json <レコードリスト置き場> <定義体置き場> out.json
"""
import sys, re, json, pathlib, collections
import openpyxl

MAP, KOUTEI, RECDIR, DEFDIR, OUT = sys.argv[1:6]
SRC = pathlib.Path(MAP).parent / 'utf8'

m = json.load(open(MAP))
izon, kind = m['izon'], m['kind']
ko = json.load(open(KOUTEI))

# 村上様ご判定（2026-11-03）で「必要」とされた、工程表に無い入口 17本
HOSOKU = ['BUTSYOR1', 'CB1D1220', 'CD4X0121', 'CD4X0122', 'CD4X012B', 'CD4X012D',
          'CD4Z0091', 'CD4Z0092', 'GENSYOR1', 'JHYSYORI', 'JYUSYORI', 'KETGAMEN',
          'SINSYORI', 'SUIHYCL1', 'SUIHYCL2', 'SZUGAMEN', 'URIGAMEN']

# ASSIGN 句。コメント行（7桁目が *）は読まない
ASSIGN = re.compile(r'\bASSIGN\s+(?:TO\s+)?([A-Z0-9#@$]+(?:-[A-Z0-9#@$]+)*)')

def assigns(t):
    out = []
    for ln in t.splitlines():
        if ln[:7].lstrip().startswith('*') or ln.lstrip().startswith('*'):
            continue
        for x in ASSIGN.findall(ln):
            out.append(x.upper())
    return out

def tojiru(starts):
    mitsuketa, nokori = set(), list(starts)
    while nokori:
        n = nokori.pop()
        if n in mitsuketa or n not in izon:
            continue
        mitsuketa.add(n)
        nokori.extend(izon[n])
    return mitsuketa

start405, nai = [], []
for k in ko:
    n = (k.get('cl') or '').upper().replace('.CL', '').replace('.COB', '')
    if n in izon:
        start405.append(n)
    elif n:
        nai.append(n)
start405 = sorted(set(start405))
todatsu = tojiru(start405 + [x for x in HOSOKU if x in izon])

# ---- 論理名 → 物理名（レコードリスト） ------------------------
ronri, jitsu, nmap = {}, set(), {}
for x in sorted(pathlib.Path(RECDIR).glob('*LIB_rev4.xlsx')):
    wb = openpyxl.load_workbook(x, read_only=True, data_only=True)
    for i, r in enumerate(wb['ファイルレイアウト'].iter_rows(values_only=True)):
        if i and r[2]:
            p = str(r[2]).strip().upper()
            jitsu.add(p)
            if r[1] and p not in nmap:
                nmap[p] = str(r[1]).strip()
    for i, r in enumerate(wb['物理ファイル名論理ファイル名'].iter_rows(values_only=True)):
        if i == 0 or not r or not r[2]:
            continue
        p = str(r[2]).strip().upper()
        for c in r[3:]:
            if c:
                ronri[str(c).strip().upper()] = p

# ---- 定義体（レプリケーション対象）がある物理ファイル ---------
teigi = {p.stem.upper() for p in pathlib.Path(DEFDIR).glob('*.sql')}

# 定義体はあるがレコードリストに無いもの。論理名 → 物理名を補う
#   実ソースの ASSIGN と FD の COPY 名で確かめたもののみ
TEHODOKI = {'AEJKFL1': 'AEJKFP', 'AEJKFL2': 'AEJKFP',
            'CSAGML1': 'CSAGMP',
            'GAIHIYL1': 'GAIHIYFP', 'GAIHIYL2': 'GAIHIYFP'}

hyo, fumei, kanren = set(), collections.Counter(), collections.defaultdict(set)
for n in sorted(todatsu):
    if kind.get(n) != 'COB':
        continue
    f = SRC / (n + '.COB')
    if not f.exists():
        continue
    for a in assigns(f.read_text('utf-8', 'replace')):
        tok = a.split('-')
        if 'GS' in tok or 'LP' in tok:        # 画面・帳票・プリンタ
            continue
        if 'DA' not in tok:
            continue
        name = tok[-1]
        p = TEHODOKI.get(name) or ronri.get(name, name)
        if p in jitsu or p in teigi:
            hyo.add(p); kanren[p].add(n)
        else:
            fumei[name] += 1

print('■ 405工程の起動CL        %d本（ソースが見当たらないもの %d本）'
      % (len(start405), len(set(nai))))
print('■ たどり着くプログラム    %d本（CL %d ／ COBOL %d）'
      % (len(todatsu),
         sum(1 for n in todatsu if kind.get(n) == 'CL'),
         sum(1 for n in todatsu if kind.get(n) == 'COB')))
print()
print('■ 試作環境に作る表        %d表' % len(hyo))
print('     うち定義体のまま（レプリケーション対象） %d表' % len(hyo & teigi))
print('     うち変換仕様書どおりに起こす             %d表' % len(hyo - teigi))
print()
print('■ 定義が見当たらない      %d種' % len(fumei))
for f, c in fumei.most_common():
    print('     %-10s %d回' % (f, c))

json.dump({'start405': start405, 'start_nai': sorted(set(nai)),
           'todatsu': sorted(todatsu),
           'hyo': sorted(hyo),
           'rep': sorted(hyo & teigi), 'conv': sorted(hyo - teigi),
           'nm': {p: nmap.get(p, '') for p in sorted(hyo)},
           'tsukau': {p: sorted(kanren[p]) for p in sorted(hyo)},
           'fumei': dict(fumei)},
          open(OUT, 'w'), ensure_ascii=False, indent=1)
print()
print('書き出し:', OUT)

# ---- 仕分け提案書（村上様ご確認済）との突き合わせ -------------
SHIWAKE = pathlib.Path(RECDIR) / 'shiwake.xlsx'
if SHIWAKE.exists():
    han = {}
    wb = openpyxl.load_workbook(SHIWAKE, read_only=True, data_only=True)
    for i, r in enumerate(wb['② 全ファイル仕分け'].iter_rows(values_only=True)):
        if i == 0 or not r or not r[1]:
            continue
        han[str(r[1]).strip().upper()] = (r[0] or '').strip()
    c = collections.Counter(han.get(p, '仕分けに無し') for p in hyo)
    print()
    print('■ 仕分け提案書（村上様ご確認済）での判定')
    for k, v in c.most_common():
        print('     %-12s %3d表' % (k, v))
    nokosu = sorted(p for p in hyo if han.get(p, '').startswith('残す'))
    youkak = sorted(p for p in hyo if han.get(p) == '要確認')
    nashi  = sorted(p for p in hyo if p not in han)
    print()
    print('■ 作る表（「残す」だけ）  %d表' % len(nokosu))
    print('     定義体のまま %d表 ／ 変換仕様書から %d表'
          % (len(set(nokosu) & teigi), len(set(nokosu) - teigi)))
    print('■ 要確認のまま            %d表 ', youkak)
    print('■ 仕分けに無し            %d表 ', nashi)
    d = json.load(open(OUT))
    d['nokosu'] = nokosu; d['youkakunin'] = youkak; d['shiwake_nashi'] = nashi
    d['hantei'] = {p: han.get(p, '仕分けに無し') for p in sorted(hyo)}
    json.dump(d, open(OUT, 'w'), ensure_ascii=False, indent=1)
