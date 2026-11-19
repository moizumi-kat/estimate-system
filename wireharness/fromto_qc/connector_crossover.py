# -*- coding: utf-8 -*-
"""盤間渡りコネクタ(R-F/L-F=制御 / 渡り用TB=太物)の自動生成。

茂泉様(古川電気)確定モデル:
  配線の組立は「一面(=内部配置パネルの盤＋その扉)」ごと。盤Nの機器から盤N+1の
  機器へ行く線は、盤境界で分割し、送り盤側=R-F(P)[制御] または 渡り用TB[太物]、
  受け盤側=L-F(R)[制御] または 渡り用TB[太物] に接続する。R-F/L-F・渡り用TBは
  図面に描かれず製造工程で用意する(= which signals cross は設計者知識)。

このモジュールの役割(◎誤答ゼロ):
  1. device→面 を図面から確定する:
       - 内部の制御リレー: 相番号(権威。DCTの盤下ロケーター対応表)で盤を確定。
       - 内部のその他機器 : 内部配置の盤見出しラベル('8-1'/'117-2'等)に最近傍で盤。
       - 扉付け機器       : 扉面の枠(縦長矩形)に含まれる面=盤。
  2. カットモデルで盤間渡り号線を検出(号線が繋ぐ機器の面が2面以上)。
  3. 渡りを型分類:繋ぐ MCCB が主端子(1,3,5/相 R,S,T,U,V,W,N)を使えば太物→渡り用TB、
     警報/補助/制御のみなら制御→R-F/L-F。
  4. 送り元(左の面)→受け先(右の面)で分割し、連結行を生成(採番は決定的 canonical)。

device→面 が確定できない機器(扉枠・盤ラベルの無い製番等)は面=None となり、その
号線は渡り判定に使わない(安全側=誤った渡りを出さない)。盤情報の無い図面では
検出ゼロ=既存出力に影響しない(回帰ゼロ)。
"""
import re
import collections

import ezdxf

from . import locator_map as LMAP

COLORS = {'*', '赤', '白', '青', '緑', '黒', '黄', 'E', '', None, 'SLD'}
# MCCB 主端子(電源)。これらに繋がる渡りは太物(渡り用TB)。
_MCCB_MAIN = set('123456') | {'R', 'S', 'T', 'U', 'V', 'W', 'N'}


def _norm(s):
    return re.sub(r'\s', '', str(s or '')).upper()


def _panel_cols(doc):
    """内部配置の盤見出しラベル('8-1'/'117-2'等)→ [(盤番号:int, x)]（最上段 y の行）。"""
    labs = collections.defaultdict(list)
    for e in doc.modelspace().query('TEXT MTEXT'):
        t = (e.plain_text() if e.dxftype() == 'MTEXT' else e.dxf.text).strip()
        m = re.match(r'^(\d+)-(\d+)$', t)
        if m:
            try:
                labs[round(float(e.dxf.insert[1]))].append((int(m.group(2)), float(e.dxf.insert[0])))
            except Exception:
                continue
    if not labs:
        return []
    return sorted(set(labs[max(labs)]), key=lambda c: c[1])


def _door_faces(doc, intern_xmax):
    """扉面の枠(縦長矩形 LWPOLYLINE)→ 左→右順の [(x0, x1)]（面1扉, 面2扉, …)。
    内部配置より右(x>intern_xmax)の、幅 600〜1100・高さ>1500 の矩形を扉枠とみなす。"""
    raw = []
    for e in doc.modelspace().query('LWPOLYLINE'):
        try:
            pts = [(p[0], p[1]) for p in e.get_points()]
        except Exception:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if 600 < w < 1100 and h > 1500 and min(xs) > intern_xmax:
            raw.append((round(min(xs)), round(max(xs))))
    # 入れ子/重複をまとめる(x0 が近いものは同一枠)
    frames = []
    for a, b in sorted(set(raw)):
        if not frames or a - frames[-1][0] > 300:
            frames.append((a, b))
    return frames


