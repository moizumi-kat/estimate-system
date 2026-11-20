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
  3. 渡りを型分類(手本準拠):相端子(R/S/T/U/V/W/N)または MCCB の一次極(1/3/5)に
     繋がれば太物→渡り用TB、それ以外(信号端子・MCCB 二次 2/4/6 の計器タップ等)は
     制御→R-F/L-F。詳細は classify() の docstring。
  4. 送り元(左の面)→受け先(右の面)で分割し、連結行を生成(採番は決定的 canonical)。

device→面 が確定できない機器(扉枠・盤ラベルの無い製番等)は面=None となり、その
号線は渡り判定に使わない(安全側=誤った渡りを出さない)。盤情報の無い図面では
検出ゼロ=既存出力に影響しない(回帰ゼロ)。
"""
import re
import collections

import ezdxf

from . import locator_map as LMAP
from . import panel_layout as _PL

COLORS = {'*', '赤', '白', '青', '緑', '黒', '黄', 'E', '', None, 'SLD'}
# 主幹遮断器(太物の電源がここを通る)。
_MCCB_DEV = {'MCCB', 'MCB', 'ELB', 'ELCB', 'NFB', 'MCCB主幹'}
# MCCB の一次側(電源入り)極=奇数極。二次側(2/4/6)は計器/CT のタップ(制御)なので除く。
_PRIMARY_POLES = {'1', '3', '5'}
# 相端子(手本の太物=渡り用TB は R/S/T/U/V/W/N の相端子で現れる)。
_PHASE_TERM = re.compile(r'^\d*[RSTUVWN]$')
# スケルトン図(E)に『電源機器』として実在する PARTS(由来図面での裏付けに使う)。
# これらは太物(電源系統)の本体。シーケンス図(F)では PARTS 空の参照として現れる。
_POWER_PARTS = {'MCCB', 'MCB', 'MCDT', 'ELB', 'ELCB', 'NFB'}
# 線番属性のタグ(シーケンス図に描かれた号線=制御の裏付け)。
_SENBAN_TAGS = {'線番', 'SENBAN', 'GOUSEN'}


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


def _gousen_base(g):
    """号線の末尾枝番(英字)を外した基底。237A/237B→237, 103D→103。
    基底が数字でない(KA等)は分割せず元のまま(過剰グループ化を防ぐ)。"""
    b = re.sub(r'[A-Za-z]+$', '', g)
    return b if (b and b != g and b.isdigit()) else g


def detect_crossings(wires, dev_face, bases=None):
    """各号線が繋ぐ機器の面集合を作り、2面以上に跨る号線(=盤間渡り)を返す。
    戻り: {号線: set(面)}（跨ぐものだけ）。

    **端子台(TB)の扱い(茂泉様確定):** シーケンス配線の端点 device='TB' は『汎用端子台』で、
    その端子番号(no)が物理ブロック名(例 TB22)と偶然一致して誤マッチし、別盤への偽渡りを
    生む(回路のTB端子台は回路自身の盤にある: 回路103→盤1のTB13)。そのため TB端点は物理
    ブロックに当てず、原則その号線の回路基底が属する盤へ寄せる(bases 指定時)。

    **集約端子台バス(茂泉様確定/5-12043 の 237 母線で裏取り):** 共通の中継端子台に多数の
    号線(例 237A〜237J)が集まる母線は、その中継端子台がマスター盤に在り、各盤の機器から
    盤間を渡ってそこへ集まる。基底グループ(237)の全メンバ(≥3)がTB端点を持ち、非TB側の
    機器が2盤以上に跨るとき『集約バス』と判定し、TB端点を最上位(マスター)盤へ寄せる
    → 各メンバが機器盤↔マスター盤で正しく渡りになる(237B〜Hの取りこぼし解消)。"""
    rel_concat = {(b, n): p for (b, n), p in dev_face.items()}
    circuit_panel = {k: p for (d, k), p in dev_face.items() if d == '52'}
    sbases = sorted((bases or []), key=len, reverse=True)

    def _circ_panel(g):
        for b in sbases:
            if g.startswith(b):
                return circuit_panel.get(b)
        return None

    # Pass1: 非TB端点の面・TB有無を号線ごと／基底グループのメンバを集計
    non_tb = collections.defaultdict(set)
    has_tb = collections.defaultdict(bool)
    members = collections.defaultdict(set)
    for w in wires:
        g = w.get('gousen')
        if not g or g in COLORS:
            continue
        members[_gousen_base(g)].add(g)
        for e in (w.get('from', {}), w.get('to', {})):
            if _norm(e.get('device')) == 'TB':
                has_tb[g] = True
            else:
                p = face_of(dev_face, rel_concat, e.get('device'), e.get('no'))
                if p:
                    non_tb[g].add(p)
    # 集約バス判定: 全メンバ(≥3)がTB端点＋非TB面が2盤以上 → 中継TBはマスター(最上位)盤
    bus_panel = {}
    for base, ms in members.items():
        if len(ms) < 3 or not all(has_tb.get(g) for g in ms):
            continue
        gp = set()
        for g in ms:
            gp |= non_tb.get(g, set())
        if len(gp) >= 2:
            bus_panel[base] = max(gp, key=_face_key)

    def _tb_panel(g):
        b = _gousen_base(g)
        if b in bus_panel:
            return bus_panel[b]          # 集約バスの中継端子台=マスター盤
        return _circ_panel(g)            # 通常=回路の盤(無ければ盤不明=面内完結)

    # Pass2: 面集合を確定(TB端点は _tb_panel を足す)
    g2p = collections.defaultdict(set)
    for g in set(list(non_tb) + list(has_tb)):
        ps = set(non_tb.get(g, set()))
        if has_tb.get(g):
            tp = _tb_panel(g)
            if tp:
                ps.add(tp)
        g2p[g] = ps
    return {g: ps for g, ps in g2p.items() if len(ps) >= 2}


def classify(gousen, wires):
    """渡り号線の型を返す: 'power'(太物→渡り用TB) か 'control'(制御→R-F/L-F)。

    **手本から学習した規則(7製番の手本で裏取り):**
      手本では太物の盤間渡りは必ず『相端子』で現れる — 渡り用TB の端子が
      R/S/T/U/V/W/N 相(例 101V/1R/2G2N)、= MCCB の一次側(奇数極 1/3/5)。
      一方 制御の盤間渡りは R-F/L-F の信号線で、繋ぐ端子は信号端子(13,97,A1,
      ALa/ALc 等)か、MCCB の二次側 2/4/6(計器/CT のタップ)止まり。
    ∴ 渡りが『MCCB系の一次極(1/3/5)』または『相端子(R/S/T/U/V/W/N)』に繋がれば
      太物、それ以外は制御。MCCB の 2/4/6 のみ(RC2/SC2 等の計器タップ)は制御。

    実測: 5-21071=16/16(太物4=1G系の MCCB 1/3/5・制御12)、117 の計器 RC2/SC2
    (MCCB 2/4/6)を制御と正しく判定(旧 1,3,5/2,4,6 一括ルールは誤って太物)。"""
    for w in wires:
        if w.get('gousen') != gousen:
            continue
        for e in (w.get('from', {}), w.get('to', {})):
            t = _norm(e.get('terminal'))
            if not t:
                continue
            if _PHASE_TERM.match(t):                       # 相端子 = 太物
                return 'power'
            if _norm(e.get('device')) in _MCCB_DEV and \
                    re.sub(r'\D', '', t) in _PRIMARY_POLES:  # MCCB一次極1/3/5 = 太物
                return 'power'
    return 'control'


def build_source_evidence(skel_paths=None, seq_paths=None):
    """由来図面から独立した裏付け情報を作る(端子信号とは別系統の第2の証拠)。

      - power_devs: スケルトン図(E)に PARTS∈電源機器 として実在する機器 (DEVICE,DEVICE1)。
                    = 太物(電源系統)の本体。シーケンス図では PARTS 空の参照。
      - seq_gousen: シーケンス図(F)に『線番』として描かれた号線。= 制御結線。

    実測(5-21071/117): 太物 1G/2G 系は seq_gousen に出ず(電源機器に接続)、制御 104/KA/
    RC2/SC2 は seq_gousen に出る。117 の 201 は seq_gousen に無い→太物側を裏付け。
    戻り: {'power_devs': set((DEVICE,DEVICE1)), 'seq_gousen': set(号線)}。図面無しなら空。"""
    power_devs = set()
    for p in (skel_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            if a.get('PARTS', '').upper() in _POWER_PARTS and a.get('DEVICE'):
                power_devs.add((_norm(a.get('DEVICE')), _norm(a.get('DEVICE1'))))
    seq_gousen = set()
    for p in (seq_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            for at in ins.attribs:
                if at.dxf.tag in _SENBAN_TAGS:
                    t = (at.dxf.text or '').strip()
                    if t and t not in COLORS:
                        seq_gousen.add(t)
    return {'power_devs': power_devs, 'seq_gousen': seq_gousen}


def build_circuit_bases(skel_paths=None):
    """スケルトン(E)のMCCB系機器の DEVICE1 = 回路番号の集合を返す(線番畳みの権威)。
    茂泉様確定: MCCBの2行目(DEVICE1)が回路番号で、その回路の電線は『回路番号＋枝番』。
    単桁の主幹(DEVICE1='1'/'2'等)は回路でなく主幹番号なので除外(len>=2)。
    例 117: {101,102,103,201,202,203,204,301,302,303,401,402}
       5-21071: {1G,2G1,2G2,1A,1G1,1G2,2G3,1AC,1GC}"""
    bases = set()
    for p in (skel_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            if a.get('PARTS', '').upper() in _POWER_PARTS:
                c = _norm(a.get('DEVICE1'))
                if len(c) >= 2:
                    bases.add(c)
    return bases


def build_cable_sizes(skel_paths=None):
    """スケルトン(E)の電源機器から 回路番号(DEVICE1)→電線サイズ(CABLE_SIZE) を作る。
    現場ルール(茂泉様): 盤間渡りは 2.0sq以下＝コネクタ(R-F/L-F)、2.0sq超＝直接1本渡り(太物)。
    太物フィーダ(CVTケーブル等)のサイズ確認・注記に使う。例 2G2→60sq, 1G1→200sq。"""
    sizes = {}
    for p in (skel_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            cs = a.get('CABLE_SIZE', '') or a.get('GAISENSIZE', '')
            c = _norm(a.get('DEVICE1'))
            if cs and c and len(c) >= 2:
                sizes[c] = cs
    return sizes


def _sq_value(size_str):
    """'60sq'/'HIV3.5sq'/'200sq' から sq 数値を取り出す。取れなければ None。"""
    m = re.search(r'(\d+(?:\.\d+)?)\s*sq', str(size_str or ''), re.I)
    return float(m.group(1)) if m else None


def normalize_gousen(g, circuit_bases):
    """線番を手本準拠で基底(回路番号/制御信号名)へ畳む。

    **電源(スケルトン回路番号＋数字枝番):** '20110'→'201'(回路201＋枝番10)。
      回路番号はスケルトン由来の集合(circuit_bases)のみ。'104'(回路に非該当)→そのまま
      ('104→1' の誤変換を防ぐ)。線番そのものが回路番号(1G1/1G2=別MCCB)→畳まない。
    **制御(英字基底＋末尾ちょうど2桁数字=枝番):** '2BZ04'→'2BZ','1BZ07'→'1BZ'。
      末尾1桁(RC2/LT2/AL2=信号index)や末尾が英字/端子(201R/201C1)→別号線のまま。
      制御は英字を含むため回路番号集合に依らず判定できる(circuit_bases 不要)。
    畳む対象が無ければ原文のまま(回帰ゼロ)。"""
    gn = _norm(g)
    if circuit_bases and gn in circuit_bases:
        return g              # 線番そのものが回路番号(例 1G1/1G2=別MCCB)なら畳まない
    # 制御: 英字で終わる基底＋末尾ちょうど2桁の数字枝番 → 基底(2桁=枝番/1桁=index)
    m = re.fullmatch(r'(.*[A-Za-z])(\d{2})', gn)
    if m:
        return m.group(1)
    # 電源: 回路番号(スケルトン由来・len>=2)＋数字枝番(1〜2桁) → 回路番号(最長一致)
    if circuit_bases:
        for c in sorted((b for b in circuit_bases if len(b) >= 2), key=len, reverse=True):
            if gn.startswith(c):
                rem = gn[len(c):]
                if rem and rem.isdigit() and len(rem) <= 2:
                    return c
    return g


def source_type(gousen, wires, evidence):
    """由来図面だけから渡りの型を推す(端子信号とは独立): 'power'/'control'/None。
      号線がシーケンス図の線番 → 制御。電源機器(スケルトン)に繋がる → 太物。両方/無しは None。"""
    if not evidence:
        return None
    seq_g = evidence.get('seq_gousen') or set()
    power_devs = evidence.get('power_devs') or set()
    in_seq = gousen in seq_g
    hits_power = False
    for w in wires:
        if w.get('gousen') != gousen:
            continue
        for e in (w.get('from', {}), w.get('to', {})):
            if (_norm(e.get('device')), _norm(e.get('no'))) in power_devs:
                hits_power = True
    if in_seq and not hits_power:
        return 'control'
    if hits_power and not in_seq:
        return 'power'
    return None            # 情報不足/両立 → 由来からは断定しない


def generate(routed, dct_paths, skel_paths=None, seq_paths=None):
    """routed(wires)＋DCT から盤間渡りを検出・分類し、連結記述を返す。

    **型判定(手本 From-To 準拠/茂泉様確定・実測で手本と100%一致):**
      主判定 = 号線がシーケンス図の線番(=制御ハーネス From-To)に有れば制御、無ければ太物。
      太物(電源)は図面にも From-To にも載らず製造工程で補完されるため『載っていない=太物』。
    **確度(◎誤答ゼロ):** 主判定を端子信号(classify)で裏付け、
      一致=◎/From-To=制御だが端子は太物寄り(例 201の計器配線)=○/From-To=太物だが端子は
      制御寄り=△(要確認)。シーケンス情報が無い図面は端子信号へフォールバック。

    戻り: {
      'control': [(号線, 送り面, 受け面)…],   # R-F(P)/L-F(R) 対象
      'power'  : [(号線, 送り面, 受け面)…],   # 渡り用TB 対象
      'faces'  : 検出に使えた面の数,
      'conf'   : {号線: '◎'|'○'|'△'},        # 二重検証の確度
      'conflicts': [(号線, 端子信号型, 由来図面型)…],  # 不一致=人が確認
      'rows'   : [...],
    }
    device→面 が作れない(盤情報なし)図面では空(回帰ゼロ)。"""
    wires = routed.get('wires', [])
    # device→面 は『内部配置図(盤枠)ベース』を優先(茂泉様の読み方: 盤はロケーター図でなく
    # 内部配置図/扉面の物理位置で決まる)。平の配置図D図面があり結果が得られればそれを採用、
    # 無い製番(DCTのみ/G図面のみ)は従来のDCTロケーター式へフォールバック(回帰ゼロ)。
    dev_face = _PL.build_device_face(dct_paths) or build_device_face(dct_paths)
    if not dev_face:
        return {'control': [], 'power': [], 'faces': 0,
                'conf': {}, 'conflicts': [], 'rows': []}
    # 線番の畳み込み(スケルトン由来の回路番号＋数字枝番 → 回路番号)。
    # 盤間渡りの号線名に限定して一貫適用(wires/seq_gousen 双方を同じ基底に揃える)。
    bases = build_circuit_bases(skel_paths)
    if bases:
        wires = [dict(w, gousen=normalize_gousen(w.get('gousen'), bases)) for w in wires]
    cross = detect_crossings(wires, dev_face, bases)
    evidence = build_source_evidence(skel_paths, seq_paths)
    if bases:
        evidence['seq_gousen'] = {normalize_gousen(x, bases)
                                  for x in evidence.get('seq_gousen', set())}
    # 主判定(手本 From-To 準拠/茂泉様確定): 号線がシーケンス図の線番(=制御ハーネス)に
    # 有れば制御、無ければ太物(太物は図面/From-Toに載らず製造で補完)。実測で手本と100%一致。
    seq_set = evidence.get('seq_gousen') or set()
    has_seq = bool(seq_paths) and bool(seq_set)
    face_order = sorted({f for ps in cross.values() for f in ps}, key=_face_key)
    out = {'control': [], 'power': [],
           'faces': len(face_order),
           'conf': {}, 'conflicts': []}
    for g, faces in cross.items():
        fs = sorted(faces, key=_face_key)
        send, recv = fs[0], fs[-1]          # 左(最小面)=送り元, 右(最大面)=受け先
        term = classify(g, wires)           # 端子信号(裏付け/シーケンス無し時のフォールバック)
        if has_seq:
            if g in seq_set:
                typ = 'control'             # 手本From-To(制御ハーネス)に在る=制御
            elif bases and g not in bases:
                # 太物候補だが、シーケンス線番にもスケルトン回路番号にも無い
                # = 本物のフィーダでない(機器番号の誤認等の文字化け/産物) → 除外(確認依頼に出さない)
                continue
            else:
                typ = 'power'               # シーケンスに無く回路番号=本物の太物フィーダ
        else:
            typ = term                      # シーケンス情報が無い図面はフォールバック
        # 確度: 主判定と端子信号の突合(二重検証)
        if typ == term:
            out['conf'][g] = '◎'            # 一致=確定
        elif typ == 'control':
            out['conf'][g] = '○'            # From-To=制御 だが端子は太物寄り(例 201の計器配線)
        else:
            out['conf'][g] = '△'            # From-To=太物 だが端子は制御寄り → 要確認
            out['conflicts'].append((g, term, typ))
        out[typ].append((g, send, recv))
    out['control'].sort()
    out['power'].sort()
    out['conflicts'].sort()
    # 太物フィーダの電線サイズ(現場ルール: 2.0sq超=直接渡り)。スケルトンのCABLE_SIZEから。
    cs = build_cable_sizes(skel_paths)
    out['sizes'] = {g: cs[g] for g, _s, _r in out['power'] if g in cs}
    out['rows'] = _rows(out['control'], out['power'], out['conf'], face_order, out['sizes'])
    return out


def _face_key(f):
    """面(盤番号)を物理順に並べるキー。数字前置を数値、以降を文字列で比較。"""
    m = re.match(r'^(\d+)', str(f))
    return (int(m.group(1)) if m else 10 ** 9, str(f))


def _boundaries(send, recv, face_order):
    """送り面〜受け面の間の『隣接する物理盤境界』ペア列を返す(通過面も中継)。
    手本では3面跨ぎは各盤境界ごとにR-F/L-Fを置くため、送受の間の全境界を列挙する。"""
    order = list(face_order or [])
    if order and send in order and recv in order:
        i, j = order.index(send), order.index(recv)
        if i > j:
            i, j = j, i
        seg = order[i:j + 1]
    else:
        seg = sorted({send, recv}, key=_face_key)
    return list(zip(seg, seg[1:]))


def _rows(control, power, conf=None, face_order=None, sizes=None):
    """検出した渡りを手本形式の連結行(端点行)に変換。

    **多面跨ぎ(手本準拠):** 送り面〜受け面の間の各盤境界に R-F(P)[左面]/L-F(R)[右面] を置く。
    中間面は『左から受ける L-F(R) ＋ 右へ送る R-F(P)』の両方を持つ(通過面も中継)。
    **コア番号(白-N):** 信号ごとに一貫(全境界で同じ芯番号)。採番順の規約は不要で、送り=受けの
    一致が要件との茂泉様方針。
    **太物(現場ルール/茂泉様): 2.0sq超は盤間を『直接1本渡り』(たまに渡りTB)。** 渡り用TBでなく
    直接渡りとして各境界の両面に記す。電線サイズ(sizes)が取れれば端子に併記(例 60sq)。
    戻り: [{'面','号線','機器','端子','型','確度'}]。"""
    conf = conf or {}
    sizes = sizes or {}
    rows = []
    kc = 0                                      # 制御信号ごとの芯番号(全境界で一貫)
    for g, s, r in control:
        kc += 1
        core = f'白-{kc}'
        c = conf.get(g, '○')
        for a, b in _boundaries(s, r, face_order):
            rows.append({'面': a, '号線': g, '機器': 'R-F(P)', '端子': core, '型': '制御', '確度': c})
            rows.append({'面': b, '号線': g, '機器': 'L-F(R)', '端子': core, '型': '制御', '確度': c})
    for g, s, r in power:
        c = conf.get(g, '○')
        term = sizes.get(g, '') or g           # サイズが取れれば併記、無ければ号線名
        for a, b in _boundaries(s, r, face_order):
            rows.append({'面': a, '号線': g, '機器': '直接渡り(太物)', '端子': term, '型': '太物', '確度': c})
            rows.append({'面': b, '号線': g, '機器': '直接渡り(太物)', '端子': term, '型': '太物', '確度': c})
    return rows
