# -*- coding: utf-8 -*-
"""CA1Z0010  見積登録処理の起動（CA1Z0010.CL 91行 の移植）

工程表の1番の入口。ASP の CL がやっていることは3つだけです。

  1) 開始メッセージを4行、操作端末へ出す（?MSG0 枠・業務名・CL名・日付・時刻）
  2) トランザクション作業ファイルを作って、トランザクションを始める
         CRTTWF SIZE-4000
         STRTRNS EXCLMODE-@RECORD2,TWASIZE-150,MAXDATA-5000
     → **1993年の SIZE-1500 を 2015-02-19 に 4000 へ広げている**（村上様の修正）
  3) CALL A10010PO,(?WSNAME,?VLD1,?VLD2)   ?VLD1='101' / ?VLD2='102'
     終わったら STPTRNS → DLTTWF → 終了メッセージ4行 → RETURN PGMEC-@PGMEC

  異常時（CHECK WHEN ABNORMAL）は、ラベル・コマンド名・エラーコードを
  並べたメッセージを足して出し、**同じように STPTRNS → DLTTWF をしてから**
  RETURN PGMEC-?SAVEEC（元の戻りコード）で返す。ここを省くと作業ファイルが
  残るので、試作でも同じ順番で行う。

SQL Server では「トランザクション作業ファイル」に当たるものがありません。
始まりと終わりを BEGIN TRAN／COMMIT・ROLLBACK に置き換えます。
EXCLMODE-@RECORD2（レコード単位の排他）は、SQL Server の既定の行ロックに当たります。
"""
import datetime

業務名 = '見積登録処理'
CLID  = '*  (CA1Z0010)      '
MSG0  = '*' * 35
VLD1  = '101'
VLD2  = '102'


def _waku(kubun, jobdate=None, stime=None):
    """開始・終了・異常の4行メッセージを作る（行35-41, 62-68, 76-88）。"""
    ima = datetime.datetime.now()
    jobdate = jobdate or ima.strftime('%y/%m/%d')
    stime = stime or ima.strftime('%H:%M:%S')
    shirushi = {'START': '<<START>>            *',
                'END':   '<< END >>            *',
                'ABEND': '<<ABEND>>            *'}[kubun]
    return [MSG0,
            '*' + 業務名 + shirushi,
            CLID + ' ' + jobdate + ' ' + stime + ' *',
            MSG0]


def run(wsname, con=None, a10010po=None, hyoji=None, kaisu=None):
    """CL をそのまま実行する。

    wsname    … ?WSNAME := %STRING(@ORGWS)   操作している端末の名前
    con       … データベース接続（トランザクションの始め・終わりに使う）
    a10010po  … 呼ぶ本体。既定は cim.cobol.a10010po.A10010PO
    返り値    … (PGMEC, メッセージの一覧)   PGMEC は ASP の戻りコード
    """
    log = []
    dasu = hyoji or log.append
    for x in _waku('START'):
        dasu(x)                                    # SNDMSG TO-XCTL

    if a10010po is None:
        from .a10010po import A10010PO
        a10010po = A10010PO(con=con, hyoji=dasu)

    # CRTTWF SIZE-4000 ／ STRTRNS
    if con is not None:
        con.execute('BEGIN')                       # トランザクション開始
    pgmec = 0
    try:
        a10010po.run(wsname, VLD1, VLD2, kaisu=kaisu)
        if con is not None:
            con.commit()                           # STPTRNS（正常）
        for x in _waku('END'):
            dasu(x)
    except Exception as e:
        # CHECK WHEN ABNORMAL（行76-90）
        pgmec = getattr(e, 'program_status', 1)
        for x in _waku('ABEND'):
            dasu(x)
        dasu('LABEL=STEP2,NAME=A10010PO,COD=%s,INF=%s'
             % (getattr(e, 'flsts', ''), e))
        if con is not None:
            con.rollback()                         # STPTRNS（異常でも必ず行う）
    # DLTTWF は、どちらの道でも通る（行60, 89）
    return pgmec, log
