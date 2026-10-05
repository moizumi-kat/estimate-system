# -*- coding: utf-8 -*-
"""内部配置図(機器相番号/配置図)下部の『ロケーター対応表』を抽出する。

製造現場では、設計の実機器記号(例 86X103, 52STX103)に対し、内部配置図の下部で
A,B,C… の仮名称(ロケーター)を割り当て、ハーネスシート(From-To)ではその仮名称で
記載している(例: 86X→E, 52STX→F, CX→A)。この対応表を図面から取得し、
From-To 生成時に実機器→ロケーターへ変換することで、人のハーネスシートに合わせる。

対応表の体裁(DXFブロック属性):
  DEVICE1 = ロケーター(例 '2-E' = 基2のE), DEVICE2 = ':', DEVICE = 実機器(例 '86X103')
  同一行(y近接)に 左=ロケーター / 中=':' / 右=実機器 が並ぶ。複数列あることもある。
取得結果は「実機器ベース→ロケーター文字」と「(ベース,番号)→フルロケーター」の両方。
"""
import re
import ezdxf


def _split_actual(actual):
    """実機器文字列 '86X103' → (base='86X', number='103')。末尾の数字列を番号とみなす。"""
    s = str(actual or '').strip()
    m = re.match(r'^(.*?)(\d+)$', s)
    if m:
        return m.group(1), m.group(2)
    return s, ''


def _split_locator(loc):
    """ロケーター '2-E' → (基='2', letter='E')。'-'が無ければ (,'E')。"""
    s = str(loc or '').strip()
    if '-' in s:
        a, b = s.split('-', 1)
        return a.strip(), b.strip()
    return '', s


def extract_pairs(path):
    """1図面 → [(locator, actual)] のリスト(重複除去)。配置図の対応表ブロックから。"""
    try:
        doc = ezdxf.readfile(path)
    except Exception:
        return []
    msp = doc.modelspace()
    A = []
    for ins in msp.query('INSERT'):
        for a in (ins.attribs or []):
            s = (a.dxf.text or '').strip()
            if not s:
                continue
            try:
                x, y = round(a.dxf.insert.x, 1), round(a.dxf.insert.y, 1)
            except Exception:
                x, y = 0.0, 0.0
            if a.dxf.tag in ('DEVICE', 'DEVICE1', 'DEVICE2'):
                A.append((x, y, a.dxf.tag, s))
    pairs = []
    anchors = [(x, y) for (x, y, t, s) in A if t == 'DEVICE2' and s == ':']
    for (ax, ay) in anchors:
        loc = act = None
        locdx = actdx = 1e9
        for (x, y, t, s) in A:
            if abs(y - ay) > 4:
                continue
            if t == 'DEVICE1' and x < ax and (ax - x) < locdx:
                loc, locdx = s, ax - x
            if t == 'DEVICE' and x > ax and (x - ax) < actdx:
                act, actdx = s, x - ax
        # ロケーターは「N-文字」か1〜2文字の記号。実機器は英数字(数字を含む)。
        if loc and act and re.search(r'[A-Za-z]', loc) and re.search(r'\d', act):
            pairs.append((loc, act))
    # 重複除去(順序保持)
    seen = set()
    out = []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def build_map(dct_paths):
    """配置図群 → ロケーター対応。
    戻り: {
      'by_base'   : {実機器ベース(大文字): ロケーター文字},   例 {'86X':'E','52STX':'F','CX':'A'}
      'by_actual' : {(ベース大文字, 番号): フルロケーター},   例 {('86X','103'):'2-E'}
      'pairs'     : [(locator, actual), ...],                 生の対応
    }
    """
    by_base = {}
    by_actual = {}
    pairs_all = []
    for p in (dct_paths or []):
        for loc, act in extract_pairs(p):
            base, num = _split_actual(act)
            ki, letter = _split_locator(loc)
            B = base.upper()
            if B and letter:
                by_base.setdefault(B, letter)
                by_actual[(B, num)] = loc
                pairs_all.append((loc, act))
    return {'by_base': by_base, 'by_actual': by_actual, 'pairs': pairs_all}


def locator_for(lmap, device, no=''):
    """実機器(device, no) → ロケーター表記。対応が無ければ None。
    既定は『基-文字』(例 '2-E')。基が不明なら文字のみ(例 'E')。"""
    if not lmap:
        return None
    B = str(device or '').upper()
    num = str(no or '')
    full = lmap.get('by_actual', {}).get((B, num))
    if full:
        return full
    letter = lmap.get('by_base', {}).get(B)
    return letter


def apply_to_routed(routed, lmap, keep_actual=True):
    """route_and_length の結果(wires)の from/to 機器を、ロケーター対応があれば仮名称へ置換する。
    ・device = ロケーター(例 '2-E')、no = '' に正規化(人のハーネスシートに合わせる)。
    ・keep_actual=True のとき、元の実機器を 'actual' に退避(逆引き・確認用)。
    ・対応が無い機器(MCCB/51/52/TB 等)はそのまま。回帰ゼロ(対応表が無ければ不変)。
    戻り: 置換件数。routed はその場で更新。"""
    if not lmap or not lmap.get('by_actual'):
        return 0
    n = 0
    for w in routed.get('wires', []):
        for e in (w.get('from', {}), w.get('to', {})):
            loc = locator_for(lmap, e.get('device', ''), e.get('no', ''))
            if loc:
                if keep_actual:
                    e.setdefault('actual', f"{e.get('device','')}-{e.get('no','')}".strip('-'))
                e['device'] = loc
                e['no'] = ''
                n += 1
    return n
