# -*- coding: utf-8 -*-
"""レコードリスト（FADLIB／FCDLIB／FFDLIB／KANDLIB）を読み、
   「COBOLファイル定義からSQL Serverテーブル定義へのフィールド名変換仕様書」
   のとおりに SQL Server の列を起こす。

   ■ 列にするもの
     実データを持つ**要素項目（PIC がある最下層）だけ**。
     集団項目（PIC が空）は列にしない。

   ■ 繰返し（OCCURS）
     英名を _ で6文字に詰め、_1, _2 … を付けて水平展開する。
       JUZENR(6文字) → JUZENR_1     NTMG1(5文字) → NTMG1__1
       TEST(4文字)   → TEST___1
     集団項目に繰返しが付く場合は、その配下の要素項目すべてを展開する。

   ■ 型
     X(n)        → char(n)
     N(n)        → nchar(n)
     9(n)        → numeric(n,0)
     9(a)V9(b)   → numeric(a+b,b)
     S 付き（符号）→ 桁数は変えない
"""
import re, pathlib, collections, datetime
import openpyxl

# Excel が「2」などを日付に書き換えてしまっているセルがある（レベル欄に多い）。
# 1900年式の通し番号に戻す。戻した回数は hinoki に数えて、黙って直さない。
hinoki = collections.Counter()


def seisu(v, doko=''):
    """セルの値を整数にする。空なら 0"""
    if v is None or v == '':
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, datetime.datetime):
        hinoki[doko] += 1
        return (v - datetime.datetime(1899, 12, 31)).days
    if isinstance(v, datetime.date):
        hinoki[doko] += 1
        return (v - datetime.date(1899, 12, 31)).days
    t = str(v).strip()
    if re.fullmatch(r'-?\d+', t):
        return int(t)
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})(?: .*)?', t)
    if m:
        hinoki[doko] += 1
        return (datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                - datetime.date(1899, 12, 31)).days
    hinoki['読めない:' + doko] += 1
    return 0

# ────────────────────────────────────────────────────────────────
# レコードリストの読み取り誤り（OCR）を直す
#   レコードリストは PDF から起こしたもので、B↔E・I↔1 の取りちがえがある。
#   **根拠を示せるものだけ**直す。根拠が無いものは直さず、そのまま出す。
# ────────────────────────────────────────────────────────────────
NAOSHI = {
    ('NIPSYOFP', 'BGKOSN'): ('EGKOSN',
        'この表の項目はすべて EG… で始まる（EGCODE/EGNAME/EGJMOK…）。'
        'BG… は1つだけで、B と E の取りちがえと見られる'),
    ('NIPSYOFP', 'BGYOHK'): ('EGYOHK',
        '同上。EGYOHK（余白）の B と E の取りちがえ'),
    ('NOKIB4SF', 'YOBIHI'): ('YOBIH1',
        '直後に YOBIH2（予備日付２）が並ぶので、これは予備日付１＝YOBIH1。'
        'さらに村上様の定義体 NOKIKFFP.sql にも YOBIH1 が実在する'),
}

PIC_X = re.compile(r'^X\((\d+)\)$')
PIC_N = re.compile(r'^N\((\d+)\)$')
PIC_9 = re.compile(r'^S?9\((\d+)\)(?:V9\((\d+)\))?$')
PIC_9V = re.compile(r'^S?9\((\d+)\)V(9+)$')
PIC_V9 = re.compile(r'^S?V9\((\d+)\)$')


def pad6(nm):
    """繰返し項目の英名を 6文字に詰める（足りない分は _）"""
    return nm + '_' * max(0, 6 - len(nm))


def sqlgata(pic):
    """PIC → SQL Server の型。分からない形は None を返す（黙って決めない）

       既存32表（村上様へお渡し済み・レビュー済み）の実測に合わせた規則
         X(n)          → char(n)
         N(n)          → nchar(n)
         9(n)          → numeric(n,0)
         S9(n)         → decimal(n,0)     ← 符号つきは decimal
         S9(a)V9(b)    → numeric(a+b,b)   ← 小数点があるものは numeric
         9(a)V9(b)     → numeric(a+b,b)
       （符号つきでも小数点があれば numeric。既存50件中48件が decimal、
         小数点つきの2件だけが numeric だったため、この形で一致する）
    """
    p = (pic or '').strip().upper().replace(' ', '')
    m = PIC_X.match(p)
    if m:
        return 'char(%s)' % m.group(1), int(m.group(1))
    m = PIC_N.match(p)
    if m:
        return 'nchar(%s)' % m.group(1), int(m.group(1)) * 2
    m = PIC_9.match(p)
    if m:
        a = int(m.group(1)); b = int(m.group(2) or 0)
        gata = 'decimal' if (p.startswith('S') and not b) else 'numeric'
        return '%s(%d,%d)' % (gata, a + b, b), a + b
    m = PIC_9V.match(p)
    if m:
        a = int(m.group(1)); b = len(m.group(2))
        return 'numeric(%d,%d)' % (a + b, b), a + b
    m = PIC_V9.match(p)
    if m:
        b = int(m.group(1))
        return 'numeric(%d,%d)' % (b, b), b
    return None, None


