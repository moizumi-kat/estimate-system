# -*- coding: utf-8 -*-
"""現行ソースの呼び出し関係を作る（第5版）。

   これまでに直したこと
     第2版 COBOL の CALL "XXXX"（引用符つき）を数える
     第3版 ソース末尾 S は実行形式では O。CL は PROGRAM-ID の名前で書く
     第4版 SBMJOB の PGM- も起動
     第5版 **COPY でプログラム本体を取り込む形も依存として数える**（COPY … OF FZSLIB）
     ・THEN CALL のように、CALL が行頭でない形
     ・CL にプログラム名をそのまま書く形（CALL を書かない）
           （村上様ご指摘 2026-11-03）
"""
import sys, re, json, pathlib, collections
U = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])

src = {f.stem.upper(): f for f in list(U.glob('*.CL')) + list(U.glob('*.COB'))}
kind = {n: f.suffix.upper().lstrip('.') for n, f in src.items()}
txt  = {n: f.read_text('utf-8', 'replace') for n, f in src.items()}

PID      = re.compile(r'^\s*PROGRAM-ID\.\s*([A-Z0-9#@$\-]+)\s*\.', re.M | re.I)
# CALL は行頭とは限らない（例 THEN CALL CD4X0121）
CL_CALL  = re.compile(r'\bCALL\s+([A-Z0-9#@$]+)')
# ASP の CL は、プログラム名をそのままコマンドとして書く形でも起動する
#   例  SUIHYCL1 YY-?YEAR,MM-?TUKI   ／   CB1D1220 KUBUN-1,DENNO1-00000
BARE     = re.compile(r'^\s+([A-Z0-9#@$]{4,8})(?=[\s,]|$)', re.M)
SBM      = re.compile(r'PGM-([A-Z0-9#@$]+)')
COB_CALL = re.compile(r'CALL\s+["\']([A-Z0-9#@$\-]+)["\']')
COPY     = re.compile(r'^\s*COPY\s+([A-Z0-9#@$\-]+)(?:\s+OF\s+([A-Z0-9]+))?', re.M | re.I)
SEL      = re.compile(r'ASSIGN\s+TO\s+(?:[0-9]{2}-)?(?:[A-Z]{2}-)?(?:[0-9]{2}-)?(?:[A-Z]{2}-)?([A-Z0-9]+)')

# 実行形式の名前 → ソースの名前（末尾 S→O 等）
jikkou = {n: n for n in src}
for n in src:
    if kind[n] == 'COB':
        m = PID.search(txt[n])
        if m:
            jikkou[m.group(1).upper()] = n

yobu, copy_de, tukau = {}, {}, {}
pat = collections.Counter()
for n in src:
    t = txt[n]
    if kind[n] == 'CL':
        c = CL_CALL.findall(t) + SBM.findall(t) + BARE.findall(t)
    else:
        c = COB_CALL.findall(t) + CL_CALL.findall(t) + SBM.findall(t)
    out = set()
    for x in c:
        y = jikkou.get(x.upper())
        if y and y != n:
            out.add(y); pat['%s⇒%s' % (kind[n], kind[y])] += 1
    yobu[n] = sorted(out)

    # COPY でプログラム本体を取り込む形（ライブラリ指定なしのものが該当）
    cp = set()
    for m, lib in COPY.findall(t):
        y = jikkou.get(m.upper())
        if y and y != n and lib.upper() in ('', 'FZSLIB'):   # XFDLIB=記述・XMDLIB=画面は除く
            cp.add(y); pat['%s⊂%s（COPY）' % (kind[n], kind[y])] += 1
    copy_de[n] = sorted(cp)

    tukau[n] = sorted(set(SEL.findall(t))) if kind[n] == 'COB' else []

# 呼び出し＋COPY をあわせた依存
izon = {n: sorted(set(yobu[n]) | set(copy_de[n])) for n in src}
json.dump({'yobu': yobu, 'copy': copy_de, 'izon': izon, 'tukau': tukau,
           'kind': kind, 'jikkou': jikkou},
          open(OUT, 'w'), ensure_ascii=False)
print('■ 依存の内訳（のべ）')
for k, v in pat.most_common():
    print('   %-18s %5d回' % (k.replace('COB', 'COBOL'), v))
print()
print('COPY でプログラムを取り込んでいる本数:', sum(1 for v in copy_de.values() if v))
