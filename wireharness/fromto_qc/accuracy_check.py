# -*- coding: utf-8 -*-
"""From-To(ハーネスデータ)正解率の測定ツール。

手本ハーネス(配線情報リスト)と、アプリの From-To 出力を『実機器＋端子機能』で突合する。
社内資産の手本・図面はリポジトリに含めないため、製番ごとのディレクトリをパスで渡す:

  <root>/<製番>/in/      … F*/E*/H*/*-DCT* の図面(DXF)
  <root>/<製番>/honbon/  … 手本 配線情報リスト(cp932, 11列TAB) *.txt

使い方:
  python -I -m fromto_qc.accuracy_check <root>

測定方針(茂泉様基準『両端が同じ実機器・同機能の端子なら正解』に忠実):
  ・非ロケータ機器(MCCB/TB/AM/CT/CP/接触器 等)= 実機器名＋端子機能で突合。
  ・相番号(リレー)= 手本ファイル(=1盤)とDCTの盤を回路/文字集合で対応付け、盤内で突合。
  ・端子の表記ゆれは機能同一として正規化: 位置注記(上/下/左/右)除去・全半角統一・
    表示灯リード(＋/－/空)を同機能・補助接点のNO/NCを同機能。
これは回帰追跡用。手本・図面が無い環境では製番0件として終了する。
"""
import os
import re
import glob
import sys
import collections
import unicodedata

from . import harness_produce as HP
from . import locator_map as LMAP
from . import contact_pins as CP

COLORS = {'*', '赤', '白', '青', '緑', '黒', '黄', 'E', ''}
LAMP = ('WL', 'OL', 'GL', 'RL', 'PL', 'SL', 'YL', 'BL')


def _ndev(d):
    return re.sub(r'[\s\-]', '', (d or '').strip()).upper()


def _base(dev):
    return re.sub(r'[\s\-].*$', '', str(dev or '')).rstrip('0123456789').upper()


def _is_loc(dev, no):
    d = (dev or '').strip()
    return len(d) == 1 and d.isalpha() and not no


def _normterm(t):
    t = unicodedata.normalize('NFKC', (t or '')).strip().upper()
    t = re.sub(r'[ _]*[上下左右]+$', '', t)   # 位置注記を除去
    return t.replace(' ', '')


def _fclass(pin, dev):
    """端子 → 機能クラス(表記ゆれ・NO/NC・表示灯リードを同機能化)。"""
    b = _base(dev)
    p = _normterm(pin)
    if b in LAMP:
        if p in ('+', '', 'X1', '1'):
            return 'LAMP_SIG'
        if p in ('-', 'X2', '2', 'E'):
            return 'LAMP_COM'
    f = CP.function(p, dev)
    if f in ('NO', 'NC'):
        return f
    return 'p:' + p


def _sheets(indir):
    seq = sorted(glob.glob(indir + '/*-F*.DXF') + glob.glob(indir + '/*-F*.dxf'))
    skel = sorted(glob.glob(indir + '/*-E*.DXF') + glob.glob(indir + '/*-E*.dxf'))
    h = sorted(glob.glob(indir + '/*-H*.DXF') + glob.glob(indir + '/*-H*.dxf'))
    dct = sorted(glob.glob(indir + '/*-DCT.DXF') + glob.glob(indir + '/*-DCT.dxf'))
    return (seq or h), skel, dct


# ---- 非ロケータ(実機器)側 ----
def _honbon_real(d):
    ends = []
    for f in sorted(glob.glob(d + '/honbon/*.txt')):
        for line in open(f, 'rb').read().decode('cp932', 'replace').splitlines():
            c = line.split('\t')
            if len(c) < 8 or (c[1] or '').strip() in COLORS:
                continue
            dev = (c[5] or '').strip(); no = (c[6] or '').strip(); term = (c[7] or '').strip()
            if not dev or dev == '*':
                continue
            if dev.upper() == 'TB':
                if term:
                    ends.append(('TB', term, dev))
                continue
            if _is_loc(dev, no):
                continue
            if not term and _base(dev) not in LAMP:
                continue
            ends.append(('TB' if dev.upper().startswith('TB') else _ndev(dev + no), term, dev))
    return ends


def _app_real(name, indir):
    seq, skel, dct = _sheets(indir)
    routed = HP.route_and_length(seq, skel, seiban=name, dct_paths=dct or None)
    g = collections.Counter()
    for w in routed['wires']:
        for e in (w['from'], w['to']):
            dev = str(e.get('device', '')); no = str(e.get('no', '')); term = e.get('terminal', '') or ''
            if not dev or _is_loc(dev, no):
                continue
            if not term and _base(dev) not in LAMP:
                continue
            key = 'TB' if dev.upper().startswith('TB') else _ndev(dev + no)
            g[(key, _fclass(term, dev))] += 1
    # 盤間渡りコネクタ(R-F/L-F=制御・渡り用TB=太物)は route_and_length が out['crossings'] に
    # 生成する(out['wires'] には入らない)。手本にも R-F(P)/L-F(R) 端点として載るので、
    # 測定対象に含める(含めないと渡りコネクタが一律『未』になり機能一致を過小評価する)。
    for r in (routed.get('crossings') or {}).get('rows', []):
        dev = str(r.get('機器', '')); term = str(r.get('端子', '') or '')
        if not dev:
            continue
        key = 'TB' if dev.upper().startswith('TB') else _ndev(dev)
        g[(key, _fclass(term, dev))] += 1
    return g