def yomu(recdir):
    """レコードリストを読んで {物理ファイル名: {...}} を返す"""
    file = {}
    for x in sorted(pathlib.Path(recdir).glob('*LIB_rev4.xlsx')):
        lib = x.stem.split('_')[0]
        wb = openpyxl.load_workbook(x, read_only=True, data_only=True)
        for i, r in enumerate(wb['ファイルレイアウト'].iter_rows(values_only=True)):
            if i == 0 or not r or not r[2]:
                continue
            p = str(r[2]).strip().upper()
            d = file.setdefault(p, {'lib': lib, 'nm': str(r[1] or '').strip(),
                                    'items': []})
            d['items'].append({
                'lv':  seisu(r[4], 'レベル'),
                'jp':  str(r[5] or '').strip(),
                'en':  str(r[6] or '').strip().upper(),
                'pic': str(r[7] or '').strip(),
                'you': str(r[8] or '').strip(),
                'occ': seisu(r[9], '繰返し数'),
                'len': seisu(r[10], '項目長'),
                'pos': seisu(r[11], '項目位置'),
            })
        ws = wb['物理ファイル名論理ファイル名']
        for i, r in enumerate(ws.iter_rows(values_only=True)):
            if i == 0 or not r or not r[2]:
                continue
            p = str(r[2]).strip().upper()
            if p in file:
                file[p].setdefault('ronri', [])
                for c in r[3:]:
                    if c:
                        file[p]['ronri'].append(str(c).strip().upper())
    return file


def _ki(items):
    """レベル番号から木を作る"""
    root = {'lv': 0, 'ko': [], 'occ': 0, 'pic': '', 'en': '', 'jp': ''}
    michi = [root]
    for it in items:
        it = dict(it, ko=[])
        while len(michi) > 1 and michi[-1]['lv'] >= it['lv']:
            michi.pop()
        michi[-1]['ko'].append(it)
        michi.append(it)
    return root


def hashira(items, hyo=''):
    """要素項目を（繰返しを展開して）列の一覧にする。

       繰返しは**メモリ上の並びのまま**展開する。
       集団項目に OCCURS 50 が付き、その中に KAISHI と OWARI があるなら
         KAISHI_1, OWARI__1, KAISHI_2, OWARI__2, …
       と交互に並ぶ（KAISHI を50個並べてから OWARI ではない）。
       現行ファイルのバイト位置と合わせるため、ここは崩してはいけない。

       返り値 (cols, warn)   cols=[(列名, 型, 日本語名, PIC)]
    """
    root = _ki(items)
    warn = []

    def naosu(en):
        t = NAOSHI.get((hyo, en))
        return (t[0], t[1]) if t else (en, None)

    # 1回目 … 各要素項目が何回あらわれるか数える
    kazu = collections.Counter()

    def kazoeru(nd):
        for ko in nd['ko']:
            n = max(1, ko['occ'])
            if ko['pic']:
                kazu[naosu(ko['en'])[0]] += n
            else:
                for _ in range(n):
                    kazoeru(ko)
    kazoeru(root)

    # 2回目 … 並びのまま列にする
    ima = collections.Counter()
    cols = []

    def dasu(nd):
        for ko in nd['ko']:
            n = max(1, ko['occ'])
            if ko['pic']:
                en, riyu = naosu(ko['en'])
                if riyu:
                    warn.append('%s → %s に直しました（%s）' % (ko['en'], en, riyu))
                gata, _b = sqlgata(ko['pic'])
                if gata is None:
                    warn.append('%s の PIC「%s」が読めません' % (en, ko['pic']))
                    continue
                for _ in range(n):
                    ima[en] += 1
                    if kazu[en] == 1:
                        cols.append((en, gata, ko['jp'], ko['pic']))
                    else:
                        cols.append(('%s_%d' % (pad6(en), ima[en]), gata,
                                     '%s(%d)' % (ko['jp'], ima[en]), ko['pic']))
            else:
                if ko['occ'] > 1 and any(m['occ'] > 1 for m in ko['ko']):
                    warn.append('%s は繰返しの入れ子です（添字2つ）' % ko['en'])
                for _ in range(n):
                    dasu(ko)
    dasu(root)
    return cols, [w for i, w in enumerate(warn) if w not in warn[:i]]
