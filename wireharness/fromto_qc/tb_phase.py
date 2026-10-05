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
     フレーム50/60でサイズ不明 → 太物か否かは稼働時間・顧客仕様に依存し図面だけでは
       一意に決まらない → 要確認(既定は内。人が確定、修正は学習)。
  ※回路番号が盤をまたいで重複する製番は、番号だけでは一意化できない(別途、盤別の区別が要る)。
"""
import re
import ezdxf

_BRK = re.compile(r'^(MCCB|ELCB|NFB|ELB)', re.I)
FUTO_SQ = 5.5          # これ超(8sq以上)は太物
FUTO_FRAME = 100       # これ以上のフレームは太物(確定)


def _num_sq(s):
    m = re.search(r'(\d+(?:\.\d+)?)\s*sq', str(s or ''), re.I)
    return float(m.group(1)) if m else None


def _frame(ty):
    m = re.search(r'(\d{2,3})', str(ty or ''))
    return int(m.group(1)) if m else None


def build_phase_map(draw_paths):
    """図面群 → {回路番号: {'phases','futo','confidence','basis','frame','size'}}。
    phases: 端子台に載る相の集合(太物なら{'E'})。confidence: '確定'/'要確認'。"""
    import collections
    term = collections.defaultdict(collections.Counter)
    frames = collections.defaultdict(set)
    sizes = collections.defaultdict(set)
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
        else:
            # フレーム50/60でサイズ不明 → 内か太物(稼働時間・顧客仕様)で一意に決まらない。
            # 既定は内(多数)だが断定しない=要確認(人が確定、修正を学習)。
            futo, conf, basis = False, '要確認', 'サイズ不明/フレーム50-60'
        eff = (phases & {'E'}) if futo else set(phases)
        out[circ] = {'phases': eff, 'all_phases': phases, 'futo': futo,
                     'confidence': conf, 'basis': basis, 'frame': frame, 'size': size}
    return out
