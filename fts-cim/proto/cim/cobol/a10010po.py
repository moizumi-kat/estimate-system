# -*- coding: utf-8 -*-
"""A10010PO  見積登録 画面制御（A10010PO.COB 1,028行 の移植）

工程表の1番「11-1 営業／見積／見積登録」の中心。
子の画面プログラム6本を呼び分けるだけで、自分では画面を出しません。

    CA1Z0010.CL  →  CALL A10010PO,(?WSNAME,?VLD1,?VLD2)
                      ├ GRPA 鑑   → A10040PO
                      ├ GRPB 項目 → A10060PO
                      ├ GRPC 明細 → A10070PO
                      ├ GRPD 函体 → A10080PO
                      ├ GRPE 科目 → A10090PO
                      └ GRPF 概算 → A10050PO

■ 現行のまま写したところ（崩してはいけない）
  ・行き画面 の先頭4文字で呼び分ける。**空白は GRPA と同じ扱い**（行682-686）
  ・子から戻ったら 行き画面 ← 戻り画面（行700）
  ・鑑だけは キーＩＤ="F010" で終了（行583-586）
  ・リターンコード "1000"（ダクト類）と "0000"（函体）は**異常ではない**（行707-709）
    空白なら空白に戻す。それ以外は 3文字目から2桁をファイルステータスとして異常終了
  ・異常終了時の PROGRAM-STATUS ＝ 4000 ＋ 作業ＳＴ（行978, 1002）
  ・CLOSE に **ＶＬＤ１／ＶＬＤ２ は入っていない**（行879がコメントアウトされている）。
    現行がそうなっているので、そのまま閉じない。

■ まだ現行どおりに作れないところ（村上様へ依頼中）
  ・画面定義 A10040D1〜A10090D1（XMDLIB・6本）が未入手。
    現行は 初期処理 で6画面を LOW-VALUE で初期化し、子を呼ぶ前後で
    画面情報 とやりとりする（行583-635）。ここは入手後に埋める。
  ・受け渡し領域 A10CPYPO（コピー原文）が未入手。
    下の Linkage は、7本（A10010PO と子6本）の**使いかたから起こした暫定**。
    桁は確定していないので、入手後に差し替える。
"""
from .. import db

# 定数エリア（行464-467）
概算新規 = '8'
概算修正 = '9'
ダクト類 = '1000'
函体     = '0000'

# 画面グループ → 子プログラム（行680-694 の EVALUATE）
GAMEN = {
    'GRPA': ('A10040PO', '鑑処理',   'A10040D1'),
    'GRPB': ('A10060PO', '項目処理', 'A10060D1'),
    'GRPC': ('A10070PO', '明細処理', 'A10070D1'),
    'GRPD': ('A10080PO', '函体処理', 'A10080D1'),
    'GRPE': ('A10090PO', '科目処理', 'A10090D1'),
    'GRPF': ('A10050PO', '概算処理', 'A10050D1'),
}

# OPEN の区分（行646-672）。現行の並びのまま。
OPEN_IO = ['DSPFILE', 'AWKGFL1', 'AWKKFL1', 'AWKMFL1', 'AHATML1', 'ACHKFL1']
OPEN_INPUT = ['AJBGFL1', 'AJBKFL1', 'AJBMFL1', 'ASOSML1', 'ANAMML1', 'ATORML1',
              'AKAKML1', 'ASETML1', 'ABUHML1', 'ASIYML1', 'ASUNML1', 'ASUNML2',
              'ASUNML3', 'ATYUML1', 'ABANML1', 'CCALML1', 'CSHKML1', 'AHOSML1']
OPEN_OUTPUT = ['VLDA1001', 'VLDA1002']
OPEN_IO2 = ['ARMIJL1']

# CLOSE（行862-878）。ＶＬＤ１／ＶＬＤ２ は**入っていない**（行879がコメント）
CLOSE = ['DSPFILE', 'AWKGFL1', 'AWKKFL1', 'AWKMFL1',
         'AJBGFL1', 'AJBKFL1', 'AJBMFL1', 'ARMIJL1', 'AHATML1',
         'ASOSML1', 'ANAMML1', 'ATORML1', 'AKAKML1', 'ASETML1',
         'ABUHML1', 'ASIYML1', 'ASUNML1', 'CSHKML1',
         'ASUNML2', 'ASUNML3', 'ATYUML1', 'ABANML1',
         'ACHKFL1', 'CCALML1', 'AHOSML1']

