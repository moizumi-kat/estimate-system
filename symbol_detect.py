# -*- coding: utf-8 -*-
"""記号認識(テンプレートマッチング)による図面の機器記号 検出・着色。

重要: これは advisory 機能です。
 - 積算額・コード選定には一切関与しません(選定エンジン不変=回帰ゼロ)。
 - 確認ビューアで「どこに何の記号があるか」の当たり付けに使うだけ。
 - OpenCV(cv2)が無い環境では無効化され、例外を出さずに空を返します(本番を壊さない)。

記号の境界ルール(JIS/JEM規格で裏取り):
 記号本体(glyph)=接点/丸/箱/コイル。接続線(リード・配線)は記号ではない。
 → 着色はglyphのみ。貫通配線・母線はモルフォロジーで除外して黒のまま残す。
"""
import os, json

_HERE = os.path.dirname(os.path.abspath(__file__))
_TPL_DIR = os.path.join(_HERE, 'symbol_templates')
_CACHE = {}   # ロード済みテンプレート等のキャッシュ


def available():
    """cv2/numpy と設定が揃っていれば True。"""
    try:
        import cv2, numpy  # noqa
    except Exception:
        return False
    return os.path.isdir(_TPL_DIR) and os.path.isfile(os.path.join(_TPL_DIR, 'config.json'))


def _load():
    if _CACHE.get('ready'):
        return _CACHE
    import cv2, numpy as np
    cfg = json.load(open(os.path.join(_TPL_DIR, 'config.json'), encoding='utf-8'))
    tpls = {}
    for dev, d in cfg['devices'].items():
        p = os.path.join(_TPL_DIR, d['file'])
        im = cv2.imread(p)
        if im is None:
            continue
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        tpls[dev] = {'g': g, 'color': tuple(d['color']), 'th': float(d['th']),
                     'name': d.get('name', dev), 'jis': d.get('jis', '')}
    _CACHE.update(ready=True, cfg=cfg, tpls=tpls)
    return _CACHE


def _nms(boxes, scores, iou=0.35):
    import numpy as np
    if not boxes:
        return []
    b = np.array(boxes, float); s = np.array(scores)
    x1, y1, x2, y2 = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    a = (x2 - x1) * (y2 - y1); order = s.argsort()[::-1]; keep = []
    while order.size:
        i = order[0]; keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]]); yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]]); yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0, xx2 - xx1); h = np.maximum(0, yy2 - yy1); inter = w * h
        ov = inter / (a[i] + a[order[1:]] - inter + 1e-9)
        order = order[1:][ov < iou]
    return keep


def _detect_raw(gray):
    """全機種をマッチングし、グローバルNMS後の検出リストを返す。"""
    import cv2, numpy as np
    c = _load(); cfg = c['cfg']; tpls = c['tpls']
    scales = cfg.get('scales', [0.85, 1.0, 1.16])
    allb, alls, alld = [], [], []
    H, W = gray.shape[:2]
    for dev, t in tpls.items():
        tg = t['g']; th = t['th']
        for s in scales:
            tt = cv2.resize(tg, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            h, w = tt.shape[:2]
            if h >= H or w >= W:
                continue
            r = cv2.matchTemplate(gray, tt, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(r >= th)
            for x, y in zip(xs, ys):
                allb.append([int(x), int(y), int(x + w), int(y + h)])
                alls.append(float(r[y, x])); alld.append(dev)
    keep = _nms(allb, alls, cfg.get('nms_iou', 0.35))
    out = []
    for i in keep:
        out.append({'dev': alld[i], 'box': allb[i], 'score': alls[i]})
    return out


def _color_glyph(out_img, gray, x1, y1, x2, y2, color):
    """記号枠内の黒画素だけを着色。貫通配線(縦)・母線(横)の長い直線は除外。"""
    import cv2, numpy as np
    m = 7
    yy1 = max(0, y1 - m); xx1 = max(0, x1 - m)
    yy2 = min(gray.shape[0], y2 + m); xx2 = min(gray.shape[1], x2 + m)
    roi = gray[yy1:yy2, xx1:xx2]
    ink = (roi < 150).astype('uint8') * 255
    bh = y2 - y1; bw = x2 - x1
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(bh * 0.95) + m))
    vl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, vk)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (int(bw * 0.95) + m, 1))
    hl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, hk)
    sym = cv2.subtract(ink, cv2.bitwise_or(vl, hl))
    inner = np.zeros_like(sym); inner[m:m + bh, m:m + bw] = 255
    sym = cv2.bitwise_and(sym, inner)
    sym = cv2.dilate(sym, np.ones((2, 2), 'uint8'))
    out_img[yy1:yy2, xx1:xx2][sym > 0] = color


def detect(png_bytes):
    """検出結果のリストを返す。advisory。失敗時は []。
    各要素: {dev,name,jis,x,y,w,h,score,color:[r,g,b]} (x,y,w,hは画像px)。"""
    if not available():
        return []
    try:
        import cv2, numpy as np
        arr = np.frombuffer(png_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return []
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        c = _load(); tpls = c['tpls']
        res = []
        for d in _detect_raw(gray):
            t = tpls[d['dev']]; b = d['box']; bgr = t['color']
            res.append({'dev': d['dev'], 'name': t['name'], 'jis': t['jis'],
                        'x': b[0], 'y': b[1], 'w': b[2] - b[0], 'h': b[3] - b[1],
                        'score': round(d['score'], 3),
                        'color': [bgr[2], bgr[1], bgr[0]]})  # RGB
        return res
    except Exception:
        return []


def colorize(png_bytes):
    """記号だけを機器別色で着色したPNGバイト列と、機種別検出数を返す。
    戻り値: (png_bytes or None, counts dict)。失敗時は (None, {})。advisory。"""
    if not available():
        return None, {}
    try:
        import cv2, numpy as np
        arr = np.frombuffer(png_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None, {}
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        out = img.copy()
        c = _load(); tpls = c['tpls']
        counts = {}
        for d in _detect_raw(gray):
            t = tpls[d['dev']]; b = d['box']
            _color_glyph(out, gray, b[0], b[1], b[2], b[3], t['color'])
            counts[t['name']] = counts.get(t['name'], 0) + 1
        ok, buf = cv2.imencode('.png', out)
        if not ok:
            return None, counts
        return buf.tobytes(), counts
    except Exception:
        return None, {}
