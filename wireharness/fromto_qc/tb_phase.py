# -*- coding: utf-8 -*-
"""端子台(TB)の相端子を『図面のTERMINAL属性＋太物判定』で決める(誤答ゼロ)。

茂泉様との突き合わせで確立した規則:
  1. 相の組み合わせは図面の TERMINAL 属性にそのまま入っている(例 'U,V,W,E' / 'U,V,E')。
     → 推定せず属性から取得(これが正解)。
  2. 電流の大きい線(太物)はハーネスと別扱い。太物回路は端子台に接地Eだけが載る。
     太物の判定(確定できるもの):
       ・内部電線サイズ(DENSEN)が読めて 5.5sq 超  → 太物(確定)
       ・ブレーカのフレームが 100 以上           → 太物(確定, 検証10/10)
       ・フレーム50/60 かつ サイズ5.5sq以下       → 内(確定)
       ・社内基準(FS電線選定)で kW→電線サイズ。負荷率①(×1.0=24h操業)と③(×0.35)の
         両極で太物判定が一致する回路は、稼働時間に依らず確定(wire_select)。
     フレーム50/60でサイズ不明 かつ 負荷率で太物判定が割れる → 太物か否かは稼働時間・
       顧客仕様に依存し図面だけでは一意に決まらない → 要確認(既定は内。人が確定、学習)。
  ※回路番号が盤をまたいで重複する製番は、番号だけでは一意化できない(別途、盤別の区別が要る)。
  ※DENSEN は主幹/隣接回路の値が混入しうるため、読めた確定を上位に置きつつも、kW基準は
    既存の確定(DENSEN/フレーム)を上書きしない(回帰ゼロを検証済: 128回路, 回帰0)。
"""
import re
import ezdxf
from . import wire_select

_BRK = re.compile(r'^(MCCB|ELCB|NFB|ELB)', re.I)
_V400 = re.compile(r'(400|440)\s*V')
FUTO_SQ = 5.5          # これ超(8sq以上)は太物
FUTO_FRAME = 100       # これ以上のフレームは太物(確定)


def _num_sq(s):
    m = re.search(r'(\d+(?:\.\d+)?)\s*sq', str(s or ''), re.I)
    return float(m.group(1)) if m else None


def _frame(ty):
    m = re.search(r'(\d{2,3})', str(ty or ''))
    return int(m.group(1)) if m else None


def _futo_by_kw(kw, voltage):
    """FS電線選定基準で kW→電線サイズ。負荷率①(×1.0)と③(×0.35)の両極で太物判定が
    一致すれば (太物bool, ③サイズ, ①サイズ) を返す。割れる(稼働時間依存)なら None。"""
    hi = wire_select.by_kw(kw, voltage, '1.0')    # 24h操業(上限)
    lo = wire_select.by_kw(kw, voltage, '0.35')   # 低稼働(下限)
    if hi is None or lo is None:
        return None
    fh, fl = (hi > FUTO_SQ), (lo > FUTO_SQ)
    if fh != fl:
        return None
    return fh, lo, hi


def build_phase_map(draw_paths):
    """図面群 → {回路番号: {'phases','futo','confidence','basis','frame','size'}}。
    phases: 端子台に載る相の集合(太物なら{'E'})。confidence: '確定'/'要確認'。"""
    import collections
    term = collections.defaultdict(collections.Counter)
    frames = collections.defaultdict(set)
    sizes = collections.defaultdict(set)
    kws = {}
    v400 = set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            circ = d.get('DEVICE1', '')
            if not re.match(r'^\d+$', str(circ)):
                continue
            t = d.get('TERMINAL', '')
            if t and re.search(r'[UVW]', t):
                term[circ][t] += 1
            if _BRK.match(d.get('DEVICE', '')):
                f = _frame(d.get('TYPE', ''))
                if f:
                    frames[circ].add(f)
            q = _num_sq(d.get('DENSEN', ''))
            if q:
                sizes[circ].add(q)
            m = re.search(r'([\d.]+)\s*kW', d.get('KW', ''))
            if m:
                try:
                    kws[circ] = float(m.group(1))
                except ValueError:
                    pass
            if any(_V400.search(v) for v in d.values() if v):
                v400.add(circ)
    out = {}
    for circ, tc in term.items():
        phases = set(re.findall(r'[UVWE]', tc.most_common(1)[0][0]))
        frame = min(frames[circ]) if frames.get(circ) else None
        size = max(sizes[circ]) if sizes.get(circ) else None
        if size is not None:
            # 内部電線サイズが読めれば最優先・確定(5.5sq超=太物)
            futo, conf, basis = (size > FUTO_SQ), '確定', f'電線{size}sq'
        elif frame is not None and frame >= FUTO_FRAME:
            # フレーム100以上は必ず太物(検証10/10)
            futo, conf, basis = True, '確定', f'フレーム{frame}'
        elif circ in kws and _futo_by_kw(kws[circ], 400 if circ in v400 else 200) is not None:
            # 社内基準(FS)でkW→電線。負荷率①と③で太物判定が一致する=稼働時間に依らず確定。
            fk, lo, hi = _futo_by_kw(kws[circ], 400 if circ in v400 else 200)
            futo, conf, basis = fk, '確定', f'kW基準一致({kws[circ]}kW:{lo}-{hi}sq)'
        else:
            # フレーム50/60でサイズ不明、かつkW基準でも負荷率で割れる → 稼働時間・顧客仕様
            # に依存し一意に決まらない。既定は内(多数)だが断定しない=要確認(人が確定、学習)。
            futo, conf, basis = False, '要確認', 'サイズ不明/負荷率依存'
        eff = (phases & {'E'}) if futo else set(phases)
        out[circ] = {'phases': eff, 'all_phases': phases, 'futo': futo,
                     'confidence': conf, 'basis': basis, 'frame': frame, 'size': size}
    return out
