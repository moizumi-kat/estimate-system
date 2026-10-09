# -*- coding: utf-8 -*-
"""④FromTo(作業者の正解)に対する図面カバレッジ／差分分類(ルール精査の採点器)。

目的: 当方が図面(①設計DXF＋②DCT)から再現できる From-To が、人の正解④をどこまで
カバーするかを機器・端点・エッジ粒度で測り、埋まらない差分を『種類』に分類する。
これにより『どこを自動化/確認削減に効かせるか』を定量判断する(◎誤答ゼロの運用設計)。

差分の種類(エッジ単位, 両端のうち弱い方で分類):
  in_dxf          … 機器(ベース)が設計DXFに在る=再現可能の母体(これを上げる)
  locator         … A,B,C… の仮名称(内部配置図の凡例で実機器へ対応=②で解決)
  fixed           … BOX/LUG/SPD/分離器 等の固定特殊端点(語彙で既知化できる)
  breaker_mark    … MCCB/ELCB だが○◎符号・枠番号で記号が食い違う(正規化で解決)
  ct_phase        … CT の相サフィックス(101R/101T 等)
  multi           … マルチ計器など、別シート/別記号で設計DXFに現れにくい機器
  new             … 既知外(例 GOR)。要調査
  other           … 上記以外
"""
import glob
import os
import re
import collections

import ezdxf

from . import fromto_truth as ft
from . import locator_map as LM

FIXED = {'BOX', 'LUG', 'Lug', 'SPD', 'ET', '分離器', 'アース'}
MULTI = {'ﾏﾙﾁ', 'マルチ', '伝送', 'ﾀｲﾏ', 'タイマ', 'T/U', '中欠用', '表示用'}


def _seq_devices(paths):
    """設計DXF群 → (DEVICEベース集合, 機器記号集合)。"""
    bases, syms = set(), set()
    for p in paths:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev, no = d.get('DEVICE', ''), d.get('DEVICE1', '')
            if dev:
                bases.add(dev)
                syms.add(f'{dev}-{no}' if no else dev)
    return bases, syms


def classify_sym(sym, bases, syms, loc):
    """機器記号 → 差分種類。loc: {ロケーター文字: 実機器}。"""
    base = sym.split('-')[0]
    if sym in syms or base in bases:
        return 'in_dxf'
    act = loc.get(base) or loc.get(sym)
    if act:
        actbase = re.match(r'^(.*?)(\d*)$', act).group(1)
        if act in syms or actbase in bases:
            return 'in_dxf'          # ロケーター経由で実機器が設計DXFに在る
        return 'locator'
    if re.fullmatch(r'[A-Z]', base):
        return 'locator'
    if base in FIXED or any(sym.startswith(f) for f in FIXED):
        return 'fixed'
    if base in ('MCCB', 'ELCB') and re.search(r'[○◎]|LG|CE|CG|LE', sym):
        return 'breaker_mark'
    if base == 'CT' and re.search(r'[RST]$', sym):
        return 'ct_phase'
    if base in MULTI:
        return 'multi'
    if base == 'GOR':
        return 'new'
    return 'other'


_RANK = ['other', 'new', 'multi', 'ct_phase', 'breaker_mark', 'fixed', 'locator', 'in_dxf']


def analyze_seiban(seiban_dir, seq_glob=('図面データ_*.DXF', '図面データ_*.dxf'),
                   dct_glob=('機器相番号_*.dxf',)):
    """1製番 → カバレッジ＆差分分類の集計 dict。
    seq=設計DXF(①), dct=機器相番号(②, 変換済みDXF)。"""
    r = ft.load_seiban(seiban_dir)
    if not r['wires']:
        return None
    seq = []
    for g in seq_glob:
        seq += glob.glob(os.path.join(seiban_dir, g))
    dcts = []
    for g in dct_glob:
        dcts += glob.glob(os.path.join(seiban_dir, g))
    bases, syms = _seq_devices(seq)
    loc = {}
    for dd in dcts:
        for l, a in LM.extract_pairs(dd):
            loc[l.strip()] = a.strip()
    ft_syms = set()
    for w in r['wires']:
        for e in w['ends']:
            ft_syms.add(ft.endpoint_sym(e))
    dev_cat = collections.Counter(classify_sym(s, bases, syms, loc) for s in ft_syms)
    edge_cat = collections.Counter()
    for e in r['edges']:
        cs = [classify_sym(k[0], bases, syms, loc) for k in e]
        if all(c == 'in_dxf' for c in cs):
            edge_cat['both_in_dxf'] += 1
        else:
            edge_cat[min(cs, key=lambda c: _RANK.index(c) if c in _RANK else 0)] += 1
    return {'seiban': os.path.basename(seiban_dir.rstrip('/')),
            'wires': len(r['wires']), 'edges': len(r['edges']),
            'ft_syms': len(ft_syms), 'dev_cat': dict(dev_cat), 'edge_cat': dict(edge_cat),
            'locator_pairs': len(loc)}


