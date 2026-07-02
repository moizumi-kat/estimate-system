#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
積算コード選定システム v1.6  Flask版（図面 → コード選定）
  ブラウザから図面PDF/画像をアップ → Claude Visionで抽出 → v9.2 DB照合＋AI絞り込み → 結果表示・Excel
設計原則:
  AIはコードを生成しない。コードは v9.2 DB の実在コード または 確定/学習ルール からのみ。
  確信が持てない/読取不明瞭は △(要確認)。◎の誤りゼロを最優先。
起動:
  export ANTHROPIC_API_KEY=sk-ant-...
  pip install flask anthropic pdf2image openpyxl pillow
  python3 app.py        # → http://localhost:8000
"""
import os, re, io, json, base64, unicodedata, datetime, zipfile, tempfile, hmac, secrets, functools
from flask import Flask, request, jsonify, send_file, Response
from anthropic import Anthropic
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

HERE=os.path.dirname(os.path.abspath(__file__))
DB=json.load(open(os.path.join(HERE,'db.json'),encoding='utf-8'))
byCode={d['code']:d for d in DB}
# 部品別必要属性表(属性駆動の抽出チェック・選定確信度判定で共有)
try:
    ATTR_TABLE=json.load(open(os.path.join(HERE,'attr_table.json'),encoding='utf-8'))
except Exception:
    ATTR_TABLE={}
MODEL=os.environ.get('SELECT_MODEL','claude-opus-4-8')
app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=40*1024*1024  # 40MB
app.secret_key=os.environ.get('APP_SECRET', secrets.token_hex(16))
APP_PASSWORD=os.environ.get('APP_PASSWORD','')  # 未設定なら認証オフ（ローカル試用向け）

from flask import session, redirect

def login_required(f):
    @functools.wraps(f)
    def w(*a,**k):
        if not APP_PASSWORD:        # パスワード未設定時は素通り（ローカル用）
            return f(*a,**k)
        if session.get('auth'):
            return f(*a,**k)
        # API呼び出しは401、画面はログインへ
        if request.path.startswith('/api/'):
            return jsonify(error='未認証'),401
        return redirect('/login')
    return w

LOGIN_HTML="""<!DOCTYPE html><html lang=ja><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>ログイン</title>
<style>body{font-family:'Yu Gothic',Meiryo,sans-serif;background:#f7f5ef;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.box{background:#fff;border:1px solid #d6d1c4;border-radius:10px;padding:32px;width:320px}
h1{font-size:16px;color:#1e3a28;margin:0 0 18px}input{width:100%;padding:10px;border:1px solid #d6d1c4;
border-radius:6px;font-size:14px;box-sizing:border-box}button{width:100%;margin-top:12px;padding:11px;
background:#1e3a28;color:#fff;border:0;border-radius:6px;font-size:14px;font-weight:700;cursor:pointer}
.e{color:#c0504d;font-size:12px;margin-top:10px}</style></head>
<body><form class=box method=post action=/login>
<h1>積算コード選定システム</h1>
<input type=password name=pw placeholder=パスワード autofocus>
<button>ログイン</button>
{ERR}</form></body></html>"""

@app.route('/login',methods=['GET','POST'])
def login():
    if not APP_PASSWORD: return redirect('/')
    if request.method=='POST':
        if hmac.compare_digest(request.form.get('pw',''),APP_PASSWORD):
            session['auth']=True; return redirect('/')
        return Response(LOGIN_HTML.replace('{ERR}','<div class=e>パスワードが違います</div>'),mimetype='text/html')
    return Response(LOGIN_HTML.replace('{ERR}',''),mimetype='text/html')

@app.route('/logout')
def logout():
    session.clear(); return redirect('/login')


def client():
    if not os.environ.get('ANTHROPIC_API_KEY'):
        raise RuntimeError('ANTHROPIC_API_KEY 未設定')
    return Anthropic()

def norm(s):
    if not s: return ''
    s=unicodedata.normalize('NFKC',str(s)).lower()
    return re.sub(r'[ー\-\s\u3000()（）\[\]、,．.]','',s)

# ===== フェーズ1: 図面抽出（Vision）=====
EXTRACT_PROMPT="""あなたは配電盤・制御盤・分電盤の設計図面を読む専門家です。
この図面（単線結線図・盤リスト・分電盤結線図など）から、盤ごとに搭載される電気機器を抽出してください。

【最重要・抽出の第一原則: 機器の「塊」を正しく束ねる】
図面では、1つの機器が「記号(四角/丸/×印等)」＋「その周囲に縦書き・横書きで
分散した文字(機器名・電圧・電流・遮断容量・極数・容量)」で構成される。
機器を抽出する際は、記号の上下左右(特に左側・直上)に近接する文字を、
その機器1つの仕様としてすべて束ねること。数値が複数行に分かれていても1機器とする。
例(実際の図面の配置):
- 「VCB」「7.2kV」「600A」「12.5kA」が記号の脇に縦4行 → name="VCB 7.2kV 600A 12.5kA"
- 「DS×3」「7.2kV」「600A」が縦3行 → name="DS 7.2kV 600A", qty=3
- 「VT(PF付き)」「7.2kV」「1A」「6.6kV/110V」「50VA」 → 1つのVTとして束ねる
- 「LBS」「7.2kV200A」 → name="LBS 7.2kV 200A"
- 「T:1φ100kVA」「P:6600V」「S:210V/105V」 → 1つの変圧器として束ねる
記号の近くにある数値を「別の要素」として切り離さず、必ず最寄りの機器に結合する。
機器名だけ拾って定格(数値)を落とすことが最も多い失敗。記号の周囲の数値を見落とさない。

【電気図記号は機器名の代わりになる（重要）】
機器は「機器名の文字(VCB等)」だけでなく「電気図記号」でも表される。
記号そのものが機器を表すので、文字の機器名が無くても記号があれば機器として抽出する。
主な図記号と機器の対応:
- 〇を2つ重ねた記号(一次・二次巻線) = 変圧器(TR)。脇に「T:◯φ◯kVA P:◯V S:◯V」と表記
- ×印を四角で囲む = VCB(真空遮断器)
- 斜め線・接点記号 = DS(断路器)
- 丸の中に貫通線 = CT(変流器)、丸2つ = VT
- 縦の平行線 = コンデンサ(SC)、コイル記号 = リアクトル(SR)
- 箱に斜線・ヒューズ付き = LBS(高圧負荷開閉器)
- 丸の中に文字(V/A/W/I>等) = 計器・継電器
変圧器のように機器名の文字が無く記号と「T:」表記だけの場合も、必ず機器として抽出する。

【自己検証: 機器名も図記号も無い数値の塊は「結合漏れ」のサイン】
抽出を終える前に必ず自己チェックすること。図面上に「機器名(VCB/DS/CT等)も
図記号も伴わない、電圧・電流・遮断容量などの数値だけの塊」が浮いて残っていたら、
それは独立機器ではなく、近くの機器の定格を結合し損ねた証拠である。最寄りの機器に結合する。
※ただし近くに図記号(変圧器の〇二重等)があれば、それは正常な機器なので結合漏れではない。
逆に、機器名や記号はあるが定格が空の機器があれば、その記号の周囲を見直して
近くの数値(電圧・電流・遮断容量・極数・容量)を探し、結合すること。
特に高圧主回路のVCB・DS・LBS・PAS・VCT・VTは必ず定格を伴うはずなので、
定格が無いまま機器名だけになっていたら読み落としを疑い、周囲を再確認する。

ルール:
- 盤名ごとにグループ化（例: 高圧受電盤, 低圧動力盤No1, P-AC11, L-1W, T-1）
- 各機器の「品名・仕様(型番/容量/極数/AF等を図面表記のまま)」と「数量」
- 主幹/分岐/計器/操作回路/支給品(TR/SC/SR)/SPD/函体/端子盤を漏れなく
- 読み取れない項目は推測せず "unclear":true

【ケーブル・電線類は計上対象外（機器ではない）】
FP・FPT・CVT・CV・CVV・IV・VVF・KIV等の電線・ケーブル類は機器ではないので、
盤搭載機器として抽出しない("cable":true を付けるか、そもそも出力しない)。
例: 「6kV FPT60」「CVT38²」「CV60²」等はケーブルなので計上対象外。
ただし負荷名称行に付随するケーブルサイズ表記(L-1A CVT60²等)は従来どおり負荷明細扱い。

【高圧コンデンサ盤の構成（密集機器の分離・重要）】
高圧コンデンサ盤は1系統あたり次の4機器で構成される。縦に密集して描かれるが、
必ず4つの別々の機器として分離して抽出すること(まとめて1～2機器にしない)。
- PF(限流ヒューズ): 例 40A
- VMC(真空電磁接触器): 例 200A
- SR(直列リアクトル): 例 4.97kvar ※SCの約6%の小容量
- SC(高圧コンデンサ本体): 例 7.02kV 3φ 79.8kvar
各機器の定格を取り違えないこと。特に「SCの容量(79.8kvar)」と「VMCの定格電流(200A)」と
「SRの容量(4.97kvar)」は別物。これらを1つの機器名に混ぜてはいけない。
コンデンサ盤が複数系統(SC-1, SC-2)あれば、系統ごとに4機器を繰り返し抽出する。
VMCやSRが抜け落ちやすいので、PFとSCだけでなくVMC・SRも必ず拾うこと。

【PF(パワーヒューズ)とF(計器ヒューズ)は別機器・分離する】
「PF」はパワーヒューズ(限流ヒューズ、高圧主回路の機器、計上対象)。
「F」は計器ヒューズ(計器保護用の小ヒューズ)。両者は別機器なので、図面で近くにあっても
1つにまとめず、別々の機器として分離して抽出すること。
  例: 図面に「PF」と「F 3A×2」が近接 → name="PF"(または定格付き) と name="F 3A", qty=2 の2機器に分離
「F×N」「F N個」はFが計器ヒューズN個の意味。PFと混同して「PF Fx2」のように
1機器に合体させてはいけない。

【部品別・必要属性表（全カテゴリ網羅）】
部品名を特定したら、対応する属性を図面(記号の脇・機器表・凡例・仕様表)から
必ず探して name に含めること。属性が読めれば正しいコード選定に直結する。
《高圧機器》
- VCB: 電圧/定格電流(400/600/1200A)/遮断容量(8/12.5/20/25kA)/操作(手動/電動/引出)
- VCS・VMC: 電圧/定格電流(200/400A)/形式(電磁/引出/PF付)
- DS(断路器): 電圧/極数(1P/3P)/定格電流(200/400/600A)
- LBS: 電圧/定格電流(200A)/PF有無/G感度(G75A以下等)
- PAS: 電圧/定格電流(200/300A)/種別(SOG/UGS/G-RY付)
- PF(限流ヒューズ): 定格電流(A)
《変成器・計器》
- CT: 変流比(150/5A等)/負担(VA)/形式(丸型/モールド)
- VT: 電圧(一次kV)/負担(VA)/形式(モールド)
- WHM(電力量計): 相数(1φ/3φ)/変流比/電流/検定有無
- ZCT: 定格電流(200/400/600A)
- 計器(VM/AM/VS/AS): 形式(普通角/広角/付加)
《変圧器・コンデンサ》
- TR(変圧器): 相数(1φ/3φ)/容量(kVA) ※二次電圧は選定に不要
- SC(高圧コンデンサ): 電圧/容量(kVA)
- SC(低圧コンデンサ): 電圧(V)/容量(kvar or kW)
- SR(リアクトル): 相数/容量(kvar) ※定格電流(A)でなく容量(kvar)
- LCユニット: L%/電圧(V)/容量(kvar)
《遮断器・開閉器（低圧）》
- B)MCB/ELB/MCCB(分岐): 極数(2P/3P/4P)/AF/AT(定格電流)
- M)MCB/LUG(主幹): 極数/定格電流/欠相保護有無
- ACB: 極数/AF/AT
- MCB(汎用)/MCCB: 極数/定格電流
- MCTT: 極数/定格電流/交流直流
- 高速電源切替器: 極数/定格電流/操作
- SSC: 極数/定格電流
《電動機・起動》
- MGS(電磁開閉器): 容量(kW)/電圧(V)
- SM/起動器/スターリアクトル: 容量(kW)/電圧/極数
- INV(インバータ): 容量(kW)/電圧(V)
- ACリアクトル/スターリアクトル: 容量(kW)/電圧(V)
《端子・函体・その他》
- 端子盤/TB/制御端子: 極数(P)/定格電流
- 保安器函/接地端子盤: 極数(P)
- 函体: 種別(屋内/屋外/標準)・寸法増し有無
- SPD: クラス(I/II)/極数/遮断容量(kA)
- 継電器(OCR/DGR/RPR/OVGR等): 形式(静止型/方向性等)
- TV機器: 種別(分配器/増幅器/混合器)・ポート数
- コンセント: 極数/定格電流/E付・プラグ付
《区分の共通ルール》
- TR/SC/SR等: 図面に「支給」明記→支給品/「スペース」明記→スペース/明記なし→購入
  ※高圧TR/SC/SRは購入品コードが無いので明記なしでも支給品系
読めない属性は推測せず省く。名称だけで終わらせず、その部品の属性を狙って拾うこと。

【同一機器の重複抽出を避ける】
1つの機器に付属する制御・保護要素(PASのSOG制御箱、VCBの操作機構等)を、
別個の機器として二重に出さないこと。SOGはPASの一部、と判断する。
品名中の「×N」「xN」「XN」は数量N個を意味する。"qty" にその数を入れること。
  例: 「SC 7.2kV 50kvar(SC×3)」→ name="SC 7.2kV 50kvar", qty="3"
  例: 「PF×3 40A」→ name="PF 40A", qty="3" / 「CT×2」→ qty="2"
ただし「3φ」の3は相数であり数量ではない。
また「2.2kW×2」のように単位(kW/kVA等)直後の×Nは負荷構成の説明なので数量にしない。

【配電盤リストの「負荷名称行」の扱い（最重要）】
配電盤リスト(電灯盤/動力盤の表)では、1つの分岐回路の下に、その回路が給電する複数の負荷が
ぶら下がって列挙されることがあります。表の構造を次の2種類に区別してください:
- 【機器行】「接続用遮断器(AF/AT)」列に値がある行（例: 1L1 MCB3P 225/150、2M1 MCB3P 225/225）
  → これが計上対象の分岐機器。通常どおり "name" に機器仕様を入れる。
- 【負荷明細行】AF/AT列が空欄で、負荷名称(L-2A, M-1A, EV1, 直圧1, PF-1等)と容量(kVA/kW)・
  ケーブルサイズ(CVT38²等)だけの行
  → これは直前の機器行(親分岐MCB)が給電する負荷の内訳。独立した機器ではない。
  → "load_detail":true を付け、"parent" に親の分岐回路記号(例:"1L1")を入れる。
  → "name" には負荷名称と容量をそのまま入れてよいが、機器仕様は付けない。
重要: 負荷名称(L-1A,L-2A,M-1A等)は「機器名」ではなく「その回路が何に給電するかの名称」です。
これを独立した機器として抽出しないでください。親の分岐MCBが計上単位です。
ただし負荷名称行に明示的に機器(「MCB分岐」等)が併記されている場合のみ機器行として扱います。

【電圧帯の判定（重要）】
単線結線図では変圧器(TR)を境に電圧が変わります。各機器について、接続されている線の電圧を判定し "volt" に入れてください:
- 高圧(TR一次側、6.6kV/7.2kV等) → "volt":"HV"
- 低圧 400V級(415V/440V、三相3線400V級) → "volt":"400V"
- 低圧 200V級(210V/220V) → "volt":"200V"
- 低圧 100V級(105V/100V) → "volt":"100V"
- 図から判断できない/確信が持てない → "volt":"" (空欄。推測しない)
低圧でも200V級と400V級で品番が異なるため、TR二次側の電圧表記(210V/415V等)を必ず確認してください。
特にCT・VT・計器・電磁開閉器(MGS)・モータ回路(L-S/INV)は電圧でコードが変わります。線の接続と電圧表記をたどって判定してください。

【動力盤リストの主回路記号】
動力制御盤リストには「主回路」列に結線図記号(A〜L)がある。各機器でこの記号を読み取り "symbol" に入れる。
A/B=電源、C=直入始動、D=Y-Δ始動、E/G=直入自動交互、F/H=Y-Δ自動交互、I=インバータ、J/K=INVバイパス付、L=インバータ二重化。
動力盤でない(記号が無い)場合は "symbol":"" とする。容量(kW)は "kw" に入れる。

【受変電の保護セット（重要）】
高圧受変電の単線図では、保護リレーと、その検出器・試験端子が、線で繋がって/近接してまとまって描かれ、1つの保護セットを構成します。次のセット関係を読み取り、付属側に "group" でセット名(親リレー)を入れてください:
- 地絡方向保護: DGR(親) ← ZCT・ZPD(検出器)。例: ZCTに "group":"DGR"
- 過電流保護: OCR(親) ← CT・CTT(検出器・試験端子)。例: CTに "group":"OCR"
- 逆電力/地絡過電圧: RPR・OVGR(親) ← VT・VTT・ZPD
- 計量: WHM/Wh(親) ← VCT・VT・CT
リレー本体(DGR/OCR/RPR/OVGR/WHM)は "group" を自分自身の名前にします。線の繋がり・近接配置でセットを判断し、どのリレーに属すか分かるものは必ず "group" を付けてください。判断できない場合は "group":"" とします。

同じ図面に対しては常に同じ結果を返すよう、決定的・一貫して判断すること。
推測や曖昧な解釈で揺らがず、図面に書かれた事実に厳密に基づいて抽出する。
出力は次のJSONのみ（説明文・マークダウン禁止）:
{"panels":[{"panel":"盤名","items":[{"name":"品名仕様","qty":"数量","volt":"HV|400V|200V|100V|","symbol":"A〜L|","kw":"容量数値|","group":"親リレー名|","load_detail":false,"parent":"","unclear":false}]}]}"""

# ===== DXF: 文字を座標つきで抽出し、Claudeで盤・機器に構造化 =====
def extract_dxf(cli, data_bytes):
    import ezdxf
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tf:
        tf.write(data_bytes); path=tf.name
    try:
        doc=ezdxf.readfile(path)
    except Exception as e:
        raise RuntimeError(f"DXF読込失敗: {e}")
    finally:
        try: os.unlink(path)
        except: pass
    msp=doc.modelspace()
    texts=[]
    for e in msp:
        try:
            if e.dxftype()=="TEXT":
                texts.append((round(e.dxf.insert.x,1),round(e.dxf.insert.y,1),e.dxf.text))
            elif e.dxftype()=="MTEXT":
                texts.append((round(e.dxf.insert.x,1),round(e.dxf.insert.y,1),e.text))
        except Exception:
            continue
    if not texts:
        return {"panels":[]}
    texts.sort(key=lambda v:(-v[1],v[0]))
    lines="\n".join(f"({x},{y}) {t}" for x,y,t in texts)
    prompt=(EXTRACT_PROMPT +
        "\n\n以下はDXF図面から抽出した文字（座標(x,y)付き）です。"
        "座標の近さから盤と機器の対応を判断し、盤ごとに機器を構造化してください。\n\n"+lines[:60000])
    with cli.messages.stream(model=MODEL,max_tokens=64000,
        messages=[{"role":"user","content":prompt}]) as stream:
        msg=stream.get_final_message()
    txt="".join(b.text for b in msg.content if b.type=="text").strip()
    txt=re.sub(r"^```(json)?|```$","",txt,flags=re.M).strip()
    try: return json.loads(txt)
    except Exception:
        m=re.search(r"\{.*\}",txt,re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: pass
        # 応答が出力上限で途中で切れた場合は分かりやすく通知
        if getattr(msg,'stop_reason',None)=='max_tokens':
            raise ValueError('図面の機器数が多く抽出結果が出力上限を超えました。盤を分割して投入してください。')
        return {"panels":[]}

# ===== 入力1件を抽出（拡張子で振り分け）=====
def extract_one(cli, fname, data_bytes):
    low=fname.lower()
    if low.endswith(".pdf"):  return extract_pdf_hires(cli, data_bytes)
    if low.endswith(".png"):  return extract(cli, data_bytes, "image/png")
    if low.endswith((".jpg",".jpeg")): return extract(cli, data_bytes, "image/jpeg")
    if low.endswith(".dxf"):  return extract_dxf(cli, data_bytes)
    return None  # 未対応はスキップ

def extract_pdf_hires(cli, data_bytes):
    """PDFを高解像度画像に変換し、ページごとにVisionへ送る。
    APIにPDFをそのまま渡すより解像度を制御でき、密集機器・細かい容量の
    読み取り精度が上がる。複数ページは各々抽出してpanelsを統合する。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        # PyMuPDF未導入なら従来方式(PDFそのまま)にフォールバック
        return extract(cli, data_bytes, "application/pdf")
    all_panels=[]
    doc=fitz.open(stream=data_bytes, filetype="pdf")
    for page in doc:
        # 3倍解像度でレンダリング(細部の文字が潰れないように)
        pix=page.get_pixmap(matrix=fitz.Matrix(3,3))
        png=pix.tobytes("png")
        res=extract(cli, png, "image/png")
        for p in res.get("panels",[]):
            all_panels.append(p)
    return {"panels":all_panels}

# ===== ZIP含む入力をまとめて抽出（複数図面を一括）=====
def extract_input(cli, fname, data_bytes):
    low=fname.lower()
    all_panels=[]
    if low.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data_bytes)) as z:
            for info in z.infolist():
                if info.is_dir(): continue
                inner=info.filename
                if inner.startswith("__MACOSX") or "/." in inner: continue
                if not inner.lower().endswith((".pdf",".png",".jpg",".jpeg",".dxf")): continue
                try:
                    res=extract_one(cli, inner, z.read(info))
                except Exception as e:
                    all_panels.append({"panel":f"[抽出失敗] {os.path.basename(inner)}",
                        "items":[{"name":str(e),"qty":"","unclear":True}]}); continue
                if res:
                    base=os.path.basename(inner)
                    for p in res.get("panels",[]):
                        p["panel"]=f"{base} / {p.get('panel','')}"
                        all_panels.append(p)
        return {"panels":all_panels}
    res=extract_one(cli, fname, data_bytes)
    if res is None:
        raise RuntimeError("未対応の形式です（PDF/PNG/JPG/DXF/ZIPのみ）")
    return res

