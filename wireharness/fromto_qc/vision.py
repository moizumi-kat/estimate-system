# -*- coding: utf-8 -*-
"""
Vision（Claude）で結線をトレースし From-To を得る + 幾何との突合。

思想:
  DXFの幾何解析は「機器の入力側/出力側」を区別できず頭打ち（~30%）。
  Visionは人と同じく“線を目で追って”接続を読めるので、この壁を越えられる。
  ただしVisionは完全決定論でないので、幾何と突合し
    一致 → 確定 / 不一致 → 要確認(QC指摘)
  として使う（human-in-the-loop）。

  ANTHROPIC_API_KEY が必要。未設定なら trace_tile() は RuntimeError。
"""
import os
import re
import json
import base64

VISION_PROMPT = """あなたは制御盤のシーケンス図（ラダー/展開接続図）を読む配線技術者です。
この画像は1枚のDXFから描き起こした回路の一部です。次の凡例に従います。
  - 黒い実線 = 電線（結線）
  - 赤い丸 = 端子（機器の接続点）
  - 青い枠の文字 = 号線（線番。その電線の番号）
  - 濃紺の太字 = 機器記号（例 52-102, 43-102, 3-102, TB-102, GL-102）

画像内の各「号線(線番)」について、その電線が接続している機器と端子を、線を目で追って列挙してください。
線が分岐(T字)している場合は、そのネットに繋がる全ての端点を1つの号線にまとめること。
描かれていない接続を創作しないこと。読み取れない箇所は unclear:true を付けること。

出力はJSONのみ（説明文やコードフェンス不要）:
{
  "nets": [
    {"senban":"10205","ends":[{"device":"3-102","terminal":"入"},{"device":"52-102","terminal":"14"}],"unclear":false}
  ]
}
"""


def _client():
    if not os.environ.get('ANTHROPIC_API_KEY'):
        raise RuntimeError('ANTHROPIC_API_KEY 未設定（Visionトレースには必要）')
    from anthropic import Anthropic
    return Anthropic()


def trace_tile(png_path, model=os.environ.get('VISION_MODEL', 'claude-opus-4-8')):
    """タイル画像1枚をVisionでトレースし {"nets":[...]} を返す。"""
    cli = _client()
    data = base64.standard_b64encode(open(png_path, 'rb').read()).decode()
    block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}
    with cli.messages.stream(model=model, max_tokens=8000,
                             messages=[{"role": "user", "content": [block, {"type": "text", "text": VISION_PROMPT}]}]) as st:
        msg = st.get_final_message()
    txt = "".join(b.text for b in msg.content if b.type == "text").strip()
    txt = re.sub(r'^```(json)?|```$', '', txt, flags=re.M).strip()
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r'\{.*\}', txt, re.S)
        return json.loads(m.group(0)) if m else {"nets": []}


def cross_check(vision_nets, geom_model, alias=None):
    """Vision結果と幾何モデルのネットを号線で突合。
    戻り: 各号線について confirmed(両者一致) / vision_only / geom_only。"""
    from .geometry import norm
    alias = alias or {}

    def dset_v(net):
        return {alias.get(norm(e.get('device', '') + str(e.get('terminal', ''))),
                          norm(e.get('device', ''))) for e in net.get('ends', [])}

    geom_by_id = {}
    for n in geom_model.nets:
        if n['id']:
            geom_by_id.setdefault(n['id'], set()).update(alias.get(norm(s), norm(s)) for s in n['devices'])

    out = []
    for vn in vision_nets.get('nets', []):
        sid = vn.get('senban', '')
        v = {alias.get(norm(e.get('device', '')), norm(e.get('device', ''))) for e in vn.get('ends', [])}
        g = geom_by_id.get(sid, set())
        out.append({'senban': sid, 'vision': sorted(v), 'geom': sorted(g),
                    'agree': bool(v and g and v == g),
                    'status': 'confirmed' if (v and g and v == g) else ('vision_only' if not g else 'mismatch')})
    return out
