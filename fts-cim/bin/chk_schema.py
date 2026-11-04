# -*- coding: utf-8 -*-
"""検算。新しい schema_gen.py が、村上様へお渡し済みの32表を
   1列もずらさず再現できているかを見る。1つでも違えば落とす。

   使い方  python3 chk_schema.py 旧schema_gen.py 新schema_gen.py
"""
import sys, importlib.util, pathlib


def yomu(p, nm):
    spec = importlib.util.spec_from_file_location(nm, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.TABLES


kyu = yomu(sys.argv[1], 'kyu')
shin = yomu(sys.argv[2], 'shin')

ng = []
for t, v in sorted(kyu.items()):
    if t not in shin:
        ng.append('%s が新しい側にありません' % t)
        continue
    d = shin[t]
    if [c[:2] for c in v['cols']] != [c[:2] for c in d['cols']]:
        ng.append('%s の列がちがいます（旧 %d列 / 新 %d列）'
                  % (t, len(v['cols']), len(d['cols'])))
        a = [c[:2] for c in v['cols']]; b = [c[:2] for c in d['cols']]
        for i in range(max(len(a), len(b))):
            x = a[i] if i < len(a) else None
            y = b[i] if i < len(b) else None
            if x != y:
                ng.append('     %3d 旧=%s 新=%s' % (i, x, y))
    for k in ('notnull', 'pk', 'default'):
        if v.get(k) != d.get(k):
            ng.append('%s の %s がちがいます' % (t, k))
    if v.get('src') != d.get('src'):
        ng.append('%s の出どころ（src）がちがいます %r → %r'
                  % (t, v.get('src'), d.get('src')))

print('■ 旧 %d表 %d列 → 新 %d表 %d列'
      % (len(kyu), sum(len(v['cols']) for v in kyu.values()),
         len(shin), sum(len(v['cols']) for v in shin.values())))
if ng:
    print('■ 一致しませんでした %d件' % len(ng))
    for x in ng:
        print('   ' + x)
    sys.exit(1)
print('■ 既存32表はすべて一致しました（回帰ゼロ）')