def extract(cli, data_bytes, media):
    src={"type":"base64","media_type":media,"data":base64.standard_b64encode(data_bytes).decode()}
    block={"type":"document","source":src} if media=="application/pdf" else {"type":"image","source":src}
    with cli.messages.stream(model=MODEL,max_tokens=64000,
        messages=[{"role":"user","content":[block,{"type":"text","text":EXTRACT_PROMPT}]}]) as stream:
        msg=stream.get_final_message()
    txt="".join(b.text for b in msg.content if b.type=="text").strip()
    txt=re.sub(r'^```(json)?|```$','',txt,flags=re.M).strip()
    try: return json.loads(txt)
    except Exception:
        m=re.search(r'\{.*\}',txt,re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: pass
        if getattr(msg,'stop_reason',None)=='max_tokens':
            raise RuntimeError('図面の機器数が多く抽出結果が出力上限を超えました。盤を分割して投入してください。')
        raise RuntimeError('抽出JSON解析失敗')

# ===== フェーズ2: 属性方式 選定エンジン（候補生成→ルール絞り込み）=====
def R(code,conf,note): return dict(code=code,name=byCode.get(code,{}).get('name','') if code else '',conf=conf,note=note)

def _norm2(s):
    import re as _re
    return _re.sub(r'[()（）.]','',norm(s))

def split_qty_suffix(raw):
    """品名中の「x2 ×2 X2」等を数量として分離。
    ただし「2.2kW×2」のように単位(kW/kVA/kvar/V/A)直後の×Nは
    負荷構成の説明なので数量にしない。機器個数を表す×Nのみ分離する。"""
    import re as _re
    s=str(raw)
    for m in _re.finditer(r'[xX×]\s*(\d{1,3})\b', s):
        pre=s[:m.start()].rstrip()
        # 容量単位(kw/kva/kvar)直後は負荷構成→数量にしない。
        # 定格A(例 600A×3)直後は台数なので数量にする。
        if _re.search(r'(kw|kva|kvar|kva?r)$', pre, _re.I):
            continue
        return (s[:m.start()]+s[m.end():]).strip(' ,、'), m.group(1)
    return s, None

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
 ('マルチ指示計','マルチ指示計',True),('mda','マルチ指示計',True),('多機能電力計','マルチ指示計',True),('マルチt/d','マルチT/D',True),
 ('電圧計','電圧計',True),('電流計','電流計',True),('電力計','電力計',True),('力率計','力率計',True),
 ('vm','VM',True),('am','AM',True),('vs','VS',True),('as','AS',True),
 ('whm','WHM',False),('mgs','MGS',True),('pas','PAS',True),('vcb','VCB',True),('vct','VCT',True),
 ('vmc','VCS',True),('vcs','VCS',True),('sog','SOG',True),
 ('lbs','LBS',True),('vgb','VCB',True),('ds','DS',True),('ch','CH',True),('l-s','L-S',False),('インバータ','INV',False),('inv','INV',False),
 ('ocr','OCR',True),('dgr','DGR',True),('rpr','RPR',True),('ovgr','OVGR',True),
 ('lg-ry','LG-RY',True),('lgr','LGR',True),('zpd','ZPD',True),('zctt','ZCTT',True),('zct','ZCT',True),
 ('vtt','VTT',True),('ctt','CTT',True),
 ('m)lug','M)LUG',True),('m)mcb','M)MCB',True),('m)elb','M)ELB',True),('b)mcb','B)MCB',True),('b)elb','B)ELB',True),
 ('mcb','MCB',True),('elb','ELB',True),('vt','VT',True),('ct','CT',True),('pf','PF',True),('la','LA',True),('sc','SC',True),('srx','SR',True),('sr','SR',True),('tr','TR',True),
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
    {'name':'マルチ指示計','aliases':['マルチ指示計','mda','多機能電力計','デジタルマルチメーター'],'prefix':True},
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
    {'name':'VCS','aliases':['vcs','vmc'],'prefix':True},
    {'name':'SOG','aliases':['sog'],'prefix':True},
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
    {'name':'SR','aliases':['sr','srx'],'prefix':True},
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
    # 変圧器(TR): 「T:」始まり、または 相数φ+KVA を持つものはTRとして最優先判定。
    # (二次電圧の "S:210V" が "6600vs210" のように VS と誤マッチするのを防ぐ)
    if _re.match(r't\s*[:：]', n) or (_re.search(r'[13]φ', n) and _re.search(r'\d+\s*kva', n) and 'kvar' not in n):
        # SC/SR(コンデンサ/リアクトル)は別系列なので除外
        if not _re.search(r'(^|[^a-z])(sc|sr)([^a-z]|$)', n):
            return 'tr','TR',False
    # 部品→別名リストで照合（同じ部品の英語/日本語/略称をまとめて判定）
    for part in _PARTS:
        for alias in part['aliases']:
            nk=norm(alias)
            if len(nk)<=2:
                # 短い別名(as/vs/am/vm/ct/vt等)は単語境界でのみマッチ
                if _re.search(r'(?<![a-z])'+_re.escape(nk)+r'(?![a-z])', n):
                    # DB照合には別名でなく正式名(part['name'])を返す。
                    # (例: VMCで検出→DB品名は'VCS'始まりなので'VCS'で照合する)
                    return part['name'], part['name'], part['prefix']
            else:
                if nk in n:
                    return part['name'], part['name'], part['prefix']
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
    kw=_get_kw(n)
    # 容量(kva/kvar)は小数点を保持するため元文字列から抽出(normは小数点を削るため)
    _km=_re.search(r'(\d+\.?\d*)\s*k?va[r]?', qname, _re.I)
    kva=_km.group(1) if _km else _get_kva(n)
    mr=_re.search(r'(\d+)/5a',n); ratio=mr.group(1) if mr else None
    af=_re.search(r'(\d+)af',n); af=af.group(1) if af else None
    dvals=attr_value_set(qname, '')
    meta=dict(qty=qty,main=main_label,main_kw=main_kw,opts=opts,vb=vb,kw=kw,kva=kva,ratio=ratio,af=af,dvals=dvals)
    out=[]
    for d in DB:
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

    # --- TR/SC/SR 容量選定: 仕様値「以上」かつ「最も近い」コードを選ぶ ---
    # 区分: 図面に「支給」明記→支給品 / 「スペース」明記→スペース / 既定→購入品
    #   ただし高圧TR/SC/SRには購入品コードが無いため、既定でも支給品系を使う。
    # 容量は仕様以上で最小(最近傍上位)。完全一致→◎ / 繰上げ→○ / 超過→△
    _grp=None
    if meta['main']=='TR': _grp='TR'
    elif meta['main']=='SC': _grp='SC'
    elif meta['main']=='SR': _grp='SR'
    if _grp and meta.get('kva'):
        try:
            want=float(meta['kva'])
            grp_name=_grp; vb=meta.get('vb')
            nm_in=str(name)
            nn_in=norm(nm_in)
            is_shikyu=('支給' in nm_in)
            is_space=('スペース' in nm_in or 'SP' in nm_in)
            # 区分ラベル(品名接頭の判定用)
            if is_shikyu: kbn, kbn_label = '支給品','支給品'
            elif is_space: kbn, kbn_label = 'スペース','スペース'
            else: kbn, kbn_label = '購入','購入品'
            # 高圧/低圧は品名の電圧表記から直接判定(vbは不正確なため使わない)。
            #   高圧: 6.6kV/6600V/7.2kV/7.02kV/3.3kV 等の kV表記、または 200kVA級の高圧SC
            #   低圧: 200V/400V/105V/210V の明示
            is_lv=bool(re.search(r'(^|[^.\d])(100|105|200|210|400|415)v(?![a-z])', nn_in))
            # kV(高圧電圧)判定: kvの直後がa(kva)やr等の英字でないこと。kvar/kvaを除外。
            is_hv=bool(re.search(r'\d\.?\d*kv(?![a-z])', nn_in)) or bool(re.search(r'6600v|3300v', nn_in))
            if is_lv: is_hv=False  # 低圧電圧明示が最優先
            if not is_hv and not is_lv:
                # 電圧表記が無い場合: TR/高圧SCはkVA表記(高圧)、低圧SC/SRはkvar+電圧で既に判定済
                is_hv = (grp_name in('TR',) ) or (grp_name=='SC' and 'kvar' not in nn_in)
            # TRの一次が高圧(6.6kV/6600V/3.3kV/7.2kV等)なら高圧トランスとして確定。
            # 二次側の低圧電圧(210V/105V等)に惑わされない(is_lvの打消しより優先)。
            # ※norm後は小数点が消えるため "66kv"(6.6kV)/"72kv"(7.2kV)/"33kv"(3.3kV)で照合。
            if grp_name=='TR' and re.search(r'(6600v|66kv|3300v|33kv|72kv|702kv|\dkv/)', nn_in):
                is_hv=True; is_lv=False
            # 系統(ライン)の電圧文脈で判定: 高圧コンデンサ盤のSR/SCは、
            # 前後の接続機器(SC本体・VMC)が高圧7.02kVなので、機器に234V等の
            # 低圧表記があっても系統は高圧。低圧表記に引きずられず高圧(45系)とする。
            if grp_name in ('SR','SC') and ('高圧' in str(panel) and 'コンデンサ' in str(panel)):
                is_hv=True; is_lv=False
            unit='kVA' if grp_name=='TR' or (grp_name=='SC' and is_hv) else 'kvar'

            # 高圧で購入指定だが購入品が無い→支給品代替。ラベルを明示。
            if is_hv and kbn=='購入':
                kbn_label='支給品(高圧は購入品なし)'

            def _match_series(d):
                """この行が(grp/区分/電圧帯/相数)に合致するか"""
                n0=d['name']
                if not n0.startswith(grp_name): return False
                hv_row=(d['code'][:2]=='45')
                if is_hv and not hv_row: return False
                if (not is_hv) and hv_row: return False
                # 区分: 支給品/スペース/購入(=どちらの語も付かない素のコード)
                has_shikyu='支給品' in n0; has_space='スペース' in n0 or '(SP)' in n0
                if kbn=='支給品' and not has_shikyu: return False
                if kbn=='スペース' and not has_space: return False
                if kbn=='購入':
                    if is_hv:
                        # 高圧は購入品が無いので支給品を代替採用
                        if not has_shikyu: return False
                    else:
                        # 低圧購入品は素のコード(支給品/スペース語なし)
                        if has_shikyu or has_space: return False
                # 低圧の電圧(200V/400V)一致
                if not is_hv:
                    vm=re.search(r'(200|400)v', nn_in)
                    if vm and f"{vm.group(1)}V" not in n0: return False
                # TRの相数
                if grp_name=='TR':
                    if re.search(r'3φ',nn_in) and '3φ3W' not in n0: return False
                    if re.search(r'1φ',nn_in) and '1φ3W' not in n0: return False
                # SRのL%
                if grp_name=='SR' and is_hv:
                    lm=re.search(r'l\s*=?\s*(\d+)', nn_in)
                    if lm and f"L={lm.group(1)}%" not in n0 and f"={lm.group(1)}%" not in n0: return False
                return True

            pool=[]
            for d in DB:
                if not _match_series(d): continue
                mm=re.search(r'(\d+\.?\d*)\s*kva[r]?', d['name'], re.I)
                if mm: pool.append((float(mm.group(1)), d['code']))
            if pool:
                ge=sorted([(v,c) for v,c in pool if v>=want-1e-6])
                if ge:
                    v0,c0=ge[0]
                    if abs(v0-want)<1e-6:
                        return R(c0,'◎',f'{grp_name}容量一致({v0:g}{unit}・{kbn_label})')
                    return R(c0,'○',f'{grp_name}容量繰上げ(仕様{want:g}→{v0:g}{unit}・{kbn_label})')
                vmax=max(v for v,_ in pool)
                return R('','△',f'{grp_name}容量{want:g}{unit}が{kbn_label}DB最大({vmax:g}{unit})超過・要確認')
            return R('','△',f'{grp_name} {kbn_label}・該当容量コードなし・要確認')
        except (ValueError,TypeError):
            pass

    # --- メイン機器が辞書で特定できない場合は△（誤った確定を出さない）---
    # 重要原則: 機器が特定できないときは選定コード欄を空(—)にする。
    # 候補先頭を入れると無関係コード(SR/低圧TR/SC/MGS/LA等)が選定欄に出て誤誘導するため。
    if not meta.get('main'):
        if cands:
            hint=cands[0]['code']
            return R('','△',f'機器未特定・要確認(参考候補{len(cands)}件: 先頭={hint})')
        return R('','△','該当機器が辞書に無い・要確認')
    # 高圧DS(断路器): 単極用と3極用がある。極数明記が無い場合、
    # 「単極をN個」か「3極を1個」かの判断が要るため△で両論を注記する。
    if re.search(r'(?<![a-z])ds(?![a-z])', norm(name)) and not re.search(r'[123]p', norm(name)):
        amp=re.search(r'(\d+)a(?![a-z])', norm(name))
        if amp:
            a=amp.group(1)
            c1=next((c['code'] for c in cands if '1P' in c['name'] and f'{a}A' in c['name'] and '標準' in c['name']),'')
            c3=next((c['code'] for c in cands if '3P' in c['name'] and f'{a}A' in c['name'] and '標準' in c['name']),'')
            if c1 or c3:
                return R(c3 or c1,'△',f'DS極数要確認: 単極{a}A({c1})×N個 or 3極{a}A({c3})×1個')
    # LGR/LG-RY: 図面に「2段警報」明記が無ければ ZCT付(46401)を標準採用。
    if re.search(r'(?<![a-z])(lgr|lg-ry)(?![a-z])', norm(name)):
        if '2段' not in str(name) and '警報' not in str(name):
            if '46401' in byCode: return R('46401','◎','LGR ZCT付(標準)で確定')
        else:
            if '46403' in byCode: return R('46403','○','LGR 2段警報で確定')
    # --- 候補数で信頼度を決定 ---
    if not cands:
        return R('','△','該当コードなし・要確認')
    # VMC(真空電磁接触器)はVCSと解釈してコードを当てるが、図面表記との差異があるため
    # 確定にせず要確認(△)とする(ユーザー方針)。
    if re.search(r'(?<![a-z])vmc(?![a-z])', norm(name)):
        return R(cands[0]['code'],'△',f'VMC→VCSと解釈・要確認({byCode.get(cands[0]["code"],{}).get("name","")[:24]})')
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
    # --- 属性駆動チェック: 部品の必要属性が図面表記に揃っているか検査 ---
    # 不足があれば missing に記録。確信度◎なら○へ下げ要確認の材料にする。
    try:
        ml=(meta.get('main') or '').upper()
        spec=ATTR_TABLE.get(ml) or ATTR_TABLE.get(meta.get('main',''))
        if spec and sel.get('code'):
            nm_in=str(name)
            pats=spec.get('patterns',{})
            # 必須属性: 不足なら確信度を下げる。任意属性: 記録のみ。
            miss_req=[a for a in spec.get('required',[]) if pats.get(a) and not re.search(pats[a],nm_in,re.I)]
            miss_opt=[a for a in spec.get('optional',[]) if pats.get(a) and not re.search(pats[a],nm_in,re.I)]
            if miss_req:
                sel['missing_attrs']=miss_req
                if sel['conf']=='◎':
                    sel['conf']='○'
                    sel['note']=f"必須属性不足({'/'.join(miss_req)})・要確認: "+sel.get('note','')
            if miss_opt:
                sel['missing_opt']=miss_opt
    except Exception:
        pass
    return sel

def _amp_af(n):
    """AF(フレーム)とAT(トリップ)を抽出。AFを優先的に枠として使う。
    対応表記:
      ・「225af/200at」 → af=225, at=200 (明示)
      ・「mcb3p225/150」「3p225/150」 → 極数表記直後の 数字/数字 を AF/AT とみなす
      ・「mcb3p225」 → AF=225 のみ
    重要: norm()で空白が消えるため「50/30 1.5kW」→「50/3015kw」のように容量が
    連結する。AF/ATは規格アンペア値しか取らないので、トリップ側は標準AT値に
    厳密一致する場合のみ採用し、容量・幹線番号の混入(3015,5044,1502等)を排除する。"""
    import re as _re
    # 標準アンペア値(AF/ATが取りうる値)。これ以外は容量/幹線番号の混入とみなす
    STD={'15','20','30','40','50','60','75','100','125','150','175','200','225',
         '250','300','350','400','500','600','700','800','1000','1200','1600',
         '2000','2500','3200'}
    def _clean_at(v):
        if not v: return None
        if v in STD: return v
        # 連結ゴミ: 先頭から標準値に一致する最長の接頭辞を試す(例 3015→30, 1502→150)
        for L in (4,3,2):
            if len(v)>=L and v[:L] in STD: return v[:L]
        return None
    # 1) 明示AF/ATが最優先
    af=_re.search(r'(\d+)af', n); at=_re.search(r'(\d+)at', n)
    af_v=af.group(1) if af else None
    at_v=at.group(1) if at else None
    if af_v:
        return af_v, _clean_at(at_v)
    # 2) 極数表記(2p/3p/4p)の直後の「AF(/AT)」。AFは標準値、ATも標準値のみ採用
    m=_re.search(r'[234]p\s*(\d{2,4})(?:\s*/\s*(\d{2,4}))?', n)
    if m:
        af_c=m.group(1)
        at_c=_clean_at(m.group(2)) or _clean_at(at_v)
        return af_c, at_c
    return af_v, _clean_at(at_v)

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
        # まず「数字/数字」ペア(AF/AT)を探す。無ければ極数除去後の最初の数値群。
        mp=_re.search(r'(\d{2,4})\s*/\s*(\d{2,4})', n)
        if mp:
            af=mp.group(1); at=at or mp.group(2)
        else:
            m=_re.search(r'(\d{2,4})', n.replace('3p','').replace('2p','').replace('4p',''))
            af=m.group(1) if m else None
    if not af: return ''
    # AF枠の標準化: 標準枠(50/100/225/400/600/800)以外なら、ATを収容する最小枠へ
    STD_AF=[50,100,225,400,600,800,1000,1200,1600,2000,2500,3200]
    try:
        afi=int(af)
        if afi not in STD_AF:
            # ATは「AF以下」かつ「標準枠上限以内」の妥当値のみ採用。
            # 容量混入等で AT>AF や AT>3200 の異常値はAFを基準にする(暴走防止)。
            at_i=int(at) if at else None
            base_val = at_i if (at_i and at_i<=afi and at_i<=STD_AF[-1]) else afi
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

# ===== フェーズ3: AI絞り込み（DB候補内のみ・生成禁止）=====
def ai_pick(cli, raw):
    n=norm(raw); toks=[w for w in re.split(r'[\s\u3000()（）]',raw) if len(w)>=2]
    cand=[d for d in DB if any(norm(tk) in norm(d['name']) for tk in toks)][:25]
    if not cand: return dict(code='',name='',conf='△',note='DB候補なし・初見/要確認')
    cand_txt="\n".join(f"{c['code']}\t{c['name']}\t{c['kind']}" for c in cand)
    # 確信度も返させる（HIGH=ほぼ確実 / LOW=自信なし）。形式: 「コード,確信度」
    prompt=f"""図面の機器表記「{raw}」に最も合致する積算コードを下記候補から1つ選び、確信度も答えてください。
候補に適切なものが無ければ NONE。候補外コードの創作は禁止。
回答形式は「コード,HIGH」または「コード,LOW」または「NONE」のみ。説明不要。
HIGH=表記と候補が明確に一致し確信できる / LOW=候補はあるが断定できない
候補:
{cand_txt}"""
    try:
        msg=cli.messages.create(model=MODEL,max_tokens=20,messages=[{"role":"user","content":prompt}])
        out="".join(b.text for b in msg.content if b.type=="text").strip()
        m=re.match(r'\s*(\d+|none|NONE)\s*,?\s*(HIGH|LOW)?', out, re.I)
        if m:
            ans=m.group(1); conf_lv=(m.group(2) or 'LOW').upper()
            if ans in byCode:
                # 確信度HIGH → ○に格上げ。LOW → △のまま（人確認）
                if conf_lv=='HIGH':
                    return dict(code=ans,name=byCode[ans]['name'],conf='○',note='AI選定(確信度高)')
                return dict(code=ans,name=byCode[ans]['name'],conf='△',note='AI候補内選定(要人確認)')
    except Exception: pass
    return dict(code='',name='',conf='△',note='要確認')

# ===== 統合処理 =====
_EXTRACT_CACHE={}  # 入力ハッシュ→抽出結果。同じ図面は同じ結果を返す(再現性の保険)
def extract_panels(fname, data_bytes):
    import hashlib
    key=hashlib.sha256(data_bytes).hexdigest()
    if key in _EXTRACT_CACHE:
        return _EXTRACT_CACHE[key]
    cli=client()
    res=extract_input(cli, fname, data_bytes)
    _EXTRACT_CACHE[key]=res
    return res

# ===== 付属品の親吸収（二重計上の防止） =====
# 同一盤内で、親機器が付属検出器を内蔵している場合、単独計上された付属行を
# 「計上対象外(—/△)」に落として二重計上を防ぐ。消さずに残し理由を明記(トレーサビリティ)。
def _is_standalone_zct(row):
    """行が『単独のZCT/ZCTT』か。ZCT内蔵型の継電器(LG-RY/EL-RY等)や、
    名称にZCT以外の主機器を含むものは対象外(誤吸収防止)。"""
    nm=norm(row.get('raw',''))
    if not re.search(r'(?<![a-z])zct{1,2}(?![a-z])', nm):  # zct / zctt
        return False
    # 主機器名を伴う場合は付属でなく本体扱い → 吸収しない
    if re.search(r'(lgr|lg-ry|el-ry|igr|dgr|ry|mcb|elb|lbs|vcb|tr|変圧器)', nm):
        return False
    return True

def _has_zct_builtin_relay(rows):
    """盤内に ZCT内蔵型の地絡継電器(LG-RY/EL-RY 等、DB spec 'ZCT付'/'ZCT含')が
    選定されている行があれば True。"""
    for r in rows:
        code=r.get('code','')
        d=byCode.get(code,{})
        nm=d.get('name','')+d.get('spec','')
        if code and ('ZCT付' in nm or 'ZCT含' in nm or 'LG-RY' in d.get('name','')):
            return True
    return False

def absorb_accessories(rows):
    """盤内の付属吸収を適用。現状はB案件: LGR(ZCT付)内蔵時の単独ZCTを非計上にする。"""
    if _has_zct_builtin_relay(rows):
        for r in rows:
            if _is_standalone_zct(r):
                r['code']=''
                r['conf']='—'
                r['note']='LGR(ZCT付)に内蔵のため計上対象外(二重計上防止)'
                r['absorbed']=True
    return rows

def select_from_extracted(data):
    out=[]
    # 受変電部で上段に出たマルチ指示計のコードを記憶し、下段のV/電流計に継承する。
    multi_meter_code=None
    for p in data.get('panels',[]):
        rows=[]
        prev_is_main=False
        panel_nm=p.get('panel','')
        is_jushaden = ('受電' in panel_nm or '受変電' in panel_nm or '高圧' in panel_nm)
        for it in p.get('items',[]):
            nm=it.get('name','')
            # 負荷明細行(親分岐MCBの負荷内訳)は、直前の機器行(親)に集約してリストから外す。
            # (ケーブル判定より先に処理。負荷名称末尾にケーブルサイズが付くため)
            if it.get('load_detail'):
                load_nm=re.split(r'\s', nm.strip())[0] if nm.strip() else ''
                if rows:
                    rows[-1].setdefault('loads',[]).append(load_nm or nm.strip())
                continue
            # ケーブル・電線類は機器でないので計上対象外(リストから除外)
            # FP/FPT/CVT/CV/VVF/KIV/HIV等の電線。EV(エレベータ負荷)等と誤判定しないよう限定。
            if it.get('cable') or re.match(r'^\s*(\d+k?v\s+)?(fpt|cvt|cvv|vvf|kiv|hiv|fp|cv)\s*\d', norm(nm)) \
               or re.search(r'(fpt|cvt|vvf)\s*\d', norm(nm)):
                continue
            # 計器ヒューズ(F / F×N)は安価な付随小物なので計上対象外。
            # 「F×3」等はFが3個の意味。PF(パワーヒューズ=限流ヒューズ)は計上対象なので残す。
            _n=norm(nm)
            if not _n.startswith('pf'):  # PF(パワーヒューズ)は除外しない
                # 先頭がF(後ろが×N/数字/空白/末尾)、または「計器ヒューズ」表記
                if re.match(r'^f([x×]\s*\d+)?(\s|$|\d)', _n) or '計器ヒューズ' in nm:
                    continue
            # 計器ヒューズ(F/Fx/計器ヒューズ)は安価な付随小物なので計上対象外。
            # 限流ヒューズPF(高圧コンデンサ盤の40A級等)とは区別する。
            # norm後は空白除去・長音消失("計器ヒューズ"→"計器ヒュズ", "Fx2 3A"→"fx23a")。
            nmn=norm(nm)
            if re.fullmatch(r'f|fx\d*|fx\d*\d+a|計器ヒュ?ズ|計器用ヒュ?ズ', nmn) \
               or (nmn.startswith('fx') and 'pf' not in nmn):
                continue
            # 計器ヒューズ(F/Fx/計器ヒューズ)は安価な付随小物なので計上対象外。
            # ただし限流ヒューズPF(高圧40A級など)は計上対象なので除外しない。
            nn_chk=norm(nm)
            if not re.search(r'(?<![a-z])pf(?![a-z])', nn_chk):  # PFを含まないこと
                if re.match(r'^\s*f(x|\d|\b)', nn_chk) or '計器ヒューズ' in nm or re.match(r'^\s*f付', nn_chk):
                    continue
            # 数量サフィックス(x2等)を分離し、品名はクリーン版で選定
            cleaned, qsuf = split_qty_suffix(nm)
            # 品名に「×N」がある場合はそれを数量の正とする(抽出側の数量より優先)
            if qsuf: it['qty']=qsuf
            sel=select_one(cleaned if qsuf else nm, p.get('panel',''), prev_is_main, it.get('volt',''), it.get('symbol',''), it.get('kw',''), it.get('group',''))
            nn=norm(nm)

            # --- マルチ指示計(MDA)の型継承(案件全体・盤またぎ) ---
            # 受電盤等でマルチ指示計(42075-42088)が出たら型を記憶。型式は図面で確定困難なため△。
            if sel.get('code','')[:3]=='420' and 'マルチ指示計' in byCode.get(sel['code'],{}).get('name',''):
                multi_meter_code=sel['code']
                sel['conf']='△'
                sel['note']='マルチ指示計(MDA)・型式要確認(' + byCode.get(sel['code'],{}).get('name','')[:18] + ')'
            # 上位にMDA(マルチ指示計)がある場合、以降のどの盤の計器(電圧計V/電流計A/
            # 切換スイッチTHR=VS/AS)もMDAに統一する(盤またぎ・型式要確認△)。
            # ただしTR・変圧器・SC・SR等の明確な機器は継承対象にしない(誤統一を防ぐ)。
            elif multi_meter_code and not re.search(r'(tr|変圧器|sc|sr|mcb|elb|lbs|vcb|vcs|vmc|pas|ds|lgr|zct|ct|vt|pf|kva|kvar|kw)', nn) and (
                    sel.get('code','') in ('71001','71002','98802','98803','42001','42002','42003','42014','74275','74276')
                    or re.fullmatch(r'(thr|vs|as|v/s|a/s|切換スイッチ|切換sw|vm|am|vm/am|v|a|電圧計|電流計|計器\(?[va/]+\)?)', nn)):
                mm=byCode.get(multi_meter_code,{}).get('name','')
                sel=dict(code=multi_meter_code, name=mm, conf='△',
                         note=f'上位MDA(マルチ指示計)に統一・型式要確認({mm[:18]})')

            prev_is_main = bool(re.search(r'm\)?(mcb|lug)', nn)) and ('tb' not in nn[:3])
            if it.get('unclear') and sel['conf']!='△':
                sel['conf']='△'; sel['note']='図面読取不明瞭・'+sel['note']
            row=dict(sel)
            row['raw']=it.get('name','')
            row['qty']=it.get('qty','')
            row['load_detail']=False
            # 幹線番号(1L1,2M1,1EM2等)を抽出し、表示用に〈〉でくくる
            mfeed=re.match(r'\s*(\d+[A-Za-z]+\d*)', it.get('name','') or '')
            row['feed']=mfeed.group(1) if mfeed else ''
            rows.append(row)
        # 付属品の親吸収(二重計上防止): 盤内で確定後にまとめて適用
        rows=absorb_accessories(rows)
        # 各行の表示名「部品名＋仕様 〈幹線〉(負荷名称)」を組み立てる
        for r in rows:
            base=r.get('raw','')
            feed=r.get('feed','')
            # 幹線番号を raw 先頭から除いた本体を部品名＋仕様とする
            body=base
            if feed and base.startswith(feed):
                body=base[len(feed):].strip()
            disp=body
            if feed: disp=f'{body} 〈{feed}〉'
            loads=r.get('loads',[])
            if loads: disp=f'{disp}({", ".join(loads)})'
            r['display']=disp.strip()
        out.append(dict(panel=p.get('panel',''),rows=rows))
    return out

def make_excel(panels):
    FONT='Meiryo'; thin=Side(style='thin',color='BBBBBB'); bd=Border(left=thin,right=thin,top=thin,bottom=thin)
    hf=PatternFill('solid',start_color='1E3A28')
    cf={'◎':PatternFill('solid',start_color='E8F0E8'),'○':PatternFill('solid',start_color='FFF8E0'),'△':PatternFill('solid',start_color='FCE4E4')}
    wb=Workbook(); ws=wb.active; ws.title='選定結果'
    ws.append(['盤','図面表記','数量','選定コード','正式品名(DB)','判定','根拠・確認事項'])
    for c in ws[1]:
        c.font=Font(name=FONT,bold=True,color='FFFFFF'); c.fill=hf; c.border=bd; c.alignment=Alignment(horizontal='center',wrap_text=True)
    nok=nw=nc=ndetail=0; prev=None
    for p in panels:
        for r in p['rows']:
            is_detail=r.get('load_detail')
            ws.append([p['panel'],r['raw'],r['qty'],r['code'] or '—',byCode.get(r['code'],{}).get('name','') if r['code'] else '',r['conf'],r['note']])
            row=ws[ws.max_row]
            for c in row: c.font=Font(name=FONT,size=9); c.border=bd; c.alignment=Alignment(vertical='center',wrap_text=True)
            if p['panel']!=prev: row[0].font=Font(name=FONT,size=9,bold=True); row[0].fill=PatternFill('solid',start_color='F0F0F0'); prev=p['panel']
            if is_detail:
                # 負荷明細行: グレーでインデント表示、計上対象外
                for c in row: c.font=Font(name=FONT,size=9,italic=True,color='999999')
                row[1].alignment=Alignment(vertical='center',wrap_text=True,indent=2)
                ndetail+=1
                continue
            row[5].fill=cf.get(r['conf'],PatternFill()); row[5].alignment=Alignment(horizontal='center'); row[3].font=Font(name=FONT,size=9,bold=True)
            if r['conf']=='◎': nok+=1
            elif r['conf']=='○': nw+=1
            else: nc+=1
    for i,w in enumerate([18,34,7,12,30,8,40],1): ws.column_dimensions[chr(64+i)].width=w
    ws.freeze_panes='A2'; ws.auto_filter.ref=f'A1:G{ws.max_row}'
    tot=nok+nw+nc or 1
    ws2=wb.create_sheet('集計',0)
    ws2.append(['積算コード選定システム v1.6 結果']); ws2['A1'].font=Font(name=FONT,bold=True,size=13); ws2.append([])
    for lab,val in [('抽出機器数',tot),('◎ 確定',nok),('○ ほぼ確定',nw),('△ 要確認',nc),('自動確定率',f'{round((nok+nw)/tot*100)}%'),('負荷明細(計上対象外)',ndetail)]:
        ws2.append([lab,val])
    ws2.column_dimensions['A'].width=20; ws2.column_dimensions['B'].width=12
    buf=io.BytesIO(); wb.save(buf); buf.seek(0); return buf

# ===== ルート =====
@app.route('/')
@login_required
def index(): return Response(INDEX_HTML, mimetype='text/html')

@app.route('/api/health')
def health(): return jsonify(ok=True, db=len(DB), key=bool(os.environ.get('ANTHROPIC_API_KEY')))

# 【段階1】抽出のみ：図面→盤・機器リスト（コード選定はまだしない）
@app.route('/api/extract', methods=['POST'])
@login_required
def api_extract():
    # 複数ファイル対応: getlistで全ファイルを受け取り、各々抽出してpanelsを統合。
    files=request.files.getlist('file')
    if not files:
        f=request.files.get('file')
        files=[f] if f else []
    if not files:
        return jsonify(error='ファイルがありません'),400
    all_panels=[]; errors=[]; nfiles=0
    for f in files:
        if not f or not f.filename: continue
        fname=f.filename; raw=f.read(); low=fname.lower()
        if not low.endswith(('.pdf','.png','.jpg','.jpeg','.dxf','.zip')):
            errors.append(f'{fname}: 非対応形式(PDF/PNG/JPG/DXF/ZIP)')
            continue
        try:
            data=extract_panels(fname, raw)
            for p in data.get('panels',[]):
                # どのファイル由来か分かるよう、盤名にファイル名を付記(任意)
                all_panels.append(p)
            nfiles+=1
        except Exception as e:
            errors.append(f'{fname}: {e}')
    if not all_panels and errors:
        return jsonify(error=' / '.join(errors)),500
    nitems=sum(len(p.get('items',[])) for p in all_panels)
    return jsonify(panels=all_panels, count=nitems, npanels=len(all_panels),
                   nfiles=nfiles, warnings=errors)

# 【段階2】選定：抽出済みの盤・機器リスト→コード選定（◎○△）
@app.route('/api/select', methods=['POST'])
@login_required
def api_select():
    data=request.get_json()
    if not data or 'panels' not in data:
        return jsonify(error='抽出データがありません'),400
    try:
        panels=select_from_extracted({'panels':data['panels']})
    except Exception as e:
        return jsonify(error=str(e)),500
    c={'◎':0,'○':0,'△':0}
    ndetail=0
    for p in panels:
        for r in p['rows']:
            r['official']=byCode.get(r['code'],{}).get('name','') if r['code'] else ''
            r['name']=r.get('display') or r.get('raw','')
            # 候補を「コード（品名）」形式で最大5件用意(フロントのプルダウン用)
            cand_list=r.get('candidates',[]) or []
            r['cand_opts']=[{'code':cc['code'],
                             'label':f"{cc['code']}（{cc.get('name','')}）"}
                            for cc in cand_list[:5]]
            # 負荷明細行(conf='—')は計上対象外。集計母数に含めない。
            if r.get('load_detail') or r['conf'] not in c:
                ndetail+=1
                continue
            c[r['conf']]+=1
    tot=sum(c.values()) or 1
    return jsonify(panels=panels, summary=dict(total=sum(c.values()),ok=c['◎'],warn=c['○'],chk=c['△'],
        detail=ndetail, rate=round((c['◎']+c['○'])/tot*100)))

@app.route('/api/excel', methods=['POST'])
@login_required
def api_excel():
    panels=request.get_json().get('panels',[])
    buf=make_excel(panels)
    ts=datetime.datetime.now().strftime('%Y%m%d_%H%M')
    return send_file(buf,as_attachment=True,download_name=f'積算コード選定_{ts}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

INDEX_HTML=open(os.path.join(HERE,'index.html'),encoding='utf-8').read() if os.path.exists(os.path.join(HERE,'index.html')) else '<h1>index.html がありません</h1>'

if __name__=='__main__':
    port=int(os.environ.get('PORT','8000'))
    print(f'積算コード選定システム起動: http://localhost:{port}  (DB {len(DB)}件 / APIキー {"OK" if os.environ.get("ANTHROPIC_API_KEY") else "未設定"})')
    app.run(host='0.0.0.0',port=port,debug=False)