def build_device_face(dct_paths):
    """device(正規化 DEVICE, DEVICE1)→ 面(盤番号の文字列) の対応を図面から作る。
    戻り: {(DEVICE, DEVICE1): '1'|'2'|…}。盤情報が無ければ空 dict。"""
    dev_face = {}
    for p in (dct_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        cols = _panel_cols(doc)
        if not cols:
            continue
        intern_xmax = max(x for _, x in cols) + 400
        door_faces = _door_faces(doc, intern_xmax)
        # 相番号(権威): (base, num)->'2-I' の盤番号
        rel = {}
        for (b, n), loc in LMAP.build_map([p]).get('by_actual', {}).items():
            m = re.match(r'^(\d+)-', str(loc))
            if m:
                rel[(b, n)] = m.group(1)
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            d, d1 = a.get('DEVICE', ''), a.get('DEVICE1', '')
            if not d:
                continue
            x = float(ins.dxf.insert[0])
            key = (_norm(d), _norm(d1))
            if key in rel:
                face = rel[key]
            elif x <= intern_xmax:
                face = str(min(cols, key=lambda c: abs(c[1] - x))[0])
            else:
                hit = [i for i, (a0, a1) in enumerate(door_faces) if a0 - 50 <= x <= a1 + 50]
                face = str(hit[0] + 1) if hit else None
            if face:
                dev_face[key] = face
    return dev_face


def face_of(dev_face, rel_concat, device, no):
    """app の機器(device, no)→ 面。直接キー、連結名(DTAL+1=DTAL1)の順で引く。"""
    k = (_norm(device), _norm(no))
    if k in dev_face:
        return dev_face[k]
    joined = _norm(str(device) + str(no))
    for (b, _n), p in rel_concat.items():
        if b == joined:
            return p
    return None


def detect_crossings(wires, dev_face):
    """各号線が繋ぐ機器の面集合を作り、2面以上に跨る号線(=盤間渡り)を返す。
    戻り: {号線: set(面)}（跨ぐものだけ）。"""
    # 連結名フォールバック用(dev_face のキーを base 化)
    rel_concat = {(b, n): p for (b, n), p in dev_face.items()}
    g2p = collections.defaultdict(set)
    for w in wires:
        g = w.get('gousen')
        if not g or g in COLORS:
            continue
        for e in (w.get('from', {}), w.get('to', {})):
            p = face_of(dev_face, rel_concat, e.get('device'), e.get('no'))
            if p:
                g2p[g].add(p)
    return {g: ps for g, ps in g2p.items() if len(ps) >= 2}


def classify(gousen, wires):
    """渡り号線の型を返す: 'power'(太物→渡り用TB) か 'control'(制御→R-F/L-F)。
    繋ぐ MCCB が主端子(1,3,5/相)を使えば太物。警報/補助(ALc/ALa 等)のみなら制御。"""
    for w in wires:
        if w.get('gousen') != gousen:
            continue
        for e in (w.get('from', {}), w.get('to', {})):
            if _norm(e.get('device')) == 'MCCB':
                t = str(e.get('terminal', '')).strip().upper()
                if t and (t in _MCCB_MAIN or re.match(r'^[RSTUVWN]?[123456]$', t)):
                    return 'power'
    return 'control'


def generate(routed, dct_paths):
    """routed(wires)＋DCT から盤間渡りを検出・分類し、連結記述を返す。
    戻り: {
      'control': [(号線, 送り面, 受け面)…],   # R-F(P)/L-F(R) 対象
      'power'  : [(号線, 送り面, 受け面)…],   # 渡り用TB 対象
      'faces'  : 検出に使えた面の数,
    }
    device→面 が作れない(盤情報なし)図面では空(回帰ゼロ)。"""
    wires = routed.get('wires', [])
    dev_face = build_device_face(dct_paths)
    if not dev_face:
        return {'control': [], 'power': [], 'faces': 0}
    cross = detect_crossings(wires, dev_face)
    out = {'control': [], 'power': [], 'faces': len({f for ps in cross.values() for f in ps})}
    for g, faces in cross.items():
        fs = sorted(faces, key=lambda s: (len(s), s))
        send, recv = fs[0], fs[-1]          # 左(小さい面番号)=送り元, 右=受け先
        typ = classify(g, wires)
        out[typ].append((g, send, recv))
    out['control'].sort()
    out['power'].sort()
    out['rows'] = _rows(out['control'], out['power'])
    return out


def _rows(control, power):
    """検出した渡りを手本形式の連結行(端点行)に変換。
    制御=R-F(P)[送り面]/L-F(R)[受け面] の白-N芯(採番は面ペアごと canonical=送り先一致が要件で
    芯番号の規約は不要との茂泉様方針)。太物=渡り用TB[両面](相端子 N/U/V/W/R は電源構成依存の
    ため号線名を端子に置き、相の確定は確認ゲート/積算ソフト側)。
    戻り: [{'面','号線','機器','端子','型'}]。"""
    rows = []
    core = collections.defaultdict(int)         # (送り面,受け面) -> 芯カウンタ
    for g, s, r in control:
        core[(s, r)] += 1
        k = core[(s, r)]
        rows.append({'面': s, '号線': g, '機器': 'R-F(P)', '端子': f'白-{k}', '型': '制御'})
        rows.append({'面': r, '号線': g, '機器': 'L-F(R)', '端子': f'白-{k}', '型': '制御'})
    for g, s, r in power:
        rows.append({'面': s, '号線': g, '機器': '渡り用TB', '端子': g, '型': '太物'})
        rows.append({'面': r, '号線': g, '機器': '渡り用TB', '端子': g, '型': '太物'})
    return rows
