# -*- coding: utf-8 -*-
"""内部配置図＋扉面図から『盤別機器リスト(device→面)』を作る。

茂泉様確定の読み方:
  - 盤は内部配置図/扉面で数えられ、左から 1,2,…,N と順に番号を振る。
  - 1系統(回路番号)の機器が複数盤にまたがることがあり、どの盤かは『位置』でしか分からない
    (回路番号では盤は決まらない)。共通リレー(30X等)は特定盤に集約されることがある。
  - ハーネスの From-To は『一面(=盤＋その扉)』ごとに作られる。

本モジュール: D図面(配置図)から各機器を x位置で左から N 盤へ割り当て、
  {(DEVICE正規化, DEVICE1正規化): 盤番号文字列} を返す。DCT(相番号図)は別途『改名』用途。
"""
import re
import collections

import ezdxf
from ezdxf import recover

# 盤に概ね1つ出る目印(盤数・盤中心の推定に使う)。扉面に出る計器類。
_PANEL_MARKERS = ('VM', 'VS')
# device→面 の対象外(金物・付属)。
_SKIP_DEVICES = {'吊りﾎﾞﾙﾄ', 'ﾊﾝﾄﾞﾙ', 'TB取付金具', 'CABLE', '低圧ｸﾘｰﾄ', '端子ｶﾊﾞｰ',
                 '扉蝶番', 'LugTB'}
# シーケンス機器名 → 内部配置図の機器名/同一視(名寄せ)。AM=電流計A、51/86X=遮断器52に付随。
_DEVICE_ALIAS = {'AM': 'A'}
_INTEGRATED_TO_52 = {'51', '86X'}   # 同一回路の52の盤を継承(電子式で遮断器に統合/付随)


def _norm(s):
    return re.sub(r'\s', '', str(s or '')).upper()


def _read(path):
    try:
        return ezdxf.readfile(path)
    except Exception:
        try:
            return recover.readfile(path)[0]
        except Exception:
            return None


def _layout_paths(dct_paths):
    """dct_paths から配置図(D図面・-D\\d、DCTを除く)だけを返す。"""
    out = []
    for p in (dct_paths or []):
        b = str(p).upper()
        if 'DCT' in b:
            continue
        if re.search(r'-D\d', b) or '配置' in str(p) or '扉面' in str(p):
            out.append(p)
    return out


def _kmeans1d(xs, n, it=80):
    """1次元 k-means。昇順の中心 n 個を返す。"""
    xs = sorted(xs)
    uniq = sorted(set(xs))
    if len(uniq) <= n:
        return uniq
    cs = [xs[int(i * (len(xs) - 1) / (n - 1))] for i in range(n)]
    for _ in range(it):
        g = [[] for _ in cs]
        for x in xs:
            g[min(range(len(cs)), key=lambda i: abs(cs[i] - x))].append(x)
        nc = [sum(q) / len(q) if q else cs[i] for i, q in enumerate(g)]
        if nc == cs:
            break
        cs = nc
    return sorted(cs)


def panel_count(dct_paths):
    """盤数 N を推定。配置図の目印(VM/VS)が盤に1つ出る前提で最大個数を採る。
    目印が無ければ DCT の盤ラベル(N-M)の個数。どちらも無ければ 0(=不明)。"""
    best = 0
    for p in _layout_paths(dct_paths):
        doc = _read(p)
        if not doc:
            continue
        for mk in _PANEL_MARKERS:
            c = sum(1 for ins in doc.modelspace().query('INSERT')
                    if ins.attribs and any(a.dxf.tag == 'DEVICE' and (a.dxf.text or '').strip() == mk
                                           for a in ins.attribs))
            best = max(best, c)
    if best >= 2:
        return best
    # DCT の盤ラベル
    for p in (dct_paths or []):
        if 'DCT' not in str(p).upper():
            continue
        doc = _read(p)
        if not doc:
            continue
        nums = set()
        for e in doc.modelspace().query('TEXT MTEXT'):
            t = (e.plain_text() if e.dxftype() == 'MTEXT' else e.dxf.text).strip()
            m = re.match(r'^(\d+)-(\d+)$', t)
            if m:
                nums.add(int(m.group(2)))
        best = max(best, len(nums))
    return best


def build_device_face(dct_paths, n=None):
    """配置図(内部＋扉面)から device→面 を作る。
    各D図面ごとに機器 x を N 盤へ k-means で分割し、左から 1..N を割当(通し)。
    別名(AM→A)と 51/86X→同回路52 の盤継承を補う。
    戻り: {(DEVICE正規化, DEVICE1正規化): 盤番号文字列}。N 不明や配置図無しなら空。"""
    layouts = _layout_paths(dct_paths)
    if not layouts:
        return {}
    if n is None:
        n = panel_count(dct_paths)
    if n < 2:
        return {}
    df = {}
    for p in layouts:
        doc = _read(p)
        if not doc:
            continue
        items = []
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            d = a.get('DEVICE', '')
            if not d or d in _SKIP_DEVICES:
                continue
            items.append((d, a.get('DEVICE1', ''), float(ins.dxf.insert[0])))
        if len(items) < n:
            continue
        cs = _kmeans1d([x for _, _, x in items], n)
        for d, d1, x in items:
            face = str(min(range(len(cs)), key=lambda i: abs(cs[i] - x)) + 1)
            df[(_norm(d), _norm(d1))] = face
    # 名寄せ: AM→A 等の別名で同キーを複製
    for (d, d1), fc in list(df.items()):
        for alias, real in _DEVICE_ALIAS.items():
            if d == _norm(real):
                df.setdefault((_norm(alias), d1), fc)
    # 51/86X(電子式・付随)は同回路の52の盤を継承
    by52 = {d1: fc for (d, d1), fc in df.items() if d == '52'}
    for dev in _INTEGRATED_TO_52:
        for d1, fc in by52.items():
            df.setdefault((dev, d1), fc)
    return df
