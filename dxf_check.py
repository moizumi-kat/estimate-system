# -*- coding: utf-8 -*-
"""
電気図面チェックエンジン (dxf_check.py)

盤製作用DXF図面（外形図G・結線図H等）のブロック属性を構造化抽出し、
設計ミス・不整合・記載漏れを決定論的に検出する。

前提: 図面の各機器は INSERT ブロックとして描かれ、属性(ATTRIB)に
  PARTS/DEVICE/DEVICE1(回路・系統番号)/MAKER/TYPE/SPEC1/CODE/GCODE/
  QUANTITY/LOAD1/KW 等の構造化データを持つ。
  → AIの読み取り推測に頼らず、属性値そのものから厳密にチェックできる。

出力: findings のリスト。各 finding は
  { sev, cat, dwg, target, msg, suggest } の dict。
    sev  : '重大' | '警告' | '注意'
    cat  : '電気的整合性' | '記載漏れ・欠落' | '構成ルール違反' | '重複・二重計上'
    dwg  : 図番(例 030-H001)
    target: 該当機器・箇所
    msg  : 指摘内容
    suggest: 推奨対応
"""
import re, io, tempfile, os

SEV_ERROR = '重大'
SEV_WARN  = '警告'
SEV_INFO  = '注意'

CAT_ELEC   = '電気的整合性'
CAT_MISS   = '記載漏れ・欠落'
CAT_RULE   = '構成ルール違反'
CAT_DUP    = '重複・二重計上'

# 端子台系とみなす PARTS 名(記載漏れチェックの主対象)
TB_PARTS = ('TB', '端子台', '端子盤', '負荷端子台', '低圧クリート')
# 遮断器系 PARTS(容量・極数チェックの対象)
BREAKER_PARTS = ('MCCB', 'ELCB', 'MCB', 'ELB', 'NFB', 'ブレーカ')
# 品番(CODE)を必ず持つべき実機器(付属・表示灯・情報ブロック等は除外)
CODE_REQUIRED_PARTS = ('MCCB', 'ELCB', 'MCB', 'ELB', 'TB', 'CT', 'MC', 'MGS')
# 品番が無くても許容する(付属・注記・情報系)
CODE_OPTIONAL_PARTS = ('系統情報', '電線サイズ', '銘板', '圧着端子', '絶縁ﾊﾞﾘｱ',
                       '絶縁バリア', 'ETバー', 'ハンドル', 'ﾊﾝﾄﾞﾙ', 'CH', 'EF', '低圧クリート')


# ========== DXF 読み込み ==========
def _read_doc(data):
    """bytes / パス どちらでも DXF Document を返す。"""
    import ezdxf
    if isinstance(data, (bytes, bytearray)):
        with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as tf:
            tf.write(data); path = tf.name
        try:
            return ezdxf.readfile(path)
        finally:
            try: os.unlink(path)
            except Exception: pass
    return ezdxf.readfile(data)