# ---- 相番号(リレー)側: 手本ファイル=1盤 を DCT 盤に対応付け ----
def _honbon_relay_files(d):
    out = []
    for f in sorted(glob.glob(d + '/honbon/*.txt')):
        L = collections.defaultdict(list); circ = collections.Counter()
        for line in open(f, 'rb').read().decode('cp932', 'replace').splitlines():
            c = line.split('\t')
            if len(c) < 8 or (c[1] or '').strip() in COLORS:
                continue
            dev = (c[5] or '').strip(); no = (c[6] or '').strip(); term = (c[7] or '').strip()
            if len(dev) == 1 and dev.isalpha() and not no and term:
                L[dev.upper()].append(term)
            m = re.match(r'(\d)\d\d', no)
            if m:
                circ[m.group(1)] += 1
        if L:
            out.append((os.path.basename(f), L, set(circ)))
    return out


def _app_relay_panels(name, indir):
    seq, skel, dct = _sheets(indir)
    if not dct:
        return {}, {}
    routed = HP.route_and_length(seq, skel, seiban=name, dct_paths=dct)
    lm = LMAP.build_map(dct)
    LMAP.apply_to_routed(routed, lm)
    pan = collections.defaultdict(lambda: collections.defaultdict(list))
    pancirc = collections.defaultdict(set)
    for (base, num), loc in lm.get('by_actual', {}).items():
        N = str(loc).split('-')[0]
        m = re.match(r'(\d)\d\d', str(num))
        if m:
            pancirc[N].add(m.group(1))
    for w in routed['wires']:
        for e in (w['from'], w['to']):
            lf = e.get('locator_full'); term = e.get('terminal')
            if lf and '-' in str(lf) and term:
                N, letter = str(lf).split('-', 1)
                pan[N][letter.upper()].append(term)
    return pan, pancirc


def _relay_score(name, d):
    hon = _honbon_relay_files(d)
    pan, pancirc = _app_relay_panels(name, d + '/in')
    if not hon or not pan:
        return 0, 0
    used = set(); th = tt = 0
    for fn, L, circ in hon:
        best, bN = -1, None
        for N in pan:
            if N in used:
                continue
            s = len(set(L) & set(pan[N])) + 3 * len(circ & pancirc.get(N, set()))
            if s > best:
                best, bN = s, N
        if bN is None:
            continue
        used.add(bN); ap = pan.get(bN, {})
        for letter, terms in L.items():
            b = collections.Counter(_fclass(t, letter) for t in ap.get(letter, []))
            for t in terms:
                tt += 1; k = _fclass(t, letter)
                if b.get(k, 0) > 0:
                    b[k] -= 1; th += 1
    return th, tt


def measure_seiban(name, d):
    """1製番 → {'real':(hit,tot), 'relay':(hit,tot)}。"""
    hon = _honbon_real(d); app = _app_real(name, d + '/in')
    bucket = collections.Counter(app); hit = 0
    for (key, term, dev) in hon:
        k = (key, _fclass(term, dev))
        if bucket.get(k, 0) > 0:
            bucket[k] -= 1; hit += 1
    rel = _relay_score(name, d)
    return {'real': (hit, len(hon)), 'relay': rel}


def main(root):
    seibans = [n for n in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, n, 'honbon'))]
    if not seibans:
        print('測定対象の製番がありません(手本/図面は社内資産のため未同梱)。')
        return
    tot = collections.Counter()
    print(f"{'製番':16s} {'実機器':>12s} {'相番号':>12s}")
    for s in seibans:
        r = measure_seiban(s, os.path.join(root, s))
        rh, rt = r['real']; lh, lt = r['relay']
        tot['rh'] += rh; tot['rt'] += rt; tot['lh'] += lh; tot['lt'] += lt
        print(f"  {s:14s} {rh:4d}/{rt:<5d}{100*rh/rt if rt else 0:3.0f}% "
              f"{lh:4d}/{lt:<5d}{100*lh/lt if lt else 0:3.0f}%")
    a = tot['rh'] + tot['lh']; b = tot['rt'] + tot['lt']
    print(f"  {'合計':14s} 実機器 {100*tot['rh']/tot['rt'] if tot['rt'] else 0:.0f}% / "
          f"相番号 {100*tot['lh']/tot['lt'] if tot['lt'] else 0:.0f}% / "
          f"総合 {100*a/b if b else 0:.0f}%")


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
