# -*- coding: utf-8 -*-
"""レプリケーション対象の定義体（村上様よりいただいた DDL）を読む。

   ここは**一字一句変えない**。CAD・営業支援など周辺のソフトが
   同じ定義を見ているため、桁も型も既定値も現行のままにする。
   日本語の項目名だけ、レコードリストから引いて見出し用に添える。
"""
import re, pathlib

T_CREATE = re.compile(r'CREATE\s+TABLE\s+\[dbo\]\.\[(\w+)\]\s*\((.*?)\n\)\s*ON ',
                      re.S | re.I)
T_COL    = re.compile(r'^\s*\[(\w+)\]\s+\[(\w+)\]\s*(\([^)]*\))?\s*(.*?),?\s*$')
T_PK     = re.compile(r'PRIMARY\s+KEY\s+CLUSTERED\s*\((.*?)\)', re.S | re.I)
T_DEF    = re.compile(r'ALTER\s+TABLE\s+\[dbo\]\.\[(\w+)\]\s+ADD\s+DEFAULT\s+'
                      r'(\(.*?\))\s+FOR\s+\[(\w+)\]', re.I)


def _gata(kata, kakko):
    """[numeric](2, 0) → numeric(2,0)"""
    if not kakko:
        return kata
    return kata + re.sub(r'\s+', '', kakko)


def yomu1(path):
    # 定義体は Windows から出ているので cp932。UTF-8 のものも混じりうる
    b = pathlib.Path(path).read_bytes()
    for enc in ('cp932', 'utf-8-sig', 'utf-8'):
        try:
            t = b.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        t = b.decode('cp932', 'replace')
    m = T_CREATE.search(t)
    if not m:
        return None
    hyo, naka = m.group(1).upper(), m.group(2)
    cols, notnull = [], []
    for ln in naka.splitlines():
        if 'CONSTRAINT' in ln.upper() or 'PRIMARY KEY' in ln.upper():
            break
        c = T_COL.match(ln)
        if not c:
            continue
        nm = c.group(1).upper()
        cols.append((nm, _gata(c.group(2).lower(), c.group(3))))
        if 'NOT NULL' in (c.group(4) or '').upper():
            notnull.append(nm)
    pk = []
    p = T_PK.search(naka)
    if p:
        pk = [x.strip('[] \t').split()[0].strip('[]')
              for x in p.group(1).split(',') if x.strip()]
    kitei = {}
    for h, v, c in T_DEF.findall(t):
        if h.upper() == hyo:
            kitei[c.upper()] = v[1:-1] if v.startswith('((') is False and \
                v.count('(') == 1 else v[1:-1]
    return {'hyo': hyo, 'cols': cols, 'notnull': notnull, 'pk': pk,
            'default': kitei}


def yomu(defdir):
    out = {}
    for p in sorted(pathlib.Path(defdir).glob('*.sql')):
        d = yomu1(p)
        if d:
            out[d['hyo']] = d
    return out