def parse_dxf(data, fallback_name=''):
    """DXF を構造化する。戻り値:
    { name, seiban, dwgno, kind_raw, kind, components:[...], raw_title:{} }
      kind: 'G'(外形図/配置図) | 'H'(結線図) | '?'
      components: [{ block, x, y, parts, device, dno, maker, model,
                     spec, spec2, code, gcode, qty, load, kw, volt, attrs }]
    """
    doc = _read_doc(data)
    msp = doc.modelspace()
    title = {}
    comps = []
    labels = []  # 負荷名称ラベル(PARTSを持たないがLOAD1/負荷名称を持つ別ブロック)
    for e in msp:
        if e.dxftype() != 'INSERT':
            continue
        atts = {}
        for a in e.attribs:
            v = (a.dxf.text or '').strip()
            if v:
                # 同一タグが複数あれば最初を優先(見出し行の重複対策)
                atts.setdefault(a.dxf.tag, v)
        if not atts:
            continue
        bname = e.dxf.name
        if 'FRAME' in bname.upper() and ('SEIBAN' in atts or 'DWGNAME2' in atts):
            title = atts
            continue
        try:
            ins = e.dxf.insert; x, y = round(ins.x, 1), round(ins.y, 1)
        except Exception:
            x, y = 0.0, 0.0
        if 'PARTS' not in atts:
            ld = atts.get('LOAD1', '') or atts.get('負荷名称', '')
            # 見出し行(値がラベル名そのもの)は除外
            if ld and ld not in ('負荷名称', '機械No.', '負荷容量'):
                labels.append(dict(x=x, y=y, load=ld, kw=atts.get('KW', '')))
            continue
        comps.append(dict(
            block=bname, x=x, y=y,
            parts=atts.get('PARTS', ''),
            device=atts.get('DEVICE', ''),
            dno=atts.get('DEVICE1', ''),
            maker=atts.get('MAKER', ''),
            model=atts.get('TYPE', ''),
            spec=atts.get('SPEC1', ''),
            spec2=atts.get('SPEC2', ''),
            code=atts.get('CODE', ''),
            gcode=atts.get('GCODE', ''),
            qty=atts.get('QUANTITY', ''),
            load=atts.get('LOAD1', '') or atts.get('負荷名称', ''),
            kw=atts.get('KW', ''),
            volt=atts.get('C1', '') or atts.get('SPEC6', ''),
            attrs=atts,
        ))
    # 負荷名称ラベルを同じ行(y近接)の遮断器に対応付ける(負荷名は別ブロックに分離されている)
    if labels:
        brs = [c for c in comps if _is_breaker(c)]
        ys = sorted(c['y'] for c in brs)
        pitches = [ys[i + 1] - ys[i] for i in range(len(ys) - 1) if ys[i + 1] - ys[i] > 1]
        tol = max(20, (min(pitches) if pitches else 120) * 0.5)
        for c in brs:
            if c.get('load'):
                continue
            near = [lb for lb in labels if abs(lb['y'] - c['y']) <= tol]
            if near:
                near.sort(key=lambda lb: abs(lb['y'] - c['y']))
                c['load'] = near[0]['load']
                if not c.get('kw'):
                    c['kw'] = near[0].get('kw', '')

    dwgno = title.get('DWGNAME2', '') or fallback_name
    kind_raw = title.get('TITLE2', '')
    return dict(
        name=fallback_name or dwgno,
        seiban=title.get('SEIBAN', ''),
        dwgno=dwgno,
        kind_raw=kind_raw,
        kind=classify(kind_raw, dwgno),
        components=comps,
        raw_title=title,
    )


def classify(kind_raw, dwgno=''):
    """図面種別を判定。結線図=H / 外形図・配置図=G。"""
    s = (kind_raw or '') + ' ' + (dwgno or '')
    if '結線' in s:
        return 'H'
    if any(k in s for k in ['外形', '配置', 'ロードセンター', '組立', 'キュービクル', '盤外形', '内部機器']):
        return 'G'
    # 図番の -H / -G サフィックスで補完
    m = re.search(r'-([GH])\d', dwgno or '', re.I)
    if m:
        return m.group(1).upper()
    return '?'


# ========== 属性ヘルパ ==========
def _amp_pair(spec):
    """'3P100/150AT' → (frame=100, trip=150, pole=3)。無ければ None。
    トリップ(第2要素)を主幹/分岐容量比較の基準値として使う。"""
    if not spec:
        return None
    s = spec.upper().replace(' ', '')
    pole = None
    mp = re.search(r'(\d)P', s)
    if mp:
        pole = int(mp.group(1))
    # 極数表記(3P/2P/4P)を除去してから電流値を拾う(3Pの'3'を誤読しない)
    s_amp = re.sub(r'\dP', '', s)
    # 100/150AT や 225/200 形式(枠/トリップ)
    m = re.search(r'(\d+)\s*/\s*(\d+)', s_amp)
    if m:
        return (int(m.group(1)), int(m.group(2)), pole)
    # 単一値: AT優先、無ければAF、無ければ最初の数値
    for pat in (r'(\d+)\s*AT', r'(\d+)\s*AF', r'(\d+)'):
        m2 = re.search(pat, s_amp)
        if m2:
            v = int(m2.group(1))
            return (v, v, pole)
    return None


