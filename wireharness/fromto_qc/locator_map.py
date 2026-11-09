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
      'confidence': {(ベース大文字, 番号): '確定'},           凡例由来は確定
    }
    """
    by_base = {}
    by_actual = {}
    pairs_all = []
    confidence = {}
    for p in (dct_paths or []):
        for loc, act in extract_pairs(p):
            base, num = _split_actual(act)
            ki, letter = _split_locator(loc)
            B = base.upper()
            if B and letter:
                by_base.setdefault(B, letter)
                by_actual[(B, num)] = loc
                confidence[(B, num)] = '確定'
                pairs_all.append((loc, act))
    return {'by_base': by_base, 'by_actual': by_actual, 'pairs': pairs_all,
            'confidence': confidence}


# ---------------------------------------------------------------------------
# 凡例が不完全な製番の補完(欠落ロケーターの推定)。◎誤答ゼロ: 確信が持てない割当は要確認。
# ---------------------------------------------------------------------------

# 補助リレー(ロケーター化される小型機器)の判定。PARTS=AXR / 型式 RU?S が典型。
_RELAY_TYPE = re.compile(r'RU\d*S', re.I)


def relays_on_panel(layout_paths):
    """配置図群 → 盤面に実装された補助リレー等(ロケーター対象)の一覧。
    凡例行(DEVICE2=':')は除外し、実装シンボルのみ。
    戻り: [{'sym','base','num','x','y','type','parts','basis'?}]（同一symは代表1件）。"""
    seen = {}
    for p in (layout_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            if d.get('DEVICE2') == ':':           # 凡例行は除外
                continue
            dev, no = d.get('DEVICE', ''), d.get('DEVICE1', '')
            parts, typ = d.get('PARTS', ''), d.get('TYPE', '')
            if not dev:
                continue
            is_relay = (parts == 'AXR') or bool(_RELAY_TYPE.search(typ))
            if not is_relay:
                continue
            sym = f'{dev}{no}' if no else dev
            try:
                x, y = round(ins.dxf.insert.x, 1), round(ins.dxf.insert.y, 1)
            except Exception:
                x, y = 0.0, 0.0
            if sym not in seen:
                seen[sym] = {'sym': sym, 'base': dev.upper(), 'num': no,
                             'x': x, 'y': y, 'type': typ, 'parts': parts}
    return list(seen.values())


def _next_letters(used_letters, n):
    """既使用の文字集合の後に続くアルファベットを n 個返す(A..Z)。"""
    out = []
    code = ord('A')
    while len(out) < n and code <= ord('Z'):
        c = chr(code)
        if c not in used_letters:
            out.append(c)
        code += 1
    return out


def complete_locators(lmap, layout_paths):
    """凡例(lmap)に無い盤面リレーへロケーター文字を推定補完する。

    方針(承認済): 凡例優先。欠落分は『盤面に在るが凡例に無いリレー』を対象に、
    残りの文字を割り当てる。ただし並び順(配置/型式)が製番間で一定でないため、
    個々の文字↔機器の割当は確信できない→ confidence='要確認' として返す
    (集合としての特定=『この機器群が欠落ロケーター』は確度が高い)。
    戻り: {'inferred': [{'actual_base','actual_num','letter','confidence','basis'}], 'note'}。
    """
    panel = relays_on_panel(layout_paths)
    assigned = set(lmap.get('by_actual', {}).keys()) if lmap else set()
    used_letters = set()
    for loc in (lmap.get('pairs', []) if lmap else []):
        _, letter = _split_locator(loc[0])
        if letter:
            used_letters.add(letter)
    missing = [r for r in panel if (r['base'], r['num']) not in assigned]
    # 並びの手掛かりが弱いので、型式→配置(x昇順)で安定ソートし、残り文字を順に割当(要確認)。
    missing.sort(key=lambda r: (r['type'], r['x'], r['y']))
    letters = _next_letters(used_letters, len(missing))
    inferred = []
    for r, lt in zip(missing, letters):
        inferred.append({'actual_base': r['base'], 'actual_num': r['num'],
                         'sym': r['sym'], 'letter': lt, 'confidence': '要確認',
                         'basis': f"凡例漏れ・型式{r['type']}の未割当リレー"})
    note = ('凡例は完全' if not missing else
            f'{len(missing)}機器が凡例漏れ→要確認で補完(ピン照合で確定可)')
    return {'inferred': inferred, 'missing_count': len(missing), 'note': note}


def resolve_by_terminals(letter_pins, candidates):
    """欠落ロケーターを『使用ピン番号の照合』で実機器に割り当てる(学習・検証用)。

    letter_pins: {ロケーター文字: set(使用ピン)}  ④等から得た、その文字が使う端子番号。
    candidates:  {実機器sym: set(その機器の端子番号)}  シーケンス図の TERMINAL1/2 等から。
    欠落ロケーターと欠落リレーは1対1対応(全単射)である前提で割り当てる:
      1) 使用ピンを包含する候補が(未割当の中で)一意な文字から確定していく。
      2) 文字と候補が1つずつ残れば消去法で割当(要確認: ピンで確証できていないため)。
      3) それでも曖昧なら最有力候補を要確認で返す。
    返す: {文字: (実機器sym|None, '確定'|'要確認')}。衝突や証拠不足は必ず要確認。
    """
    letters = list(letter_pins or {})
    cands = dict(candidates or {})
    compat = {lt: [s for s, t in cands.items()
                   if set(letter_pins[lt]) and set(letter_pins[lt]) <= set(t)]
              for lt in letters}
    assign = {}
    taken = set()
    # 1) 未割当の中で互換候補が一意な文字を確定(反復して波及させる)
    changed = True
    while changed:
        changed = False
        for lt in letters:
            if lt in assign:
                continue
            avail = [s for s in compat[lt] if s not in taken]
            if len(avail) == 1:
                assign[lt] = (avail[0], '確定')
                taken.add(avail[0])
                changed = True
    # 2) 消去法: 文字・候補が1つずつ残ったら対応付け(要確認)
    rem_lt = [lt for lt in letters if lt not in assign]
    rem_cd = [s for s in cands if s not in taken]
    if len(rem_lt) == 1 and len(rem_cd) == 1:
        assign[rem_lt[0]] = (rem_cd[0], '要確認')
        taken.add(rem_cd[0])
        rem_lt = []
    # 3) 残りは最有力を要確認で
    for lt in rem_lt:
        avail = [s for s in compat[lt] if s not in taken] or [s for s in cands if s not in taken]
        if avail:
            best = max(avail, key=lambda s: len(set(cands[s]) & set(letter_pins[lt])))
            assign[lt] = (best, '要確認')
            taken.add(best)
        else:
            assign[lt] = (None, '要確認')
    return assign


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