# 論理ファイル名 → 物理ファイル名（試作環境の表）
BUTSURI = {
    'AWKGFL1': 'AWKGFP',  'AWKKFL1': 'AWKKFP',  'AWKMFL1': 'AWKMFP',
    'AJBGFL1': 'AJBGFP',  'AJBKFL1': 'AJBKFP',  'AJBMFL1': 'AJBMFP',
    'ARMIJL1': 'ARMIJFP', 'AHATML1': 'AHATMP',  'ASOSML1': 'ASOSMP',
    'ANAMML1': 'ANAMMP',  'ATORML1': 'ATORMP',  'AKAKML1': 'AKAKMP',
    'ASETML1': 'ASETMP',  'ABUHML1': 'ABUHMP',  'ASIYML1': 'ASIYMP',
    'ASUNML1': 'ASUNMP',  'ASUNML2': 'ASUNMP',  'ASUNML3': 'ASUNMP',
    'ATYUML1': 'ATYUMP',  'ABANML1': 'ABANMP',  'ACHKFL1': 'ACHKFP',
    'CCALML1': 'CCALMP',  'CSHKML1': 'CSHKMP',  'AHOSML1': 'AHOSMP',
}


class Abend(Exception):
    """現行の ABEND-RTN／ABEND-RTN2（STOP RUN）に当たる。

    PROGRAM-STATUS ＝ 4000 ＋ 作業ＳＴ（行978, 1002）を持たせる。
    """

    def __init__(self, flname, flmode, flsts):
        self.flname, self.flmode, self.flsts = flname, flmode, flsts
        try:
            self.program_status = 4000 + int(flsts or 0)
        except ValueError:
            self.program_status = 4000
        super().__init__('%s %s ERR  PROGRAM-STATUS=%d'
                         % (flname, flmode, self.program_status))


class Linkage:
    """リンケージ情報エリア（A10CPYPO）の**暫定**。

    7本の使いかたから起こしたもので、桁は確定していない。
    のべ回数の多い順に 戻り画面53／リターンコード44／画面情報41／端末ＩＤ34／
    キーＩＤ34／見積ナンバー115／項番79 … が確認できている。
    """

    項目 = ('画面情報', '行き画面', '戻り画面', 'キーＩＤ', 'リターンコード',
            'ファイル名', '端末ＩＤ', 'ＶＬＤ一', 'ＶＬＤ二',
            '見積ナンバー', '項番', '行番',
            '鑑画面処理区分', '項目処理区分', '項目明細処理区分', 'システム日付')

    def __init__(self):
        self.初期化()

    def 初期化(self):
        """INITIALIZE リンケージ情報エリア（行669）。
        COBOL の INITIALIZE は英数字項目を空白、数字項目をゼロにする。"""
        for n in self.項目:
            setattr(self, n, '')

    def __repr__(self):
        return 'Linkage(' + ', '.join(
            '%s=%r' % (n, getattr(self, n)) for n in self.項目
            if getattr(self, n) not in ('', None)) + ')'


def _mikomi(ko):
    """画面定義が未入手の子を呼んだときの返しかた。

    黙って成功させない。現行と同じ動きにはできないことを、呼んだ側に伝える。
    """
    def yobu(link):
        raise NotImplementedError(
            '%s は画面定義（XMDLIB）が未入手のため、まだ現行どおりに動かせません'
            % ko)
    return yobu


