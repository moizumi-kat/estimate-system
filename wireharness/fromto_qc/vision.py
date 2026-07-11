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

【機器名の書き方（厳守）】
- device には図中の濃紺太字の機器記号だけを書く（例 "52-102", "43-102", "TB-102"）。
- 「上/下/左/右/接点/母線」などの位置語を機器名に混ぜないこと（それは terminal 側に書く）。
- 隣接する別ラベルを連結しないこと（各機器記号は独立して書く）。

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


def trace_tile_gemini(png_path, model=os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')):
    """タイル画像1枚をGemini Visionでトレースし {"nets":[...]} を返す。
    要 GEMINI_API_KEY（または GOOGLE_API_KEY）。google-genai SDK を使用。"""
    key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
    if not key:
        raise RuntimeError('GEMINI_API_KEY 未設定（Geminiトレースには必要）')
    from google import genai
    from google.genai import types
    cli = genai.Client(api_key=key)
    img = open(png_path, 'rb').read()
    resp = cli.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=img, mime_type='image/png'), VISION_PROMPT])
    txt = (resp.text or '').strip()
    txt = re.sub(r'^```(json)?|```$', '', txt, flags=re.M).strip()
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r'\{.*\}', txt, re.S)
        return json.loads(m.group(0)) if m else {"nets": []}


def clean_device(s):
    """Vision出力の機器名から位置語・ノイズを除去し正規化キーにする（ASCII英数字のみ）。"""
    import re
    from .geometry import norm
    return re.sub(r'[^A-Z0-9]', '', norm(s))


def trace_drawing(model, regions=None, dpi=140, tmpdir=None, cols=1, rows=3, pad=100, tracer=None):
    """図面全体をタイル分割してVisionでトレースし、号線でネットを統合して返す。
    tracer: タイル画像→{"nets":[...]} の関数（既定 trace_tile=Claude。trace_tile_gemini でGemini）。
    戻り: {senban: {"devices": set(), "terminals": [ {device,terminal} ...], "unclear": bool}}
    ※ 複数シート（シーケンス＋スケルトン）を跨ぐ号線は、各図面の結果を号線で merge すれば連結できる。
    """
    import os
    import tempfile
    from . import render
    tracer = tracer or trace_tile
    tmpdir = tmpdir or tempfile.mkdtemp(prefix='wh_tiles_')
    if regions is None:
        regions = render.smart_tiles(model)
    merged = {}
    for i, rg in enumerate(regions):
        png = os.path.join(tmpdir, f'tile_{i}.png')
        render.render_region(model, rg, png, dpi=dpi)
        v = tracer(png)
        for n in v.get('nets', []):
            sid = str(n.get('senban', '')).strip()
            if not sid:
                continue
            slot = merged.setdefault(sid, {'devices': set(), 'terminals': [], 'unclear': False})
            for e in n.get('ends', []):
                d = clean_device(e.get('device', ''))
                if d:
                    slot['devices'].add(d)
                    slot['terminals'].append({'device': d, 'terminal': e.get('terminal', '')})
            slot['unclear'] = slot['unclear'] or bool(n.get('unclear'))
    return merged


def merge_sheets(*traced):
    """複数図面(シーケンス/スケルトン)の trace_drawing 結果を号線で統合。"""
    out = {}
    for t in traced:
        for sid, slot in t.items():
            o = out.setdefault(sid, {'devices': set(), 'terminals': [], 'unclear': False})
            o['devices'] |= slot['devices']
            o['terminals'] += slot['terminals']
            o['unclear'] = o['unclear'] or slot['unclear']
    return out


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
