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
_CACHE = {}       # {set名: {cfg,tpls}}  ロード済みテンプレート
_SETS_DIR = os.path.join(_TPL_DIR, 'sets')   # 設計事務所/案件ごとの校正セット置き場

# 設計事務所によって記号のスタイル・寸法が異なる(実証: 安井建築=箱形/大, 松田平田=スイッチ形/小)。
# ルート(symbol_templates/)が既定セット。sets/<名前>/ に案件別の校正セットを置ける(two-layer構成)。


def _set_dir(tset=None):
    if tset:
        d = os.path.join(_SETS_DIR, tset)
        if os.path.isfile(os.path.join(d, 'config.json')):
            return d
    return _TPL_DIR


def list_sets():
    """利用可能な校正セット名の一覧(既定セットは 'default')。"""
    sets = ['default']
    try:
        if os.path.isdir(_SETS_DIR):
            for n in sorted(os.listdir(_SETS_DIR)):
                if os.path.isfile(os.path.join(_SETS_DIR, n, 'config.json')):
                    sets.append(n)
    except Exception:
        pass
    return sets


def available(tset=None):
    """cv2/numpy と設定が揃っていれば True。"""
    try:
        import cv2, numpy  # noqa
    except Exception:
        return False
    d = _set_dir(tset)
    return os.path.isdir(d) and os.path.isfile(os.path.join(d, 'config.json'))


def _load(tset=None):
    key = tset or '_default'
    if key in _CACHE:
        return _CACHE[key]
    import cv2, numpy as np
    d = _set_dir(tset)
    cfg = json.load(open(os.path.join(d, 'config.json'), encoding='utf-8'))
    tpls = {}
    for dev, c in cfg['devices'].items():
        im = cv2.imread(os.path.join(d, c['file']))
        if im is None:
            continue
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        tpls[dev] = {'g': g, 'color': tuple(c['color']), 'th': float(c['th']),
                     'name': c.get('name', dev), 'jis': c.get('jis', '')}
    entry = {'cfg': cfg, 'tpls': tpls}
    _CACHE[key] = entry
    return entry


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


def _detect_raw(gray, tset=None):
    """全機種をマッチングし、グローバルNMS後の検出リストを返す。"""
    import cv2, numpy as np
    c = _load(tset); cfg = c['cfg']; tpls = c['tpls']
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


def detect(png_bytes, tset=None):
    """検出結果のリストを返す。advisory。失敗時は []。
    各要素: {dev,name,jis,x,y,w,h,score,color:[r,g,b]} (x,y,w,hは画像px)。"""
    if not available(tset):
        return []
    try:
        import cv2, numpy as np
        arr = np.frombuffer(png_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return []
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        c = _load(tset); tpls = c['tpls']
        res = []
        for d in _detect_raw(gray, tset):
            t = tpls[d['dev']]; b = d['box']; bgr = t['color']
            res.append({'dev': d['dev'], 'name': t['name'], 'jis': t['jis'],
                        'x': b[0], 'y': b[1], 'w': b[2] - b[0], 'h': b[3] - b[1],
                        'score': round(d['score'], 3),
                        'color': [bgr[2], bgr[1], bgr[0]]})  # RGB
        return res
    except Exception:
        return []


def colorize(png_bytes, tset=None):
    """記号だけを機器別色で着色したPNGバイト列と、機種別検出数を返す。
    戻り値: (png_bytes or None, counts dict)。失敗時は (None, {})。advisory。"""
    if not available(tset):
        return None, {}
    try:
        import cv2, numpy as np
        arr = np.frombuffer(png_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None, {}
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        out = img.copy()
        c = _load(tset); tpls = c['tpls']
        counts = {}
        for d in _detect_raw(gray, tset):
            t = tpls[d['dev']]; b = d['box']
            _color_glyph(out, gray, b[0], b[1], b[2], b[3], t['color'])
            counts[t['name']] = counts.get(t['name'], 0) + 1
        ok, buf = cv2.imencode('.png', out)
        if not ok:
            return None, counts
        return buf.tobytes(), counts
    except Exception:
        return None, {}


def best_set(png_bytes, hi_th=0.85):
    """複数セットのうち、この図面に最も合うセット名を返す(自動校正)。
    設計事務所で記号スタイルが違うため、どのセットが合うかを選ぶ。
    判定は『検出数』でなく『高信頼検出数(スコア>=hi_th)』で行う
    (方針: 数が多い=良い ではない。別スタイルのテンプレは低スコアで誤検出が増えるだけ)。"""
    if not available():
        return 'default'
    try:
        best, bn = -1, 'default'
        for s in list_sets():
            tset = None if s == 'default' else s
            det = detect(png_bytes, tset)
            hi = sum(1 for d in det if d.get('score', 0) >= hi_th)
            if hi > best:
                best, bn = hi, s
        return bn
    except Exception:
        return 'default'


def auto_colorize(png_bytes):
    """最適セットを自動選択して着色する。戻り値: (png or None, counts, set名)。advisory。"""
    s = best_set(png_bytes)
    tset = None if s == 'default' else s
    png, counts = colorize(png_bytes, tset)
    return png, counts, s