def _trip(spec):
    """定格電流(トリップ/AT)を返す。明示されている場合のみ。
    '3P100/75AT'→75, '3P50/15AT'→15, '225AT'→225。
    '3P100AF'(枠のみ)や裸の'100'は整定不明として None。"""
    if not spec:
        return None
    s = re.sub(r'\dP', '', spec.upper().replace(' ', ''))
    m = re.search(r'(\d+)\s*/\s*(\d+)', s)   # 枠/トリップ
    if m:
        return int(m.group(2))
    m = re.search(r'(\d+)\s*AT', s)          # 単独AT表記
    if m:
        return int(m.group(1))
    return None                              # AFのみ/裸値は不明


def _volt_of(comp, sys_volt=''):
    """機器の電圧帯(200V/400V/100V/HV)。機器属性→系統電圧の順。"""
    for src in (comp.get('volt', ''), comp.get('spec', ''), sys_volt):
        s = (src or '').upper()
        if '400' in s or '415' in s or '440' in s: return '400V'
        if '200' in s or '210' in s or '220' in s: return '200V'
        if '100' in s or '105' in s: return '100V'
        if 'KV' in s or '6.6' in s or '7.2' in s: return 'HV'
    return ''


def _is_tb(comp):
    p = comp.get('parts', '')
    return any(t in p for t in TB_PARTS)


def _is_breaker(comp):
    p = comp.get('parts', '')
    return any(b == p or b in p for b in BREAKER_PARTS)


def _spare(comp):
    return '予備' in (comp.get('load', '') or '')


# ========== チェック本体 ==========
def run_checks(drawings):
    """drawings: parse_dxf の結果リスト。findings のリストを返す。"""
    findings = []
    for d in drawings:
        findings += _check_internal(d)
    findings += _check_cross(drawings)
    # 完全に同一の指摘を排除
    uniq = []
    seen = set()
    for f in findings:
        k = (f['sev'], f['cat'], f['dwg'], f['target'], f['msg'])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)
    # 重大度順に並べる
    order = {SEV_ERROR: 0, SEV_WARN: 1, SEV_INFO: 2}
    uniq.sort(key=lambda f: order.get(f['sev'], 9))
    return uniq


