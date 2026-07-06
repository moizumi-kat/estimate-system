#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""制御盤PoC: 図面を左(パターン集)/右(適用表)に分けて構造抽出できるか検証。"""
import sys, json, base64, fitz
import app
cli=app.client()

def crop_png(pdf, page_i, x0f, x1f, y0f, y1f, target=2000):
    doc=fitz.open(pdf); pg=doc[page_i]; r=pg.rect
    clip=fitz.Rect(r.width*x0f, r.height*y0f, r.width*x1f, r.height*y1f)
    zoom=target/max(clip.width,clip.height)
    return pg.get_pixmap(matrix=fitz.Matrix(zoom,zoom), clip=clip).tobytes("png")

def ask(png, prompt):
    src={"type":"base64","media_type":"image/png","data":base64.standard_b64encode(png).decode()}
    with cli.messages.stream(model="claude-opus-4-8",max_tokens=16000,
        messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image","source":src}]}]) as st:
        m=st.get_final_message()
    t="".join(b.text for b in m.content if b.type=="text").strip()
    import re
    t=re.sub(r'^```(json)?|```$','',t,flags=re.M).strip()
    try: return json.loads(t)
    except Exception:
        mm=re.search(r'\{.*\}',t,re.S)
        return json.loads(mm.group(0)) if mm else {"raw":t[:400]}

TABLE_PROMPT="""これは動力制御盤の「適用表(動力制御盤リスト)」です。各負荷行を漏れなく抽出しJSONで返してください。
列の意味:
- panel: 制御盤名称(M-1A等)。同じ盤の行はpanelを繰り返す
- load_no: 負荷番号(FO-101等)
- load: 負荷名称
- kw: 負荷容量(kW)
- kind: 関係器種別(●=MCCB / ○=ELB / 空)
- main: 結線図の「主回路」列のパターン記号(A〜Lの1文字。空欄可)
- ctrl: 結線図の「操作回路」列のパターン番号(1〜11。空欄可)
- breaker: 備考欄の遮断器表記(例 3P 100AF/60AT。無ければ空)
- note: その他備考
予備/スペース行も load='予備' 等で記録。読めない項目は値を空にしてunclear:trueを付ける。
出力: {"rows":[{...}]} のみ。説明不要。"""

png=crop_png("seigyo.pdf",0, 0.47,1.0, 0.05,0.72)   # p1 右上=適用表(M-1A/1B/1C)
res=ask(png, TABLE_PROMPT)
rows=res.get("rows",[])
print(f"=== 適用表 抽出: {len(rows)}行 ===")
for r in rows:
    print(f"  {r.get('panel',''):<6}{r.get('load_no',''):<10}{(r.get('load','') or '')[:16]:<17}"
          f"kW={r.get('kw','')!s:<6}種={r.get('kind','') or '-':<2}主={r.get('main','') or '-':<2}"
          f"操={r.get('ctrl','') or '-':<2} {r.get('breaker','')}")