def analyze_root(root):
    """複数製番フォルダ配下を一括集計。戻り: [per-seiban dict]（Noneは除外）。"""
    out = []
    for d in sorted(glob.glob(os.path.join(root, '*/'))):
        a = analyze_seiban(d)
        if a:
            out.append(a)
    return out


# ---------------------------------------------------------------------------
# 端子粒度カバレッジ: geometry(TERMINAL1-6) の抽出端子が ④端点の(機器,端子)を
# どれだけ直接読めているか = 2人目(端子番号付与)の真の自動化率。
# ---------------------------------------------------------------------------

def _geom_terminal_index(seq_paths):
    """シーケンスDXF群 → {機器sym(大文字): set(端子名)}。geometry の TB+TERMINAL1-6 抽出を使用。"""
    from .geometry import DrawingModel
    idx = {}
    for p in seq_paths:
        try:
            m = DrawingModel(p)
            terms = m._terminals()
        except Exception:
            continue
        for t in terms:
            nm = (t.name or '').strip()
            if nm and nm != '?':
                idx.setdefault(str(t.device).upper(), set()).add(nm)
    return idx


def _locator_to_actual(dct_paths, layout_paths):
    """{ロケーター文字(大文字): 実機器sym(大文字)} を凡例から作る(補完は含めない=確定のみ)。"""
    from . import locator_map as LM
    out = {}
    for p in dct_paths:
        for loc, act in LM.extract_pairs(p):
            _, letter = LM._split_locator(loc)
            if letter:
                out[letter.strip().upper()] = act.strip().upper()
    return out


def endpoint_coverage(seiban_dir):
    """1製番 → 端子粒度カバレッジ(2人目=端子番号付与の自動化余地の診断)。

    geometry は TERMINAL1-6(＋TB座標)を既に読む。本診断は④端点を次に分類する:
      no_terminal  … LUG/BOX/SPD/端子欄空 = 端子番号不要(自動)
      tb           … 端子台(TB) = 機器のTERMINAL属性でなく台番号付番(tb_strip)で別処理
      readable     … 機器がCADにあり端子番号もCAD(TERMINAL)に明記 = 自動で読める
      term_missing … 機器はCADにあるが端子番号が明記されない(標準極/接点など)
      device_missing … 設計シーケンスにTERMINAL付き機器として現れない端点

    注意: 設計シーケンスが端子番号を明示するのは一部機器(リレー/補助接点/OL/切替SW等)に
    限られ、主回路極(1-6)は標準・多くの接点は図に番号を持たない。よって device_missing は
    『真の欠落』ではなく『図に端子属性が無い(標準規則や③手書きで補完する領域)』を多く含む。
    readable を増やすには図面側の端子明記が要る。標準極・接点規則は別途ルールで補完する。
    """
    r = ft.load_seiban(seiban_dir)
    if not r['wires']:
        return None
    seq = glob.glob(os.path.join(seiban_dir, '図面データ_*F*.DXF')) + \
        glob.glob(os.path.join(seiban_dir, '図面データ_*F*.dxf')) + \
        glob.glob(os.path.join(seiban_dir, '図面データ_*E*.DXF')) + \
        glob.glob(os.path.join(seiban_dir, '図面データ_*E*.dxf')) + \
        glob.glob(os.path.join(seiban_dir, '図面データ_*G*.DXF')) + \
        glob.glob(os.path.join(seiban_dir, '図面データ_*G*.dxf')) + \
        glob.glob(os.path.join(seiban_dir, '図面データ_*H*.DXF')) + \
        glob.glob(os.path.join(seiban_dir, '図面データ_*H*.dxf'))
    dcts = glob.glob(os.path.join(seiban_dir, '機器相番号_*.dxf'))
    idx = _geom_terminal_index(seq)
    loc2act = _locator_to_actual(dcts, seq)
    cat = collections.Counter()
    for w in r['wires']:
        for e in w['ends']:
            sym = ft.endpoint_sym(e).upper()
            term = (e['term'] or '').strip()
            base = sym.split('-')[0]
            if len(base) == 1 and base in loc2act:      # ロケーター文字→実機器
                sym = loc2act[base]
                base = sym.split('-')[0]
            # 端子台(TB)・固定特殊端点は別勘定(機器のTERMINAL属性ではなく別機構で付番)
            if base.startswith('TB'):
                cat['tb'] += 1
                continue
            if base in FIXED or not term:
                cat['no_terminal'] += 1              # LUG/BOX/SPD や端子欄空=端子番号不要
                continue
            terms = idx.get(sym) or idx.get(base)
            if terms is None:
                cat['device_missing'] += 1
            elif term in terms:
                cat['readable'] += 1
            else:
                cat['term_missing'] += 1
    return {'seiban': os.path.basename(seiban_dir.rstrip('/')), 'cat': dict(cat)}