# ---- 単一図面内チェック ----
def _check_internal(d):
    out = []
    dwg = d['dwgno'] or d['name']
    comps = d['components']
    devices = [c for c in comps if _is_breaker(c) or _is_tb(c)]

    # 系統電源電圧(系統情報ブロックの電源種別)
    sys_volt = ''
    for c in comps:
        if c['parts'] == '系統情報':
            sys_volt = c['attrs'].get('3.電源種別-2', '') or c['attrs'].get('2.電源種別-1', '')
            break

    # --- 電気的整合性: 遮断器のフレーム(AF) < トリップ(AT) は物理的に不可能 ---
    for c in comps:
        if not _is_breaker(c):
            continue
        pr = _amp_pair(c.get('spec', ''))
        if pr and pr[0] < pr[1]:
            out.append(_f(SEV_ERROR, CAT_ELEC, dwg,
                _tgt(c),
                f'遮断器のフレーム{pr[0]}Aよりトリップ{pr[1]}ATが大きい（{c["spec"]}）。物理的に成立しません。',
                'フレーム容量(AF)≧定格電流(AT)になるよう型式/整定を見直してください。'))

    # --- 電気的整合性: 主幹 < 分岐 の容量逆転 ---
    # トリップ(AT)が明示された遮断器同士のみ比較する(AFのみ=整定不明は対象外)。
    sys_no = _sys_no_of(comps)
    mains = [c for c in comps if _is_breaker(c) and _is_main(c, sys_no)]
    branches = [c for c in comps if _is_breaker(c) and not _is_main(c, sys_no) and not _spare(c)]
    main_trips = [(c, _trip(c['spec'])) for c in mains if _trip(c.get('spec', ''))]
    if main_trips and branches:
        m_main, m_at = max(main_trips, key=lambda t: t[1])
        for b in branches:
            bt = _trip(b.get('spec', ''))
            if bt and bt > m_at:
                out.append(_f(SEV_ERROR, CAT_ELEC, dwg,
                    _tgt(b),
                    f'分岐 {_tgt(b)} の定格 {bt}AT が主幹 {_tgt(m_main)} の {m_at}AT を上回っています。',
                    '主幹容量 ≧ 分岐容量になるよう、主幹/分岐のトリップ値と保護協調を確認してください。'))

    # --- 電気的整合性: 系統電圧と機器電圧の不一致 ---
    sv = _volt_norm(sys_volt)
    if sv:
        for c in devices:
            cv = _volt_of(c)
            if cv and cv != sv and c.get('volt'):
                out.append(_f(SEV_WARN, CAT_ELEC, dwg,
                    _tgt(c),
                    f'系統電源{sv}に対し機器電圧が{cv}表記です（{c.get("volt")}）。',
                    '電源電圧と機器定格電圧の整合を確認してください。'))

    # --- 記載漏れ: 実機器の品番(CODE)欠落（結線図のみ。外形図は品番省略が通常）---
    if d['kind'] == 'H':
        for c in comps:
            p = c.get('parts', '')
            if any(k in p for k in CODE_OPTIONAL_PARTS):
                continue
            if p in CODE_REQUIRED_PARTS and not c.get('code') and not _spare(c):
                out.append(_f(SEV_WARN, CAT_MISS, dwg,
                    _tgt(c),
                    f'{p} の品番(CODE)が未記入です。',
                    '手配に必要な品番を記入してください（未定なら明示）。'))

    # --- 記載漏れ: 結線図の分岐遮断器に負荷名称が無い(予備・主幹を除く)。1図面1件に集約 ---
    if d['kind'] == 'H':
        blanks = [c for c in comps if _is_breaker(c) and not _is_main(c)
                  and not _spare(c) and not c.get('load')]
        if blanks:
            names = '、'.join(_tgt(c) for c in blanks[:6]) + ('…' if len(blanks) > 6 else '')
            out.append(_f(SEV_INFO, CAT_MISS, dwg,
                f'{len(blanks)}回路',
                f'負荷名称が未記入の分岐が{len(blanks)}件あります（{names}）。',
                '接続負荷名を記入してください（予備なら「予備」と明記）。'))

    # --- 重複・二重計上: 同一機器の完全重複(同座標・同回路番号・同品番) ---
    seen = {}
    for c in comps:
        if c['parts'] in ('系統情報',):
            continue
        key = (c['parts'], c.get('dno', ''), c.get('gcode', '') or c.get('code', ''),
               round(c['x']), round(c['y']))
        if key in seen and c.get('gcode'):
            out.append(_f(SEV_WARN, CAT_DUP, dwg,
                _tgt(c),
                f'同一機器（{c["parts"]} 回路{c.get("dno","")} {c.get("gcode","")}）が同じ位置に重複配置されています。',
                '二重配置/二重計上でないか確認してください。'))
        seen[key] = True

    # --- 記載漏れ: 負荷端子台/端末処理の記載漏れ(同系統内の一貫性から検出) ---
    if d['kind'] == 'H':
        out += _check_load_termination(d, dwg, comps)

    # --- 電気的整合性: 結線図の回路番号(DEVICE1)の重複 ---
    if d['kind'] == 'H':
        dnos = {}
        for c in comps:
            if not _is_breaker(c):
                continue
            dn = c.get('dno', '')
            if dn:
                dnos.setdefault(dn, []).append(c)
        for dn, cs in dnos.items():
            if len(cs) > 1:
                out.append(_f(SEV_WARN, CAT_ELEC, dwg,
                    f'回路番号 {dn}',
                    f'回路番号 {dn} が {len(cs)} 箇所で重複しています。',
                    '回路番号は一意になるよう振り直してください。'))

    return out