class A10010PO:
    """見積登録の画面制御。

    子6本は差し替えられるようにしてある（画面定義が届いたら順に埋める）。
      kodomo = {'A10040PO': 関数, …}   関数は Linkage を受け取って書き換える
    """

    def __init__(self, con=None, kodomo=None, hyoji=None):
        self.con = con
        self.link = Linkage()
        self.gamen = {d1: None for _, _, d1 in GAMEN.values()}   # 行505-510
        self.akiteiru = []
        self.end_flg = ''
        self.wk_flname = ''
        self.log = []
        self.hyoji = hyoji or self.log.append
        self.kodomo = dict((p, _mikomi(p)) for p, _, _ in GAMEN.values())
        if kodomo:
            self.kodomo.update(kodomo)

    # ---- オープン処理（行644-845）--------------------------------
    def オープン処理(self):
        for mode, nn in (('I-O', OPEN_IO), ('INPUT', OPEN_INPUT),
                         ('OUTPUT', OPEN_OUTPUT), ('I-O', OPEN_IO2)):
            for n in nn:
                sts = self._open1(n, mode)
                if sts not in ('', '00'):
                    # 現行は 1本ずつ IF ステータス NOT = ZERO で ABEND-RTN
                    raise Abend(n, 'OPEN', sts)
                self.akiteiru.append(n)

    def _open1(self, ronri, mode):
        """ファイルを開く。試作では表の在否を見るだけ（現行のステータス相当）。"""
        if ronri in ('DSPFILE',):
            return '00'                      # 画面ファイル。試作では画面側が持つ
        if self.con is None:
            return '00'
        hyo = BUTSURI.get(ronri)
        if not hyo:
            return '00'                      # ＶＬＤ等。表を持たないもの
        try:
            db.q(self.con, 'SELECT * FROM %s WHERE 1=0' % hyo)
            return '00'
        except Exception:
            return '35'                      # ファイルが無い（COBOL の 35）

    # ---- クローズ処理（行860-882）--------------------------------
    def クローズ処理(self):
        # ＶＬＤ１／ＶＬＤ２ は現行が閉じていないので、ここでも閉じない
        self.tojita = [n for n in CLOSE if n in self.akiteiru]
        return self.tojita

    # ---- 初期処理Ｒ（行663-677）----------------------------------
    def 初期処理Ｒ(self, ws_no, vld1, vld2):
        for d1 in self.gamen:
            self.gamen[d1] = 'LOW-VALUE'     # MOVE LOW-VALUE TO A10040D1 …
        self.オープン処理()
        self.link.初期化()
        self.link.端末ＩＤ = ws_no
        self.link.ＶＬＤ一 = vld1
        self.link.ＶＬＤ二 = vld2
        self.end_flg = 'INT'

    # ---- 主処理Ｒ（行679-717）------------------------------------
    def 主処理Ｒ(self):
        iki = (self.link.行き画面 or '')[:4]
        if iki in ('', '    ') or iki == 'GRPA':
            self._ko('GRPA')                 # WHEN SPACE / WHEN "GRPA"
        elif iki in GAMEN:
            self._ko(iki)
        else:
            self.hyoji('遷移先がありません。')

        self.link.行き画面 = self.link.戻り画面          # 行700

        self.wk_flname = self.link.ファイル名            # 行704
        rc = self.link.リターンコード
        if rc in (ダクト類, 函体):                        # 行707-709 異常ではない
            pass
        elif rc in ('', '    '):                          # 行710-711
            self.link.リターンコード = ''
        else:                                             # 行712-714
            raise Abend(self.wk_flname, '', (rc or '')[2:4])

    def _ko(self, grp):
        """子を呼ぶ（行576-635）。画面情報を渡して、返ってきたものを受け取る。"""
        pgm, setsu, d1 = GAMEN[grp]
        self.link.画面情報 = self.gamen[d1]              # MOVE A100x0D1 TO 画面情報
        self.kodomo[pgm](self.link)                      # CALL "A100x0PO"
        self.gamen[d1] = self.link.画面情報              # MOVE 画面情報 TO A100x0D1
        if grp == 'GRPA' and self.link.キーＩＤ == 'F010':
            self.end_flg = 'END'                         # 鑑だけ（行583-586）

    # ---- 処理開始（行645-658）------------------------------------
    def run(self, ws_no, vld1='101', vld2='102', kaisu=None):
        """CL からの呼び出しに当たる。

        kaisu … 試作で回数を区切りたいときだけ指定する（現行は END-FLG のみ）。
        """
        self.hyoji('*****  A10010PO V01/L01 START *****')
        try:
            self.初期処理Ｒ(ws_no, vld1, vld2)
            n = 0
            while self.end_flg != 'END':
                self.主処理Ｒ()
                n += 1
                if kaisu is not None and n >= kaisu:
                    break
            self.クローズ処理()                           # 終了処理Ｒ
        except Abend:
            self.クローズ処理()                           # ABEND-RTN も閉じてから止まる
            raise
        self.hyoji('*****  A10010PO V01/L01 END   *****')
        return self.link
