# -*- coding: utf-8 -*-
"""工程11-1（見積登録）の移植が、現行の COBOL どおりに動くかを見る。

画面定義（XMDLIB）が未入手のため、子6本は**呼ばれたことを記録するだけの代役**に
差し替えて、A10010PO.COB の制御そのものを確かめる。
1つでも合わなければ異常終了する。

  使い方   py -3.13 点検\\chk_koutei11_1.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

from cim.cobol.a10010po import (A10010PO, Abend, GAMEN,
                                OPEN_IO, OPEN_INPUT, OPEN_OUTPUT, OPEN_IO2,
                                CLOSE, ダクト類, 函体)
from cim.cobol import ca1z0010

NG = []


def miru(midashi, えた, はず):
    maru = 'OK' if えた == はず else 'NG'
    print('  [%s] %-44s %s' % (maru, midashi,
                               '' if maru == 'OK' else
                               '← %r のはずが %r' % (はず, えた)))
    if maru == 'NG':
        NG.append(midashi)


def dairi(yonda, modori=None, key=''):
    """子の代役。呼ばれたことを記録し、戻り画面とキーＩＤを置く。"""
    def yobu(link):
        yonda.append(link.画面情報)
        if modori is not None:
            link.戻り画面 = modori
        link.キーＩＤ = key
    return yobu


def tsukuru(iki='', modori='', key='', rc=''):
    yonda = {}
    kodomo = {}
    for grp, (pgm, _s, _d) in GAMEN.items():
        yonda[pgm] = []
        kodomo[pgm] = dairi(yonda[pgm], modori, key)

    def tsutsumu(pgm, moto):
        def yobu(link):
            moto(link)
            link.リターンコード = rc
        return yobu
    kodomo = {p: tsutsumu(p, f) for p, f in kodomo.items()}
    a = A10010PO(con=None, kodomo=kodomo, hyoji=lambda s: a.log.append(s))
    a.初期処理Ｒ('WS01', '101', '102')
    a.link.行き画面 = iki
    return a, yonda


print('================================================================')
print('■ 工程11-1 見積登録  A10010PO.COB の制御を確かめます')
print('================================================================')
print()
print('■ 呼び分け（行き画面 の先頭4文字）')
for iki, matsu in (('', 'A10040PO'), ('    ', 'A10040PO'), ('GRPA', 'A10040PO'),
                   ('GRPB', 'A10060PO'), ('GRPC', 'A10070PO'),
                   ('GRPD', 'A10080PO'), ('GRPE', 'A10090PO'),
                   ('GRPF', 'A10050PO')):
    a, yonda = tsukuru(iki=iki)
    a.主処理Ｒ()
    yobareta = [p for p, v in yonda.items() if v]
    miru('行き画面 %-6r → %s' % (iki, matsu), yobareta, [matsu])

a, yonda = tsukuru(iki='GRPZ')
a.主処理Ｒ()
miru('行き画面 \'GRPZ\' → どの子も呼ばない',
     [p for p, v in yonda.items() if v], [])
miru('そのとき「遷移先がありません。」を出す',
     '遷移先がありません。' in a.log, True)

print()
print('■ 画面の行き来')
a, _ = tsukuru(iki='GRPB', modori='GRPC')
a.主処理Ｒ()
miru('子から戻ると 行き画面 ← 戻り画面', a.link.行き画面, 'GRPC')

a, yonda = tsukuru(iki='GRPA', key='F010')
a.主処理Ｒ()
miru('鑑で キーＩＤ="F010" なら終了', a.end_flg, 'END')

a, yonda = tsukuru(iki='GRPB', key='F010')
a.主処理Ｒ()
miru('項目で キーＩＤ="F010" でも終了しない', a.end_flg, 'INT')

a, yonda = tsukuru(iki='GRPA')
a.初期処理Ｒ('WS01', '101', '102')
miru('初期処理で6画面を LOW-VALUE にする',
     sorted(set(a.gamen.values())), ['LOW-VALUE'])
miru('端末ＩＤ・ＶＬＤ一・ＶＬＤ二 を受け取る',
     (a.link.端末ＩＤ, a.link.ＶＬＤ一, a.link.ＶＬＤ二), ('WS01', '101', '102'))
miru('END-FLG は "INT" で始まる', a.end_flg, 'INT')

print()
print('■ リターンコードの見かた')
for rc, mi in ((ダクト類, '異常にしない（ダクト類 1000）'),
               (函体,     '異常にしない（函体 0000）')):
    a, _ = tsukuru(iki='GRPC', rc=rc)
    try:
        a.主処理Ｒ()
        miru('リターンコード %r は %s' % (rc, mi), True, True)
    except Abend as e:
        miru('リターンコード %r は %s' % (rc, mi), 'Abend %s' % e, True)

a, _ = tsukuru(iki='GRPC', rc='')
a.主処理Ｒ()
miru('リターンコード 空白 は空白に戻す', a.link.リターンコード, '')

a, _ = tsukuru(iki='GRPC', rc='AB35')
try:
    a.主処理Ｒ()
    miru('リターンコード "AB35" は異常終了', 'とまらなかった', 'Abend')
except Abend as e:
    miru('リターンコード "AB35" は異常終了', 'Abend', 'Abend')
    miru('  PROGRAM-STATUS ＝ 4000 ＋ 作業ＳＴ(35)', e.program_status, 4035)

print()
print('■ OPEN と CLOSE（現行の並びのまま）')
miru('OPEN I-O   6本', len(OPEN_IO), 6)
miru('OPEN INPUT 18本', len(OPEN_INPUT), 18)
miru('OPEN OUTPUT 2本（ＶＬＤ１・ＶＬＤ２）', OPEN_OUTPUT, ['VLDA1001', 'VLDA1002'])
miru('OPEN I-O   履歴ファイル', OPEN_IO2, ['ARMIJL1'])
miru('CLOSE に ＶＬＤ１ は入れない（行879がコメント）',
     'VLDA1001' in CLOSE, False)
miru('CLOSE に ＶＬＤ２ は入れない', 'VLDA1002' in CLOSE, False)
miru('CLOSE は 25本', len(CLOSE), 25)
a, _ = tsukuru()
miru('開いた中から CLOSE の分だけ閉じる',
     'VLDA1001' in a.クローズ処理(), False)

print()
print('■ CL（CA1Z0010）')
rc, log = ca1z0010.run('WS01')
miru('画面定義が無いので、黙って成功させない', rc != 0, True)
miru('開始メッセージ4行を出す', log[:1], ['*' * 35])
miru('異常でも ABEND メッセージを出す',
     any('<<ABEND>>' in x for x in log), True)
miru('?VLD1=101 ／ ?VLD2=102', (ca1z0010.VLD1, ca1z0010.VLD2), ('101', '102'))

print()
print('================================================================')
if NG:
    print('■ 合わなかった項目 %d件' % len(NG))
    for x in NG:
        print('   ' + x)
    sys.exit(1)
print('■ A10010PO.COB の制御は、すべて現行どおりでした。')
print('   ※ 画面（XMDLIB 6本）と受け渡し領域（A10CPYPO）は未入手のため、')
print('      子6本の中身はまだ作れていません。')
print('================================================================')