# 負荷側の端末処理とみなす PARTS(圧着端子 or 負荷端子台/端子台)
_TERMINATION_PARTS = ('圧着端子', '負荷端子台', 'TB', '端子台')


def _check_load_termination(d, dwg, comps):
    """結線図の負荷端子台/端末処理の記載漏れを検出。
    同一図面内で、大半の分岐回路には負荷側端末(圧着端子/負荷端子台)が付くのに
    一部の回路だけ付いていない場合、その回路を「記載漏れの可能性」として指摘する。
    ※全回路が別方式(ETバー等)で端末が無い図面では発火しない(誤検出防止)。"""
    out = []
    sys_no = _sys_no_of(comps)
    branches = [c for c in comps if _is_breaker(c) and not _is_main(c, sys_no) and not _spare(c)]
    if len(branches) < 2:
        return out
    # 負荷側端末(圧着端子 or 系統番号でなく回路番号を持つTB)。上位の受端子台は除く。
    terms = []
    for c in comps:
        p = c.get('parts', '')
        if p == '圧着端子':
            terms.append(c)
        elif p in ('TB', '端子台', '負荷端子台'):
            dn = str(c.get('dno', ''))
            # 系統情報と同じ番号(=受電端子台)は負荷端末ではない
            if dn and dn != sys_no:
                terms.append(c)
    if not terms:
        return out  # この図面は負荷端末を個別に描かない方式 → 判定しない
    # 分岐回路の行ピッチから、同一行とみなす y 許容幅を決める
    ys = sorted(c['y'] for c in branches)
    pitches = [ys[i + 1] - ys[i] for i in range(len(ys) - 1) if ys[i + 1] - ys[i] > 1]
    pitch = min(pitches) if pitches else 120
    tol = max(20, pitch * 0.5)
    missing = []
    for b in branches:
        has = any(abs(t['y'] - b['y']) <= tol for t in terms)
        # 回路番号一致でも可
        if not has and b.get('dno'):
            has = any(str(t.get('dno', '')) == str(b['dno']) for t in terms)
        if not has:
            missing.append(b)
    # 大半(過半数)に端末があり、一部だけ欠けている場合のみ指摘
    if missing and len(missing) < len(branches):
        for b in missing:
            out.append(_f(SEV_WARN, CAT_MISS, dwg,
                _tgt(b),
                f'同系統の他の回路には負荷側端末（圧着端子/負荷端子台）があるのに、'
                f'{_tgt(b)}には見当たりません。負荷端子台の記載漏れの可能性があります。',
                '外部配線を接続する回路に負荷端子台/端末処理が必要か確認してください。'))
    return out


def _sys_no_of(comps):
    """図面の系統番号(系統情報ブロックのDEVICE1)。無ければ ''。"""
    for c in comps:
        if c.get('parts') == '系統情報' and c.get('dno'):
            return str(c['dno'])
    return ''


def _is_main(comp, sys_no=''):
    """主幹遮断器か。回路番号(DEVICE1)が系統番号と一致=主幹、
    系統番号+付番(例 系統2→201..207)=分岐、で判定する。"""
    dn = str(comp.get('dno', '') or '')
    if sys_no and dn:
        if dn == str(sys_no):
            return True
        # 系統番号で始まる長い番号は分岐(201,205,301...)
        if dn.startswith(str(sys_no)) and len(dn) > len(str(sys_no)):
            return False
    b = (comp.get('block', '') + ' ' + comp.get('device', '')).upper()
    if 'MCB_1' in b or 'MAIN' in b or '主幹' in comp.get('device', ''):
        return True
    # E250-SF 等の大型枠は主幹の可能性(補助判定)
    pr = _amp_pair(comp.get('spec', ''))
    return bool(pr and pr[0] >= 225 and comp.get('parts') == 'MCCB')


def _volt_norm(s):
    s = (s or '').upper()
    if '400' in s or '415' in s or '440' in s: return '400V'
    if '200' in s or '210' in s or '220' in s: return '200V'
    if '100' in s or '105' in s: return '100V'
    if 'KV' in s: return 'HV'
    return ''


# ---- 外形図G ↔ 結線図H クロスチェック ----
def _check_cross(drawings):
    out = []
    # 製番ごとにグループ化
    groups = {}
    for d in drawings:
        groups.setdefault(d.get('seiban', '') or '(製番不明)', []).append(d)

    for seiban, ds in groups.items():
        gs = [d for d in ds if d['kind'] == 'G']
        hs = [d for d in ds if d['kind'] == 'H']
        if not gs or not hs:
            continue  # 片方しか無ければクロスチェック不可(内部チェックのみ)

        # 結線図側の全機器を (系統番号, PARTS種別) と GCODE で索引化
        h_comps = []
        for h in hs:
            for c in h['components']:
                if c['parts'] in ('系統情報',):
                    continue
                h_comps.append((h, c))
        h_by_sys_parts = {}
        h_by_gcode = {}
        for h, c in h_comps:
            h_by_sys_parts.setdefault((_sys_of(c), c['parts']), []).append((h, c))
            g = c.get('gcode') or c.get('code')
            if g:
                h_by_gcode.setdefault(g, []).append((h, c))

        # 結線図がアップされている系統の集合(この系統だけ網羅性を判定できる)
        h_systems = set(_sys_of(c) for _, c in h_comps if _sys_of(c))

        # --- 記載漏れ: 外形図にある機器が結線図に無い ---
        # 該当系統の結線図が揃っている場合のみ判定（部分アップ時の誤検出を防ぐ）。
        seen_miss = set()
        for g in gs:
            for c in g['components']:
                p = c['parts']
                if p in ('系統情報', '電線サイズ', '銘板', 'ハンドル', 'ﾊﾝﾄﾞﾙ',
                         '絶縁ﾊﾞﾘｱ', '絶縁バリア', 'ETバー', 'CH', 'EF', '低圧クリート'):
                    continue
                if not _tb_or_breaker(c):
                    continue
                sysno = _sys_of(c)
                # 系統番号が無い / その系統の結線図が未アップ → 網羅性は判定不可
                if not sysno or sysno not in h_systems:
                    continue
                gcode = c.get('gcode') or c.get('code')
                found = (gcode and gcode in h_by_gcode) or ((sysno, p) in h_by_sys_parts)
                if found:
                    continue
                key = (sysno, p, gcode or c.get('model', ''))
                if key in seen_miss:
                    continue
                seen_miss.add(key)
                sev = SEV_ERROR if _is_tb(c) else SEV_WARN
                label = '負荷端子台' if _is_load_tb(c) else p
                out.append(_f(sev, CAT_MISS, g['dwgno'],
                    f'{label} 系統{sysno}',
                    f'外形図にある{label}（{c.get("model","")} {c.get("gcode","")}）が'
                    f'系統{sysno}の結線図に見当たりません。',
                    '結線図への反映漏れがないか確認してください（端子台の記載漏れ・二重手配の原因）。'))

        # --- 数量不一致: 同一機器の外形図/結線図での数量差 ---
        for g in gs:
            for c in g['components']:
                gcode = c.get('gcode') or c.get('code')
                if not gcode or gcode not in h_by_gcode:
                    continue
                gq = _num(c.get('qty'))
                hq = sum(_num(hc.get('qty')) for _, hc in h_by_gcode[gcode])
                if gq and hq and gq != hq and _tb_or_breaker(c):
                    out.append(_f(SEV_WARN, CAT_DUP, g['dwgno'],
                        _tgt(c),
                        f'{c["parts"]}（{gcode}）の数量が外形図={gq}・結線図={hq}で一致しません。',
                        '数量の整合を確認してください（二重計上/不足の原因）。'))

        # --- 電気的整合性: 系統番号の順序不一致(外形図の物理配置 ↔ 結線図番号) ---
        out += _check_sys_order(gs, hs, seiban)

    return out


def _check_sys_order(gs, hs, seiban):
    """外形図の端子台(系統)の物理配置順(左→右)と、結線図の系統番号の
    昇順が食い違う場合に警告する（BOX/ET配線が逆になる典型ミス）。"""
    out = []
    # 外形図から系統端子台を x 昇順(左→右)に並べる
    tb_pos = []
    for g in gs:
        for c in g['components']:
            if _is_tb(c) and _sys_of(c) and str(_sys_of(c)).isdigit():
                tb_pos.append((c['x'], int(_sys_of(c)), g['dwgno']))
    if len(tb_pos) < 2:
        return out
    tb_pos.sort()
    seq = [s for _, s, _ in tb_pos]
    # 主要な系統(小さい番号帯)だけで単調性を見る。逆順・入れ替わりを検出。
    core = [s for s in seq if s < 100]
    inversions = sum(1 for i in range(len(core) - 1) if core[i] > core[i + 1])
    if core and inversions:
        out.append(_f(SEV_INFO, CAT_ELEC, gs[0]['dwgno'],
            f'系統配置順 {core}',
            f'外形図の端子台（系統）の左→右配置順が {core} で、系統番号の昇順と一致しません。'
            '（多基盤では正常な場合もあります）',
            '外形図の盤配置順と結線図の系統番号順が合っているか確認してください。'
            '順番違いは外部配線・ET/BOX端子の指示が逆になる典型ミスです。'))
    return out


# ========== 小物ヘルパ ==========
def _sys_of(comp):
    """機器の属する系統番号。DEVICE1 の先頭数字帯を系統とみなす。"""
    dn = str(comp.get('dno', '') or '')
    m = re.match(r'(\d+)', dn)
    if m:
        v = m.group(1)
        # 3桁(例 501,502)は系統5系、2桁は回路番号→上1桁を系統とみなさない
        return v
    return ''


def _is_load_tb(comp):
    d = (comp.get('device', '') + comp.get('parts', ''))
    return '負荷' in d


def _tb_or_breaker(comp):
    return _is_tb(comp) or _is_breaker(comp)


def _num(v):
    try:
        return int(re.sub(r'[^\d]', '', str(v)) or 0)
    except Exception:
        return 0


def _tgt(comp):
    parts = comp.get('parts', '')
    dno = comp.get('dno', '')
    model = comp.get('model', '')
    spec = comp.get('spec', '')
    bits = [b for b in [parts, (f'({dno})' if dno else ''), model, spec] if b]
    return ' '.join(bits).strip() or comp.get('block', '機器')


def _f(sev, cat, dwg, target, msg, suggest):
    return dict(sev=sev, cat=cat, dwg=dwg, target=target, msg=msg, suggest=suggest)


# ========== 解析カバレッジ ==========
def coverage(drawings):
    """アップされた図面の内訳と、クロスチェック可否を返す(UI表示用)。"""
    seibans = {}
    for d in drawings:
        s = d.get('seiban', '') or '(製番不明)'
        e = seibans.setdefault(s, {'G': 0, 'H': 0, 'other': 0, 'dwgs': []})
        e['dwgs'].append(dict(dwgno=d['dwgno'], kind=d['kind'],
                              kind_raw=d['kind_raw'], n=len(d['components'])))
        if d['kind'] == 'G': e['G'] += 1
        elif d['kind'] == 'H': e['H'] += 1
        else: e['other'] += 1
    notes = []
    for s, e in seibans.items():
        if e['G'] and not e['H']:
            notes.append(f'製番{s}: 外形図のみ。結線図もアップすると外形図↔結線図の突合ができます。')
        elif e['H'] and not e['G']:
            notes.append(f'製番{s}: 結線図のみ。外形図もアップすると記載漏れ・端子台の突合ができます。')
    return dict(seibans=seibans, notes=notes)


# ========== 集計 ==========
def summarize(findings):
    c = {SEV_ERROR: 0, SEV_WARN: 0, SEV_INFO: 0}
    for f in findings:
        c[f['sev']] = c.get(f['sev'], 0) + 1
    return dict(total=len(findings), error=c[SEV_ERROR], warn=c[SEV_WARN], info=c[SEV_INFO])
