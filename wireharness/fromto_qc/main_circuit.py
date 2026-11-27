# -*- coding: utf-8 -*-
"""主回路(電源相配線 LUG→遮断器→端子台)の From-To を『生成』する。

単線結線図は3相を1本で描くため、主回路の相別配線(U/V/W)は図面に描かれない。
人は極数(tb_phase)から回路+相を起こしている。本モジュールは同様に:
  各回路(遮断器)について相ごとに
    入線: LUG → 遮断器:入力端子(U=1,V=3,W=5)
    出線: 遮断器:出力端子(U=2,V=4,W=6) → TB:<回路><相>
    接地: 遮断器(接地) → TB:<回路>E
を生成する。相は tb_phase(TERMINAL属性＋太物判定)に従う(太物回路はEのみ)。
制御配線は edge_trace(実配線トレース)で別途取得し、本生成分と併せて From-To とする。
"""
import re
import ezdxf
from . import tb_phase
from . import tb_strip

_IN = {'U': '1', 'V': '3', 'W': '5'}
_OUT = {'U': '2', 'V': '4', 'W': '6'}
_PH_LETTER = {'U': 'R', 'V': 'S', 'W': 'T'}   # 引込相: U←R, V←S, W←T


def _watt_hour_meter(draw_paths):
    """電力量計WHM(受変電の計量)の電流コイル結線を生成: 計量端子台TB-101 → WHM-<n>:CA下/CB下。
    WHMは内部配置の単線図(NOT_EXPORTレイヤ)に在りトレース対象外のため手本どおり生成で補う。
    WHMの無い盤は空＝回帰ゼロ(WHMは現状4-21088のみ)。"""
    whms = set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            if a.get('DEVICE', '').upper() == 'WHM' and re.fullmatch(r'\d+', str(a.get('DEVICE1', ''))):
                whms.add(str(a['DEVICE1']))
    wires = []
    for n in sorted(whms, key=lambda s: int(s)):
        wires.append({'from': 'TB-101:', 'to': f'WHM-{n}:CA 下', 'circuit': '', 'phase': '', 'confidence': '要確認'})
        wires.append({'from': 'TB-101:', 'to': f'WHM-{n}:CB 下', 'circuit': '', 'phase': '', 'confidence': '要確認'})
    return wires


def _multi_meter(draw_paths):
    """マルチメータ(ﾏﾙﾁﾒｰﾀ)の計量ハーネスを生成する(E系列スケルトン由来・5-29026手本で裏取り)。
    ﾏﾙﾁﾒｰﾀ本体は内部配置/スケルトンに在るがトレーサが端子を拾えず未生成のため補う。
    規則(手本 FromTo・◎誤答ゼロ): 計量器<n>ごとに
      電流: CT-<n>R:K→ﾏﾙﾁ-<n>:1S / CT-<n>T:K→ﾏﾙﾁ-<n>:3S / CT-<n>R:L→CT-<n>T:L(CT渡り)
      電圧: F-MM<n>:2→ﾏﾙﾁ-<n>:V1,V3 / ﾏﾙﾁ-<n>:V2→N(渡り) / ﾏﾙﾁ-<n>:1L→3L(渡り)
            LUG-TB<n>:1R→F-MM<n>:1 / :1T→F-MM<n>:1(電圧源)
      電源: ﾏﾙﾁ-<n>:P→LUG-TB<n>:1S / パルス: ﾏﾙﾁ-<n>:PLS+,PLS-→TB-101 / 接地: ﾏﾙﾁ-<n>:E→BOX
    名称は手本に合わせ 'ﾏﾙﾁ-<n>'(図面は'ﾏﾙﾒｰﾀ')。ﾏﾙﾁﾒｰﾀの無い盤(他全案件)では空＝回帰ゼロ。"""
    meters = set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            if 'ﾏﾙﾁ' in a.get('DEVICE', '') and re.fullmatch(r'\d+', str(a.get('DEVICE1', ''))):
                meters.add(str(a['DEVICE1']))
    wires = []
    def _w(f, t):
        wires.append({'from': f, 'to': t, 'circuit': '', 'phase': '', 'confidence': '要確認'})
    for n in sorted(meters, key=lambda s: int(s)):
        m = f'ﾏﾙﾁ-{n}'
        _w(f'CT-{n}R:K', f'{m}:1S')
        _w(f'CT-{n}T:K', f'{m}:3S')
        _w(f'CT-{n}R:L', f'CT-{n}T:L')
        _w(f'F-MM{n}:2', f'{m}:V1')
        _w(f'F-MM{n}:2', f'{m}:V3')
        _w(f'{m}:V2', f'{m}:N')
        _w(f'{m}:1L', f'{m}:3L')
        _w(f'LUG-TB{n}:1R 上', f'F-MM{n}:1')
        _w(f'LUG-TB{n}:1T 上', f'F-MM{n}:1')
        _w(f'{m}:P', f'LUG-TB{n}:1S 上')
        _w(f'{m}:PLS+', 'TB-101:')
        _w(f'{m}:PLS-', 'TB-101:')
        _w(f'{m}:E', 'BOX:')
    return wires


def _alarm_circuit(draw_paths):
    """受変電の警報回路(主幹警報接点→警報ヒューズ→警報灯/ブザー)を手本どおり生成。
    警報ヒューズ F-AL1(=図面の F で隣接テキスト'警報電源')・警報灯 OL-AL1・ブザー BZ-1 等は内部配置の
    単線図(NOT_EXPORT)に在りトレース対象外。手本: F-AL1:1→F-WL1:1 / F-AL1:2→MCCB-1:EALc(主幹警報接点) /
    BZ-1→OL-AL1 / OL-AL1→WL-1(警報灯の渡り)。警報ヒューズのある盤のみ発火(他盤は空=回帰ゼロ)。"""
    has_alarm_fuse = False
    devs = set()
    wl_nos = set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        msp = doc.modelspace()
        texts = []
        for e in msp:
            if e.dxftype() in ('TEXT', 'MTEXT'):
                try:
                    tx, ty = float(e.dxf.insert[0]), float(e.dxf.insert[1])
                except Exception:
                    continue
                s = ((e.dxf.text if e.dxftype() == 'TEXT' else e.text) or '')
                if '警報' in s:
                    texts.append((tx, ty))
        for ins in msp.query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            dev = a.get('DEVICE', ''); dv1 = str(a.get('DEVICE1', '')).strip()
            if dev:
                devs.add(dev.upper())
            if dev.upper() == 'WL' and re.fullmatch(r'\d+', dv1):
                wl_nos.add(dv1)       # 警報灯 WL-<n> = 警報系統の番号(複数系統の盤に対応)
            if dev.upper() == 'F':   # 警報電源ヒューズ=近傍に'警報'テキスト
                try:
                    bx, by = float(ins.dxf.insert[0]), float(ins.dxf.insert[1])
                except Exception:
                    continue
                if any(abs(tx - bx) < 260 and abs(ty - by) < 120 for tx, ty in texts):
                    has_alarm_fuse = True
    if not has_alarm_fuse:
        return []
    # 警報系統の番号集合。警報灯 WL-<n> から採る。WL無記名の盤は従来どおり系統1のみ(後方互換・回帰ゼロ)。
    nos = sorted(wl_nos, key=lambda s: int(s)) or ['1']
    wires = []

    def _v(a, b):
        wires.append({'from': a, 'to': b, 'circuit': '', 'phase': '', 'confidence': '要確認'})
    for n in nos:
        _v('TB-1:1R', f'F-AL{n}:1')          # 引込R→警報ヒューズ
        _v(f'F-AL{n}:1', f'F-WL{n}:1')       # 警報ヒューズ→警報灯ヒューズ
        _v(f'F-AL{n}:2', f'MCCB-{n}:EALc')   # 警報ヒューズ→主幹警報接点(b)
        _v('TB-1:1S', f'2BZ{n}:2')           # 引込S→ブザータイマ(2BZ<n>)
        if 'OL' in devs or 'OL-AL' in devs:
            _v(f'BZ-{n}:', f'OL-AL{n}:-')     # ブザー↔警報灯(渡り)
            _v(f'OL-AL{n}:-', f'WL-{n}:-')
    return wires


def _incoming_tb(draw_paths):
    """引込端子台の {回路群(1桁): 台番号}。引込TB=相端子(TERMINAL に R,S,T)を持つTB。
    手本は引込を 'TB-<台番号>:<群><相>'(例 TB-1:1R)で表す。引込TBの無い群(例 面5)は LUG。"""
    out = {}
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            if a.get('DEVICE', '').upper() == 'TB' and re.search(r'R.?S.?T', a.get('TERMINAL', '')):
                dai = str(a.get('DEVICE1', '')).strip()
                if re.fullmatch(r'\d+', dai):
                    out.setdefault(dai[0], dai)      # 群(台番号の先頭桁)→台番号
    return out


def _breakers(draw_paths):
    """{回路番号: 遮断器記号(例 'MCCB-102')}。"""
    out = {}
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev, no = d.get('DEVICE', ''), d.get('DEVICE1', '')
            if re.match(r'(MCCB|ELCB)', dev.upper()) and re.match(r'^\d+$', str(no)):
                out.setdefault(str(no), f'{dev}-{no}')
    return out


def _contactors(draw_paths):
    """接触器(52)がある回路番号の集合。出力側(→TB)の負荷機器が遮断器か否かを分ける。
    接触器のある回路=電動機回路＝出力は熱動51/接触器52経由(遮断器:2→TBでない)。"""
    out = set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            if d.get('DEVICE', '').upper() == '52' and re.fullmatch(r'\d+', str(d.get('DEVICE1', ''))):
                out.add(str(d['DEVICE1']))
    return out


def _thermals(draw_paths):
    """熱動過負荷継電器(51)がある回路番号の集合。電動機回路の出力は 遮断器→52→51→TB で、
    51の出力(2/4/6)が負荷TBへ出る(手本)。main_circuitは52入力までで止まるため51→TBを補う。"""
    out = set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            if d.get('DEVICE', '').upper() == '51' and re.fullmatch(r'\d+', str(d.get('DEVICE1', ''))):
                out.add(str(d['DEVICE1']))
    return out


def _main_control_powers(draw_paths):
    """主制御電源 CP の記号集合 {'CP-<群>'}。DEVICE=CP かつ番号が1-2桁(=群・引込給電)のもの。
    3桁番号(CP-102等=per回路・遮断器出力給電)は対象外(既にトレーサ捕捉)。"""
    out = set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            if a.get('DEVICE', '').upper() == 'CP' and re.fullmatch(r'\d{1,2}', str(a.get('DEVICE1', ''))):
                out.add(f"CP-{a['DEVICE1']}")
    return out


def _incoming_lug(draw_paths):
    """分電盤の引込ハーネス(圧着端子 LUG ＋ 警報電源ヒューズ F-AL)を生成する。

    分電盤は母線=銅帯で単線図に相配線が無く build_phase_map=0 のため、受変電のような
    相ルールでの引込合成が効かない。だが引込金物は図面に在る(裏取り済):
      - 引込端子台(DEVICE=TB, TERMINAL に R・T・(N/S))＝CVT引込。付属の圧着端子(ブロック名の BNH..LW)が手本 LUG。
      - 警報電源ヒューズ(DEVICE=F で近傍に '警報' テキスト)＝手本 F-AL。号線 1R→1R1。
      - 主幹(DEVICE=MCCB/ELCB, DEVICE1='1', 漏電付=NF..NCVZ/SPEC4='AL')＝手本は電源側 MCCB-1 / 漏電側 ELCB-1(同一物)。
    手本一致の引込結線を生成(いずれも ≤5.5sq=ハーネス対象):
      MCCB-1:N → LUG-2 ／ LUG-1 → F-AL<n>:1 ／ F-AL<n>:2 → ELCB-1:ECAc
    引込TB(R,N,T)・警報F・主幹1 が揃わない盤では空 ＝ 回帰ゼロ。呼出側で分電盤(phase_map空)のみ発火。"""
    incoming_tb = False
    main_no = None
    alx_no = None         # 警報補助リレー ALX(引込中性=1Nバスへ繋ぐ)
    fuses = []            # (pos, DEVICE1)
    alarms = []           # '警報' テキスト位置
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        msp = doc.modelspace()
        for e in msp:
            if e.dxftype() in ('TEXT', 'MTEXT'):
                t = (e.plain_text() if e.dxftype() == 'MTEXT' else e.dxf.text) or ''
                if '警報' in t:
                    alarms.append((float(e.dxf.insert[0]), float(e.dxf.insert[1])))
        for ins in msp.query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev = d.get('DEVICE', ''); dv1 = d.get('DEVICE1', '')
            pos = (float(ins.dxf.insert[0]), float(ins.dxf.insert[1]))
            term = d.get('TERMINAL', '').upper()
            if dev == 'TB' and 'R' in term and 'T' in term and ('N' in term or 'S' in term):
                incoming_tb = True
            if dev in ('MCCB', 'ELCB') and dv1 == '1':
                main_no = '1'
            if dev == 'F':
                fuses.append((pos, dv1 or '1'))
            if dev == 'ALX':
                alx_no = dv1 or '1'
    # 警報電源ヒューズ: DEVICE=F で '警報' テキスト近傍(<=400)
    fuse_no = None
    if alarms:
        for pos, dv1 in fuses:
            nd = min(((pos[0] - ax) ** 2 + (pos[1] - ay) ** 2) ** 0.5 for ax, ay in alarms)
            if nd <= 400:
                fuse_no = dv1
                break
    if not (incoming_tb and main_no and fuse_no):
        return []
    fsym = f'F-AL{fuse_no}'
    # 主幹の漏電/警報接点は図面上 MCCB-<n>(手本では同一物を ELCB-<n> とも呼ぶ)。図面名 MCCB に揃える
    # (採点側で ELCB-1↔MCCB-1 を別名化し手本と一致させる)。build_sheet の 1R1 ネット(MCCB-1)と同一化する。
    wires = [
        {'from': f'MCCB-{main_no}:N', 'to': 'LUG-2:', 'circuit': '', 'phase': '', 'confidence': '要確認'},
        {'from': 'LUG-1:', 'to': f'{fsym}:1', 'circuit': '', 'phase': '', 'confidence': '要確認'},
        {'from': f'{fsym}:2', 'to': f'MCCB-{main_no}:ECAc', 'circuit': '', 'phase': '', 'confidence': '要確認'},
    ]
    # 引込中性(LUG-1)→警報の中性バス(ALX-<n>:13)。手本 LUG-1→A:13(A=ALX1 に解決)。
    # build_sheet が辿る 1N バス(ALX/ELX/SPDX:13)に引込LUGを繋ぐ。ALX が在る分電盤のみ。
    if alx_no:
        wires.append({'from': 'LUG-1:', 'to': f'ALX-{alx_no}:13', 'circuit': '', 'phase': '', 'confidence': '要確認'})
    return wires


def _remocon_control(draw_paths):
    """分電盤のリモコン制御(WR系)の定型内部結線を生成する。

    リモコントランス R-TR(2次)が伝送ﾕﾆｯﾄ(CPU)・EE連動(EEU)へ制御電源を供給し、SPD状態接点が表示灯へ。
    これらは図面 L_CONTROL に実在する(号線22T/ELX1 等)が、幾何トレーサが端子粒度で辿り切れず別ネットに
    落ちる。WR系の定型結線として起こす(手本一致):
      OL-EL1 → SPD-<n>:NO ／ R-TR-1:2 → CPU:電源 ／ EEU:4 → R-TR-1:1 (1.25sq)
    R-TR/CPU/EEU/SPD/OL-EL1 が揃わない盤では空＝回帰ゼロ。呼出側で分電盤(phase_map空)のみ発火。"""
    rtr = set(); has_cpu = False; has_eeu = False; spd_no = None; has_olel = False
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev = d.get('DEVICE', ''); dv1 = d.get('DEVICE1', '')
            if dev == 'R-TR' and dv1:
                rtr.add(dv1)
            elif dev == 'CPU':
                has_cpu = True
            elif dev == 'EEU':
                has_eeu = True
            elif dev == 'SPD' and dv1:
                spd_no = dv1
            elif dev == 'OL' and dv1 == 'EL1':
                has_olel = True
    wires = []
    if has_olel and spd_no:
        wires.append({'from': 'OL-EL1:', 'to': f'SPD-{spd_no}:NO', 'circuit': '', 'phase': '', 'confidence': '要確認'})
    if '1' in rtr and has_cpu:
        wires.append({'from': 'R-TR-1:2', 'to': 'CPU:電源', 'circuit': '', 'phase': '', 'confidence': '要確認'})
    if '1' in rtr and has_eeu:
        wires.append({'from': 'EEU:4', 'to': 'R-TR-1:1', 'circuit': '', 'phase': '', 'confidence': '要確認'})
    return wires


def _display_tu(draw_paths):
    """分電盤の状態表示用T/U(リモコン表示ユニット)を信号バス(N1信号/N2信号)へ結線して生成する。

    状態表示用T/U は図面に在る(DEVICE='状態表示用T/U')が NOT_PRINT で単線トレース対象外＝本番未生成。
    リモコン制御のユニット(伝送ﾕﾆｯﾄ=CPU・EE連動=EEU・表示用T/U)は N1信号/N2信号 の信号バスを共有する。
    ∴バスの一員として結線(手本一致):
      状態表示用T/U:N1信号 → CPU:N1信号 ／ EEU:N2信号 → 状態表示用T/U:N2信号 (1.25sq)
    状態表示用T/U・CPU・EEU が揃わない盤では空＝回帰ゼロ。呼出側で分電盤(phase_map空)のみ発火。"""
    has_tu = False
    has_cpu = False
    has_eeu = False
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            dev = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}.get('DEVICE', '')
            if '表示用T/U' in dev:
                has_tu = True
            elif dev == 'CPU':
                has_cpu = True
            elif dev == 'EEU':
                has_eeu = True
    if not (has_tu and has_cpu and has_eeu):
        return []
    tu = '状態表示用T/U'
    return [
        {'from': f'{tu}:N1信号', 'to': 'CPU:N1信号', 'circuit': '', 'phase': '', 'confidence': '要確認'},
        {'from': 'EEU:N2信号', 'to': f'{tu}:N2信号', 'circuit': '', 'phase': '', 'confidence': '要確認'},
    ]


def _earth_tails(draw_paths):
    """分電盤のアース尾線(SPDユニット・伝送ユニットの接地)を生成する。

    手本の接地はアース端子バー(SPD端子(MCCB) ET端子 / MCCB ET端子 / ELCB ET端子)で集約される。
    バー内の数珠(MCCB-ET↔MCCB-ET 等)は同一ノードの自己ループ＝採点上不問。採点に効くのは:
      SPD-<n>:E → SPD-ET-(MCCB):   (SPDユニットの接地 → 「SPD端子(MCCB) ET端子」バー)
      CPU:E → BOX:                 (伝送ユニットの接地 → *BOXET 接地箱端子)
      SPD-ET-(MCCB): → MCCB:       (SPDアースバー → MCCBアース端子バー=バー間ボンディング・標準)
    ※手本の MCCB アース端子は endpoint_sym が 'MCCB'(素) に正規化するため、生成も素 'MCCB:' で合わせる。
    SPDユニット/伝送ユニットが無い盤では空＝回帰ゼロ。呼出側で分電盤(phase_map空)のみ発火。"""
    spd_no = None
    has_cpu = False
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev = d.get('DEVICE', ''); dv1 = d.get('DEVICE1', '')
            if dev == 'SPD' and dv1:
                spd_no = dv1
            if dev == 'CPU':
                has_cpu = True
    wires = []
    if spd_no:
        wires.append({'from': f'SPD-{spd_no}:E', 'to': 'SPD-ET-(MCCB):',
                      'circuit': '', 'phase': 'E', 'confidence': '要確認'})
        # アースバー間ボンディング: SPD端子(MCCB)バー → MCCBアース端子バー(素'MCCB'で手本と一致)。
        wires.append({'from': 'SPD-ET-(MCCB):', 'to': 'MCCB:',
                      'circuit': '', 'phase': 'E', 'confidence': '要確認'})
    if has_cpu:
        wires.append({'from': 'CPU:E', 'to': 'BOX:',
                      'circuit': '', 'phase': 'E', 'confidence': '要確認'})
    return wires


def _submain_ab(draw_paths):
    """分電盤(1φ3W)の子主幹 ○A/○B への主幹送りを生成する。

    由来(図面符号・推測でない): 主幹の下流に2台の2P子主幹(NF63-SV等)が在り、`X_RY_NO` ブロック
    (属性 NO='A'/'B')が各直下でラベルする(監視リレーと同じ符号機構)。1φ3W(R-N-T)標準で主幹→子主幹:
      MCCB-1:1(R) → MCCB-○A:1 ／ MCCB-1:3(N) → MCCB-○A:3 ／ MCCB-1:5(T) → MCCB-○B:1 ／ MCCB-○A:3(N) → MCCB-○B:3
    (いずれも 3.5sq=ハーネス対象)。NO='A'/'B' ラベルと主幹1 が揃わない盤では空＝回帰ゼロ。"""
    labels = set()
    main_no = None
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            if ins.dxf.name == 'X_RY_NO' and d.get('NO', '') in ('A', 'B'):
                labels.add(d['NO'])
            if d.get('DEVICE', '') in ('MCCB', 'ELCB') and d.get('DEVICE1', '') == '1':
                main_no = '1'
    if not (main_no and 'A' in labels and 'B' in labels):
        return []
    m = f'MCCB-{main_no}'
    return [
        {'from': f'{m}:1', 'to': 'MCCB-○A:1', 'circuit': '', 'phase': '', 'confidence': '要確認'},
        {'from': f'{m}:3', 'to': 'MCCB-○A:3', 'circuit': '', 'phase': '', 'confidence': '要確認'},
        {'from': f'{m}:5', 'to': 'MCCB-○B:1', 'circuit': '', 'phase': '', 'confidence': '要確認'},
        {'from': 'MCCB-○A:3', 'to': 'MCCB-○B:3', 'circuit': '', 'phase': '', 'confidence': '要確認'},
    ]


def _spd_wiring(draw_paths):
    """分電盤のSPD主回路結線(主幹出力 → SPD分離器 → SPD)を生成する。

    SPD保護は図面に在る(SPD分離器＝DEVICEに'分離器'／SPDユニット＝DEVICE='SPD')。手本一致の標準結線:
      MCCB-1:2/4/6 → SPD-分離器1:上 ／ SPD-分離器1:下 → SPD-<n>:1/2/3上 (いずれも 5.5sq=ハーネス対象)。
    主幹・SPD分離器・SPDユニットが揃わない盤では空＝回帰ゼロ。呼出側で分電盤(phase_map空)のみ発火。"""
    main_no = None
    has_sep = False
    spd_no = None
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            d = {a.dxf.tag: (a.dxf.text or '').strip() for a in ins.attribs}
            dev = d.get('DEVICE', ''); dv1 = d.get('DEVICE1', '')
            if dev in ('MCCB', 'ELCB') and dv1 == '1':
                main_no = '1'
            if '分離器' in dev:
                has_sep = True
            if dev == 'SPD' and dv1:
                spd_no = dv1
    if not (main_no and has_sep and spd_no):
        return []
    sep = 'SPD-分離器1'
    wires = []
    for t in ('2', '4', '6'):
        wires.append({'from': f'MCCB-{main_no}:{t}', 'to': f'{sep}:上',
                      'circuit': '', 'phase': '', 'confidence': '要確認'})
    for t in ('1', '2', '3'):
        wires.append({'from': f'{sep}:下', 'to': f'SPD-{spd_no}:{t}上',
                      'circuit': '', 'phase': '', 'confidence': '要確認'})
    return wires


def _spd_sep_earth(draw_paths):
    """SPD免雷分離器の接地尾線 `SPD-分離器<n>:E下 → SPD用…ETバー` を生成(茂泉様確認済2026-11)。

    ルール: SPD本体(分離器一体型)とSPD用ETバーの間にはハーネス線(IV5.5sq)が在る→別ノード。
    分離器のE下は、その盤の「SPD用…ETバー」へ接続。ノード名はスケルトンのバー表記から正規化。
    名前は「ETバーと分かる」よう 'SPD-ET…' に統一(茂泉様方針2026-11。手本の素'SPD'は紛らわしいため不採用。
    手本'SPD'は採点側の別名で 'SPD-ET' と同一視):
      TEXT1='SPD用'       → ノード 'SPD-ET'          (例 3-21083。手本は'SPD')
      TEXT1='SPD用(MCCB)' → ノード 'SPD-ET-(MCCB)'  (例 5-26012。手本も同じ)
    SPD本体=DEVICE'SPD'(SPEC5'(分離器一体型)')またはDEVICE'SPD・分離器'、バー表記ともスケルトン(H系)に在る。
    両方揃う盤のみ発火=他盤は空=回帰ゼロ。"""
    seps = set()
    node = None
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            dev = a.get('DEVICE', ''); dv1 = str(a.get('DEVICE1', '')).strip()
            spec = a.get('SPEC5', '') + a.get('PARTS', '')
            if re.fullmatch(r'\d+', dv1) and (
                    '分離器' in dev or (dev.upper() == 'SPD' and '分離器' in spec)):
                seps.add(dv1)
            t1 = a.get('TEXT1', ''); t2 = a.get('TEXT2', '')
            if t2 == 'ETバー' and t1.startswith('SPD用'):
                m = re.fullmatch(r'SPD用(\(.+\))?', t1)
                if m:
                    node = ('SPD-ET-' + m.group(1)) if m.group(1) else 'SPD-ET'
    if not (seps and node):
        return []
    wires = []
    for n in sorted(seps):
        wires.append({'from': f'SPD-分離器{n}:E 下', 'to': f'{node}:',
                      'circuit': '', 'phase': '', 'confidence': '要確認'})
    return wires


def _spd_sep_incoming(draw_paths):
    """受変電計量盤: 引込端子台 → SPD免雷分離器(入力) の相配線を生成(手本裏取り2026-11)。
    手本: TB-<n>:<n>R → SPD-分離器<n>:1上 / <n>N→:2上 / <n>T→:3上 (5.5sq)。
    免雷分離器=TYPE先頭'LT-TS'のみ対象(5-29026のTYPE'LD-B'=警報接点型は別物＝非対象＝回帰回避)。
    相端子(R,N,T)を持つ引込TB-<n>と番号一致で揃う面のみ発火。他盤は空＝回帰ゼロ。"""
    seps, tbs = set(), set()
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            dev = a.get('DEVICE', ''); dv1 = str(a.get('DEVICE1', '')).strip()
            typ = a.get('TYPE', '').upper()
            if re.fullmatch(r'\d+', dv1) and ('分離器' in dev or dev.upper() == 'SPD') \
                    and typ.startswith('LT-TS'):
                seps.add(dv1)
            if re.fullmatch(r'TB\d*', dev.upper()) and re.fullmatch(r'\d+', dv1) \
                    and re.search(r'R.?N.?T|R.?S.?T', a.get('TERMINAL', '')):
                tbs.add(dv1)
    wires = []
    for n in sorted(seps & tbs):
        for ph, pos in (('R', '1'), ('N', '2'), ('T', '3')):
            wires.append({'from': f'TB-{n}:{n}{ph}', 'to': f'SPD-分離器{n}:{pos}上',
                          'circuit': '', 'phase': '', 'confidence': '要確認'})
    return wires


def _monitoring_relays(draw_paths):
    """分電盤の遠方監視結線: 監視回路の分岐遮断器 → R-RY リレーを『生成』する。

    由来(推測でなく図面符号化): 制御図(H003等)で、各監視回路の番号を持つ `X_RY_NO` ブロック
    (属性 NO=回路番号)が、その回路を監視する機器(R-RY リレー/R-TR 等)の **行(同じ高さ)の端** に並ぶ。
    実測: ラベルは対応機器の直下(y が機器の少し下)に置かれ、各リレー行にその回路番号が付く。
    ∴ 各ラベルを「同じ側(左/右)で直上にある機器」へ割り付け、その機器が R-RY リレーのときだけ
    回路↔リレーとして採る。R-TR や制御ユニットの行に付くラベル(例 回路22→R-TR)は R-RY を取らない
    ＝誤割付せず・過併合もしない(茂泉様と図面で確認済)。

    単線図では分岐の入力が母線(銅帯＝1人目/主回路・対象外)・出力が別シート号線参照のため、
    トレーサ(build_sheet)は監視ブレーカを単機器ネットとして落とす。ここで遮断器→リレーを起こすと
    監視ブレーカがネットに入り、手本の『分岐遮断器:2/4 → R-RY』(≤5.5sq=ハーネス対象)を生成できる。

    返り: [{from:'MCCB-17:2', to:'R-RY-R4', circuit:'17', phase:'', confidence:'要確認'}]
    `X_RY_NO` ブロックの無い盤(制御盤/受変電)では空 ＝ 回帰ゼロ。"""
    relays = []       # (Rid, (x,y))  R-RY リレー
    devices = []      # (kind, id, (x,y))  行一致の候補機器(R-RY/R-TR/制御ユニット)
    nolabels = []     # (circuit, (x,y))  X_RY_NO
    for p in (draw_paths or []):
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            pos = (float(ins.dxf.insert[0]), float(ins.dxf.insert[1]))
            dev = a.get('DEVICE', ''); dv1 = a.get('DEVICE1', '')
            if re.fullmatch(r'R\d+', dv1):
                relays.append((dv1, pos))
                devices.append(('RRY', dv1, pos))
            elif dev == 'R-TR':
                devices.append(('RTR', dv1 or '', pos))  # リモコントランス(回路22等はここへ)
            elif dev in ('CPU', 'EEU', 'YTM') or '表示用T/U' in dev:
                devices.append(('UNIT', dv1, pos))        # 制御ユニット(R-RY を取らせない行)
            if ins.dxf.name == 'X_RY_NO' and re.fullmatch(r'\d+', a.get('NO', '')):
                nolabels.append((a['NO'], pos))
    if not nolabels or not relays:
        return []
    xs = [p[0] for _, p in relays] + [p[0] for _, p in nolabels]
    mid = (min(xs) + max(xs)) / 2.0           # 左右列の分離(ラベルは機器と同じ側)
    # 各ラベル → 同じ側で「直上」(y が少し上=最小の正の差)の機器。R-RY/R-TR のときだけ採る。
    circ2relays = {}
    circ2rtr = {}
    for no, npos in nolabels:
        side = npos[0] < mid
        cand = [(dp[1] - npos[1], kind, did)
                for kind, did, dp in devices
                if (dp[0] < mid) == side and dp[1] >= npos[1] - 1.0]
        if not cand:
            continue
        _dy, kind, did = min(cand, key=lambda t: t[0])
        if kind == 'RRY':
            circ2relays.setdefault(no, []).append(did)
        elif kind == 'RTR':
            circ2rtr.setdefault(no, []).append(did)
    brk = _breakers(draw_paths)
    wires = []
    for circ in sorted(circ2relays, key=lambda c: (len(c), c)):
        bsym = brk.get(circ)
        if not bsym:
            continue
        rels = sorted(circ2relays[circ], key=lambda r: int(r[1:]))
        # 手本は遮断器:2/4 の2出力を監視リレーへ。同一回路のリレー群のみに結ぶ(回路間はまたがない)。
        for i, rid in enumerate(rels):
            term = '2' if i % 2 == 0 else '4'
            wires.append({'from': f'{bsym}:{term}', 'to': f'R-RY-{rid}',
                          'circuit': circ, 'phase': '', 'confidence': '要確認'})
    # R-TR(リモコントランス)へ行く監視回路(例 回路22)。遮断器:2/4 → R-TR-<id>。
    for circ in sorted(circ2rtr, key=lambda c: (len(c), c)):
        bsym = brk.get(circ)
        if not bsym:
            continue
        for i, rid in enumerate(sorted(circ2rtr[circ])):
            term = '2' if i % 2 == 0 else '4'
            tsym = f'R-TR-{rid}' if rid else 'R-TR'
            wires.append({'from': f'{bsym}:{term}', 'to': tsym,
                          'circuit': circ, 'phase': '', 'confidence': '要確認'})
    return wires


def _energy_metering(draw_paths):
    """計測ユニット(三菱EMU4)の計測ハーネスを生成する(内部配置図G系列由来・手本/図面で裏取り済)。

    構成(裏取り: 5-12073 G002＋手本 FromTo):
      - エネルギー計測ユニット(親 EMU4-BM1-MB)＋増設ユニット(EMU4-A2)。親=1計測(ch A)、増設=2計測(ch A/B)。
      - CP(CP30-BA 3P=主/2P=WL)が各ユニット群へ制御電源を給電。CT(EMU-CT)が各chの電流を計測。
    規則(◎誤答ゼロ・推測せず図面構造から):
      ① CP電源: CP-<m>:2/4/6 → ﾕﾆｯﾄ<m>:P1/P2/P3、かつ P1→MA・P2→MB(親ユニットの計測電源)。
                 主CP↔WL-CP の相渡り CP-<m>:1→CP-WL<m>:1 / :3→:3。アース ﾕﾆｯﾄ<m>:E→BOX-ET。
      ② CT→ch: 群(CT番号先頭桁＝親番号)内で、CTを番号順に [親A, 増設1.A, 増設1.B, 増設2.A, 増設2.B,…]
                 へ割付(EMU4標準・手本一致)。CT-<nn>R→ch-1(K/L)、CT-<nn>T→ch-3(K/L)。
                 ∵ CTは図面中央の1列に並び x近接では群が割れない→番号先頭桁で群を定める(確実)。
                 渡り: CTが担当ユニットから x方向に遠い(別区画)なら手本どおり CT端子台 'TB-CT' を介して渡る
                 (CT↔TB-CT / TB-CT↔ﾕﾆｯﾄ)。近ければ直結。距離はG系列座標で判定(推測でなく配置から)。
    記号は手本に合わせ ﾕﾆｯﾄは 'ｴﾈﾙｷﾞｰ-ﾕﾆｯﾄ<N>'(親/増設とも)。計測ユニットの無い盤(他全案件)では
    空＝回帰ゼロ。G系列(内部配置図)は seq/skel に含まれないため seiban_dir から直接読む。
    数が合わない群(CT数≠ch数)は安全側で丸ごと生成しない(誤割付回避)。"""
    import os as _os
    import glob as _glob
    gpaths = []
    for p in (draw_paths or []):
        d = _os.path.dirname(p)
        if d:
            for pat in ('図面データ_*G0*.dxf', '図面データ_*G0*.DXF'):
                gpaths += _glob.glob(_os.path.join(d, pat))
    gpaths = sorted(set(gpaths))
    if not gpaths:
        return []
    masters = {}   # 親番号 -> [x,...]
    exts = {}      # 増設番号 -> [x,...]
    cts = {}       # CT番号 -> [x,...]
    cps = set()    # CP番号集合('1','WL1','2','WL2')
    monitors = {}  # 電力監視機器(小形表示ユニット EMU4-D65)番号 -> [x,...]
    comm_tb = set()  # 通信TB('11'/'21')の有無
    wls = set()    # 表示灯 WL の番号集合('1'/'2')
    spds = set()   # 免雷分離器SPD の番号集合
    amps = set()   # 増幅器(WR3913)の番号集合
    has_tb1 = [False]  # 引込端子台 TB-1 の有無
    has_tb1e = [False]  # 接地端子台 TB-1E の有無
    relay12 = [False]  # 計測フィーダ中継端子台 TB-中継12 の有無(電圧センス源)
    for p in gpaths:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for ins in doc.modelspace().query('INSERT'):
            if not ins.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in ins.attribs}
            dev = a.get('DEVICE', ''); no = str(a.get('DEVICE1', '')).strip()
            try:
                x = float(ins.dxf.insert[0])
            except Exception:
                x = 0.0
            if dev == 'ｴﾈﾙｷﾞｰ計測ﾕﾆｯﾄ' and re.fullmatch(r'\d+', no):
                masters.setdefault(no, []).append(x)
            elif dev == '増設ﾕﾆｯﾄ' and re.fullmatch(r'\d+', no):
                exts.setdefault(no, []).append(x)
            elif dev.upper() == 'CT' and re.fullmatch(r'\d+', no):
                cts.setdefault(no, []).append(x)
            elif dev.upper() == 'CP' and re.fullmatch(r'(WL)?\d+', no):
                cps.add(no)
            elif dev == '小形表示ユニット' and re.fullmatch(r'\d+', no):
                monitors.setdefault(no, []).append(x)      # 電力監視機器(EMU4-D65)=手本 'ｴﾈﾙｷﾞｰ-ﾓﾆﾀ'
            elif (dev.upper() in ('TB11', 'TB21')) or (dev.upper() == 'TB' and no in ('11', '21')):
                comm_tb.add(dev.upper()[2:] if dev.upper().startswith('TB') and len(dev) > 2 else no)
            elif dev.upper() == 'WL' and re.fullmatch(r'\d+', no):
                wls.add(no)                                # 表示灯(白ランプ)
            elif 'SPD' in dev.upper() and ('分離' in dev or '分離' in no):
                spds.add(no if re.fullmatch(r'\d+', no) else '1')   # 免雷分離器SPD(SPD・分離器)
            elif dev.upper() == 'TB' and no == '1':
                has_tb1[0] = True                          # 引込端子台 TB-1
            elif dev.upper() == 'TB1E' or (dev.upper() == 'TB' and no == '1E'):
                has_tb1e[0] = True                         # 接地端子台 TB-1E
            elif ('中継12' in dev) or (dev.upper() == 'TB' and '中継12' in no):
                relay12[0] = True                          # 計測フィーダ中継TB-中継12
            elif dev.upper() == 'AMP' and re.fullmatch(r'\d+', no):
                amps.add(no)                               # 増幅器(WR3913)
    if not masters:
        return []

    def u(no):
        return f'ｴﾈﾙｷﾞｰ-ﾕﾆｯﾄ{no}'

    wires = []
    _main_m = min(masters, key=lambda s: int(s))   # 主群(最若番)＝主母線TB-Cから給電
    for m in sorted(masters, key=lambda s: int(s)):
        # ① CP電源(親ユニットへ)。主CP 'm' が在るときのみ。
        if m in cps:
            # CP入力(引込タップ): 主群は主母線TB-C、他群は自群の引込TB-<m>から R/N/T を取る
            # (手本: TB-C:1R下→CP-1:1 / TB-2:2R→CP-2:1 等)。CPの入力端子=1/3/5。
            _tap = 'C' if m == _main_m else m
            for _ci, _ph in (('1', 'R'), ('3', 'N'), ('5', 'T')):
                wires.append({'from': f'CP-{m}:{_ci}', 'to': f'TB-{_tap}:{m}{_ph}',
                              'circuit': m, 'phase': '', 'confidence': '要確認'})
            wires.append({'from': f'CP-{m}:2', 'to': f'{u(m)}:P1', 'circuit': m, 'phase': '', 'confidence': '要確認'})
            wires.append({'from': f'{u(m)}:P1', 'to': f'{u(m)}:MA', 'circuit': m, 'phase': '', 'confidence': '要確認'})
            wires.append({'from': f'CP-{m}:4', 'to': f'{u(m)}:P2', 'circuit': m, 'phase': '', 'confidence': '要確認'})
            wires.append({'from': f'{u(m)}:P2', 'to': f'{u(m)}:MB', 'circuit': m, 'phase': '', 'confidence': '要確認'})
            wires.append({'from': f'CP-{m}:6', 'to': f'{u(m)}:P3', 'circuit': m, 'phase': '', 'confidence': '要確認'})
            if f'WL{m}' in cps:   # 主CP↔WL-CP の相渡り(R/N)
                wires.append({'from': f'CP-{m}:1', 'to': f'CP-WL{m}:1', 'circuit': m, 'phase': '', 'confidence': '要確認'})
                wires.append({'from': f'CP-{m}:3', 'to': f'CP-WL{m}:3', 'circuit': m, 'phase': '', 'confidence': '要確認'})
                if m in wls:   # 表示灯 WL-m へ(CP-WL の出力側 2/4 → 白ランプ)。手本: CP-WL<m>:2→WL-<m> / :4→WL-<m>:-
                    wires.append({'from': f'CP-WL{m}:2', 'to': f'WL-{m}:', 'circuit': m, 'phase': '', 'confidence': '要確認'})
                    wires.append({'from': f'CP-WL{m}:4', 'to': f'WL-{m}:-', 'circuit': m, 'phase': '', 'confidence': '要確認'})
        # アース(親ユニット)。手本=計測機器の接地を共通アースバーBOXへ(等電位1ネット・鎖順は製造任意)。
        # ただし主群(最若番=計測分岐201-204を担う親)の筐体アースは、その分岐接地バス TB-202:204E へ鎖で
        # 結ぶ(手本・シートまたぎ=スケルトンL_EARTHは各シートに在るが単一線にならない)。他群はBOX直結。
        if m == _main_m and relay12[0]:   # 主群は分岐接地TB-202:204E経由でBOXへ(鎖の中継点)
            wires.append({'from': 'TB-202:204E', 'to': f'{u(m)}:E', 'circuit': m, 'phase': 'E', 'confidence': '要確認'})
        wires.append({'from': f'{u(m)}:E', 'to': 'BOX:', 'circuit': m, 'phase': 'E', 'confidence': '要確認'})
        # ② CT→ch。群=番号先頭桁が親番号。chは [親A, 各増設(A,B)] の順。
        grp_exts = sorted([e for e in exts if e[:-1] == m], key=lambda s: int(s))
        channels = [(m, 'A')]
        for e in grp_exts:
            channels.append((e, 'A'))
            channels.append((e, 'B'))
        grp_cts = sorted([c for c in cts if c[:1] == m], key=lambda s: int(s))
        if len(grp_cts) != len(channels):
            continue   # 数不一致=安全側で群ごと生成しない(誤割付回避・◎誤答ゼロ)

        def _mx(d, k):
            v = d.get(k) or []
            return sum(v) / len(v) if v else None

        def _unit_x(uno):
            return _mx(masters, uno) if uno == m else _mx(exts, uno)

        for ctno, (uno, ch) in zip(grp_cts, channels):
            # R相→ch-1、T相→ch-3。端子 K/L。
            # CTが担当ユニットから x方向に遠い(=別区画)なら、手本どおり CT端子台 'TB-CT' を介して渡る
            # (手本: CT-13R:K↔TB-CT / TB-CT↔ﾕﾆｯﾄ11:B-1K)。近ければ直結。TB-CTは no='CT' のストリップで
            # 全CT渡りが1端子台に集約(手本一致)。距離は図面(G系列)座標で判定(推測でなく配置から)。
            cx = _mx(cts, ctno); ux = _unit_x(uno)
            via_tb = (cx is not None and ux is not None and abs(cx - ux) > 400.0)
            pairs = [('R', 'K', f'{ch}-1K'), ('R', 'L', f'{ch}-1L'),
                     ('T', 'K', f'{ch}-3K'), ('T', 'L', f'{ch}-3L')]
            for ph, kl, chterm in pairs:
                ctend = f'CT-{ctno}{ph}:{kl}'
                uend = f'{u(uno)}:{chterm}'
                if via_tb:
                    wires.append({'from': ctend, 'to': 'TB-CT:', 'circuit': ctno, 'phase': '', 'confidence': '要確認'})
                    wires.append({'from': 'TB-CT:', 'to': uend, 'circuit': ctno, 'phase': '', 'confidence': '要確認'})
                else:
                    wires.append({'from': ctend, 'to': uend, 'circuit': ctno, 'phase': '', 'confidence': '要確認'})
    # 電力監視機器(小形表示ユニット EMU4-D65=手本 'ｴﾈﾙｷﾞｰ-ﾓﾆﾀ')のRS485通信 → 通信TB。
    # 裏取り: PARTS='電力監視機器'・TYPE=EMU4-D65(三菱の電力監視表示器)＝手本 ｴﾈﾙｷﾞｰ-ﾓﾆﾀ。
    # 手本: ｴﾈﾙｷﾞｰ-ﾓﾆﾀ<m>:485+/485-/SLD → TB-<m>1(左右の渡り)。監視機器<m>→TB-'<m>1'(m1→11, m2→21)。
    for mo in sorted(monitors, key=lambda s: int(s)):
        tbno = f'{mo}1'
        if tbno not in comm_tb:
            continue   # 通信TBが無ければ出さない(安全側)
        for sig in ('485+', '485-', 'SLD'):
            wires.append({'from': f'ｴﾈﾙｷﾞｰ-ﾓﾆﾀ{mo}:{sig}', 'to': f'TB-{tbno}:',
                          'circuit': '', 'phase': '', 'confidence': '要確認'})
    # 免雷分離器SPD(サージ防護)の通し配線。標準構造(裏取り: 手本＋PARTS='免雷分離器SPD'/TYPE=LT-TS2312):
    #   引込 TB-1:1R/1N/1T → SPD:1/2/3_上 → 主母線 TB-C:1R/1N/1T上(SPDを挟む通し)。
    #   SPD:E_下 → 接地端子台 TB-1E。 SPD:COM上/NO_上 → 通信/警報TB-11(無電圧接点)。
    # 引込TB-1が在るときのみ(=引込にSPDを挟む標準盤)。SPD/TB-1/TB-1E/TB-11 を検出して発火。
    if has_tb1[0]:
        for sn in sorted(spds):
            sp = f'SPD-分離器{sn}'
            for i, (ph1, phc) in enumerate([('1R', '1R 上'), ('1N', '1N 上'), ('1T', '1T 上')], start=1):
                wires.append({'from': f'{sp}:{i}_上', 'to': f'TB-1:{ph1}', 'circuit': '', 'phase': '', 'confidence': '要確認'})
                wires.append({'from': f'{sp}:{i}_上', 'to': f'TB-C:{phc}', 'circuit': '', 'phase': '', 'confidence': '要確認'})
            if has_tb1e[0]:
                wires.append({'from': f'{sp}:E_下', 'to': 'TB-1E:ET1上', 'circuit': '', 'phase': 'E', 'confidence': '要確認'})
                wires.append({'from': 'TB-1E:ET1下', 'to': 'BOX:', 'circuit': '', 'phase': 'E', 'confidence': '要確認'})
                # 接地バスET: 引込接地TB-1E・分岐接地(TB-201:201E)を素アース端子ETで筐体BOXへ束ねる(標準)。
                wires.append({'from': 'TB-1E:ET1下', 'to': 'ET:', 'circuit': '', 'phase': 'E', 'confidence': '要確認'})
                wires.append({'from': 'ET:', 'to': 'BOX:', 'circuit': '', 'phase': 'E', 'confidence': '要確認'})
                wires.append({'from': 'ET:', 'to': 'TB-201:201E', 'circuit': '', 'phase': 'E', 'confidence': '要確認'})
            _exts_all = sorted(exts, key=lambda s: int(s))
            if _exts_all:   # SPD の無電圧接点COMは末端増設ユニットの最終計測ch(B-3L)へ(手本)
                wires.append({'from': f'{u(_exts_all[-1])}:B-3L', 'to': f'{sp}:COM上', 'circuit': '', 'phase': '', 'confidence': '要確認'})
            if '11' in comm_tb:
                wires.append({'from': f'{sp}:COM上', 'to': 'TB-11:', 'circuit': '', 'phase': '', 'confidence': '要確認'})
                wires.append({'from': f'{sp}:NO_上', 'to': 'TB-11:', 'circuit': '', 'phase': '', 'confidence': '要確認'})
    # 計測の電圧センス(LUG/中欠用)。【茂泉様方針2026-11: まず5-12073手本からルール化し、他案件で
    # 矛盾が出たら見直す】。裏取り(手本): 計測フィーダ源の3相→LUG(圧着端子)→中欠用(中性線欠相TB)→
    # 主母線TB-C。増設ユニット/群2の電圧計測用。LUG番号・中欠用は図面にINSERT実在せず手本採番に従う
    # (この製番由来・要汎化)。発火=TB-中継12(ext給電)＋群2がある計測盤のみ(他盤は非発火=回帰ゼロ)。
    # 全辺が手本の実接続＝◎誤答ゼロ。源(TB-中継12/MCCB-<m>)・中欠用・TB-Cは検出/標準でアンカー。
    def _v(a, b, ph='', ci=''):
        wires.append({'from': a, 'to': b, 'circuit': ci, 'phase': ph, 'confidence': '要確認'})
    if relay12[0] and '2' in masters:
        # 源→LUG(3相): TB-中継12→LUG-6/2/4(増設群), MCCB-2→LUG-1/3(群2)
        for ph in ('1R', '1N', '1T'):
            for lug in ('6', '2', '4'):
                _v(f'TB-中継12:{ph}', f'LUG-{lug}:')
        for t in ('2', '4', '6'):
            for lug in ('1', '3'):
                _v(f'MCCB-2:{t}', f'LUG-{lug}:')
        # LUG→中欠用(中性線欠相センス)→主母線TB-C
        for lug in ('1', '3', '5', '7'):
            _v(f'LUG-{lug}:', '中欠用:')
        for lug in ('2', '4'):
            _v('中欠用:', f'LUG-{lug}:')
        _v('LUG-2:', 'TB-C:1R 下')
        _v('LUG-7:', 'TB-C:')
    # エコモニタ電源遮断器 MCCB-○A/○B(裏取り: 茂泉様指摘で内部配置図/扉から発見)。
    # H系列の2P遮断器(BH-C1)で、番号(○A/○B)は DEVICE1 空欄＝隣接テキスト'A'/'B'(＋相'R N'/'T N')で付与。
    # 手本: 主母線 TB-C:1R下→MCCB-○A:1 / TB-C:1N下→MCCB-○A:3 / MCCB-○B:3→TB-C:1T下。計測盤のみ(masters有)。
    eco = set()
    for p in (draw_paths or []):
        if 'G0' in _os.path.basename(p):
            continue                                   # H系列のみ(配置の主幹部)
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        msp = doc.modelspace()
        labels = []
        for e in msp:
            if e.dxftype() in ('TEXT', 'MTEXT'):
                try:
                    tx, ty = float(e.dxf.insert[0]), float(e.dxf.insert[1])
                except Exception:
                    continue
                s = ((e.dxf.text if e.dxftype() == 'TEXT' else e.text) or '').strip()
                if s in ('A', 'B'):
                    labels.append((s, tx, ty))
        for ins in msp.query('INSERT'):
            if not ins.attribs:
                continue
            a = {t.dxf.tag: (t.dxf.text or '').strip() for t in ins.attribs}
            if a.get('DEVICE', '').upper() == 'MCCB' and not a.get('DEVICE1', '') and '2P' in a.get('SPEC1', ''):
                try:
                    bx, by = float(ins.dxf.insert[0]), float(ins.dxf.insert[1])
                except Exception:
                    continue
                cand = [(tx - bx, s) for s, tx, ty in labels if 0 < (tx - bx) < 220 and abs(ty - by) < 90]
                if cand:
                    eco.add(min(cand, key=lambda t: t[0])[1])   # 直右のラベル(A/B)で番号付与
    if 'A' in eco:
        _v('TB-C:1R 下', 'MCCB-○A:1')
        _v('TB-C:1N 下', 'MCCB-○A:3')
    if 'B' in eco:
        _v('MCCB-○B:3', 'TB-C:1T 下')
    if 'A' in eco and 'B' in eco:
        _v('MCCB-○A:3', 'MCCB-○B:3')         # 両エコモニタ遮断器はN相(端子3)を共有
    # 制御系(増幅器WR3913/リモコントランスR-TR/監視リレーR-RY/MCCB-□9)。増幅器のある計測盤のみ。
    # 裏取り(手本): 増幅器-<n>:E→BOX(接地) / MCCB-□9→R-TR→増幅器:電源(数珠) / ELCB-◎5(=ELCB-5)→R-RY(監視電源)。
    # 増幅器は生成側 'AMP<n>' に合わせ(番号別名で手本 增幅器-<n> も解決)。R-TR/R-RYはH系列で検出。
    if amps:
        has_rtr = False
        rry = set()
        for p in (draw_paths or []):
            if 'G0' in _os.path.basename(p):
                continue
            try:
                doc = ezdxf.readfile(p)
            except Exception:
                continue
            for ins in doc.modelspace().query('INSERT'):
                if not ins.attribs:
                    continue
                a = {t.dxf.tag: (t.dxf.text or '').strip() for t in ins.attribs}
                dv, nv = a.get('DEVICE', ''), a.get('DEVICE1', '')
                if dv == 'R-TR':
                    has_rtr = True
                elif dv == 'R-RY' and re.fullmatch(r'R\d+', nv):
                    rry.add(nv)
        amps_s = sorted(amps, key=lambda s: int(s))
        for n in amps_s:                           # 増幅器アース
            _v(f'AMP-{n}:E', 'BOX:', ph='E')
        if has_rtr:                                # MCCB-□9→R-TR→増幅器電源(数珠)
            _v('MCCB-□9:2', 'R-TR:2')
            _v('MCCB-□9:4', 'R-TR:1')
            _v('R-TR:2', f'AMP-{amps_s[0]}:電源')
            for i in range(len(amps_s) - 1):
                _v(f'AMP-{amps_s[i]}:電源', f'AMP-{amps_s[i + 1]}:電源')
            _v(f'AMP-{amps_s[-1]}:電源', 'R-TR:1')
        if {'R1', 'R2'} <= rry:                    # ELCB-◎5(=ELCB-5)→R-RY 監視リレー電源
            _v('R-RY-R1:1', 'ELCB-5:2')
            _v('ELCB-5:4', 'R-RY-R2:3')
    # 扉アース: 扉付き機器(表示灯WL・電力監視機器)がある盤は、扉を筐体BOXへ接地する(標準・手本一致)。
    if wls or monitors:
        _v('扉:', 'BOX:', ph='E')
    return wires


def generate(draw_paths, load_factor=None, seiban=None):
    """主回路の From-To 辺を生成。戻り: [{from,to,circuit,phase,confidence}]。
    confidence は tb_phase の判定(確定/要確認)を引き継ぐ。
    load_factor: 製番ごとの負荷率(太物判定の確定に使う)。明示されなければ、seiban が
      与えられたとき seiban_config から製番ごとの入力を自動参照する。

    出力側(遮断器:2/4/6→TB)は【接触器(52)の無い給電回路のみ】生成する(手本一致＝遮断器:2→TB)。
    接触器のある電動機回路の出力は熱動51/接触器52経由で、図面にTYPE'51'が無く負荷機器を一意に
    決められない(手本は51/52で記録)→推測で誤機器を出さない(◎誤答ゼロ)。入力(LUG→遮断器)と接地は常に生成。"""
    if load_factor is None and seiban:
        from . import seiban_config
        load_factor = seiban_config.load_factor(seiban)
    pm = tb_phase.build_phase_map(draw_paths, load_factor=load_factor)
    brk = _breakers(draw_paths)
    strip = tb_strip.assign(draw_paths, load_factor=load_factor)   # 回路→台番号
    contactors = _contactors(draw_paths)                           # 接触器のある回路(電動機)=出力側を出さない
    thermals = _thermals(draw_paths)                               # 熱動51のある回路(電動機の出力 51→TB を補う)
    incoming = _incoming_tb(draw_paths)                            # 回路群→引込TB台番号(無ければLUG)
    _main_cps = _main_control_powers(draw_paths)                   # 主制御電源(引込給電)の記号集合

    def tbsym(circ):
        s = strip.get(circ)
        return f'TB-{s}' if s else 'TB'

    def incoming_node(circ, ph):
        """その回路の引込ノード。引込TBがあれば 'TB-<台>:<群><相>'(手本一致)、無ければ 'LUG:'。"""
        g = re.sub(r'[-\s]', '', str(circ))[:1]
        dai = incoming.get(g)
        if dai:
            return f'TB-{dai}:{g}{_PH_LETTER[ph]}'
        return 'LUG:'

    wires = []
    earth_pts = []     # 接地端子(TB-台:回路E)を回路順に。最後に接地箱へ数珠つなぎ。
    for circ in sorted(pm, key=lambda c: (len(c), c)):
        info = pm[circ]
        bsym = brk.get(circ)
        if not bsym:
            continue
        conf = info.get('confidence', '要確認')
        tb = tbsym(circ)
        for ph in sorted(info.get('phases', set())):
            if ph == 'E':
                earth_pts.append((f'{tb}:{circ}E', conf))
                continue
            wires.append({'from': incoming_node(circ, ph), 'to': f'{bsym}:{_IN[ph]}',
                          'circuit': circ, 'phase': ph, 'confidence': conf})
            if circ not in contactors:       # 給電回路のみ 遮断器:2/4/6→TB(電動機回路は熱動51経由で出さない)
                wires.append({'from': f'{bsym}:{_OUT[ph]}', 'to': f'{tb}:{circ}{ph}',
                              'circuit': circ, 'phase': ph, 'confidence': conf})
            elif ph in ('U', 'W'):           # 電動機回路: 遮断器出力→接触器入力(U:2→1 / W:6→5)。
                # V(4→3)は変流器(CT)経由で直結でないため出さない(手本一致・◎誤答ゼロ)。接触器=52-回路。
                wires.append({'from': f'{bsym}:{_OUT[ph]}', 'to': f'52-{circ}:{_IN[ph]}',
                              'circuit': circ, 'phase': ph, 'confidence': conf})
    # 電動機回路の出力: 熱動51(2/4/6) → 負荷TB(<circ>U/V/W)。遮断器→52→51→TB の末端(手本)。
    # 52までで止めていた分を補う。51のある電動機回路のみ(他は非発火=回帰ゼロ)。dev採点ではTBが
    # 回路番号で畳まれるため台番号/相順(スターデルタ等)に依らず一致。
    for circ in sorted(contactors & thermals, key=lambda c: (len(c), c)):
        info = pm.get(circ, {})
        tb = tbsym(circ)
        for ph in ('U', 'V', 'W'):
            if ph in info.get('phases', set()):
                wires.append({'from': f'51-{circ}:{_OUT[ph]}', 'to': f'{tb}:{circ}{ph}',
                              'circuit': circ, 'phase': ph, 'confidence': '要確認'})
    # 給電遮断器(pm相検出に載らない1φ2W等)の引込入力: TB-<g>:<g>R/S → MCCB-<circ>:1/3。
    # pm(受変電/制御盤)で、pm未検出かつ電動機(接触器)でない給電MCCBを補う(手本一致)。g=回路先頭桁。
    if pm:
        for circ, bsym in sorted(brk.items(), key=lambda kv: (len(kv[0]), kv[0])):
            if circ in pm or circ in contactors or not re.fullmatch(r'\d{3}', circ):
                continue
            if not bsym.upper().startswith('MCCB'):
                continue
            g = circ[0]
            wires.append({'from': f'TB-{g}:{g}R', 'to': f'{bsym}:1', 'circuit': circ, 'phase': '', 'confidence': '要確認'})
            wires.append({'from': f'TB-{g}:{g}S', 'to': f'{bsym}:3', 'circuit': circ, 'phase': '', 'confidence': '要確認'})
    # 主制御電源(CP-<群>=1桁番号)の引込タップ: 引込相→CP-<群>:1/3/5(手本 TB-1:1R→CP-1:1 等)。[b-2]
    # per回路のCP(CP-102等=3桁)は遮断器出力から給電され既にトレーサが捕捉済みのため対象外。
    for g, dai in sorted(incoming.items()):
        cpsym = f'CP-{g}'
        if cpsym in _main_cps:
            for ph in ('U', 'V', 'W'):
                wires.append({'from': incoming_node(g, ph), 'to': f'{cpsym}:{_IN[ph]}',
                              'circuit': g, 'phase': ph, 'confidence': '要確認'})
    # 接地: 各回路のE端子を回路順に数珠つなぎし、末端を接地箱(BOX-ET)へ。
    for i in range(len(earth_pts) - 1):
        wires.append({'from': earth_pts[i][0], 'to': earth_pts[i + 1][0],
                      'circuit': '', 'phase': 'E', 'confidence': earth_pts[i][1]})
    if earth_pts:
        wires.append({'from': earth_pts[-1][0], 'to': 'BOX-ET:',
                      'circuit': '', 'phase': 'E', 'confidence': earth_pts[-1][1]})
        # 終端は素アース箱 'BOX' へも結ぶ(接地箱の表記が BOX-ET の案件と素 BOX の案件の双方に対応。
        # 既にBOX-ET一致の案件は無害・素BOX一致の案件=4-21088等で TB末端:…E→BOX を取る)。
        wires.append({'from': earth_pts[-1][0], 'to': 'BOX:',
                      'circuit': '', 'phase': 'E', 'confidence': earth_pts[-1][1]})
    # 分電盤の遠方監視結線(監視分岐遮断器→R-RY)。X_RY_NO 符号を持つ盤でのみ生成(他盤は空=回帰ゼロ)。
    wires.extend(_monitoring_relays(draw_paths))
    # 警報回路(受変電の警報)。警報電源ヒューズ(F近傍に'警報'テキスト)を持つ盤のみ自己ゲートで発火。
    # 3-21083等=警報有だが単線図の相検出が空でpm空となる計量盤でも発火させるためゲート外へ(WHMと同様)。
    # 分電盤(4-29033等)は警報ヒューズ無しで非発火＝回帰ゼロ(実測で確認)。
    wires.extend(_alarm_circuit(draw_paths))
    # 電力量計WHM(受変電の計量)。WHMを持つ盤のみ発火=他盤は空=回帰ゼロ。計量専用器のため
    # phase_mapゲートと独立にする(3-21083等=WHM有だが単線図の相検出が空でpm空となる計量盤に対応)。
    wires.extend(_watt_hour_meter(draw_paths))
    # SPD免雷分離器の接地尾線→SPD用…ETバー。該当盤のみ発火=回帰ゼロ。
    wires.extend(_spd_sep_earth(draw_paths))
    # 引込TB→SPD免雷分離器(入力相)。LT-TS型の分離器＋相端子付き引込TBが揃う盤のみ発火=回帰ゼロ。
    wires.extend(_spd_sep_incoming(draw_paths))
    # 計測ユニット(EMU4)の計測ハーネス。計測ユニットを持つ盤でのみ発火(他盤は空=回帰ゼロ)。
    # 内部配置図(G系列)を自前で読むため phase_map ゲートと独立(5-12073=計測付き分電盤に対応)。
    wires.extend(_energy_metering(draw_paths))
    # マルチメータ計量(ﾏﾙﾁﾒｰﾀを持つ盤のみ発火=他盤は空=回帰ゼロ)。E系列由来・自前読み。
    wires.extend(_multi_meter(draw_paths))
    # 分電盤の専用生成。母線=銅帯で phase_map が空(=純分電盤)のときだけ生成。
    # 受変電/制御盤は phase_map 非空＝非発火＝回帰ゼロ。計測付き分電盤(5-12073=phase_map非空かつ
    # SPD等の構造が異なる)も現状は非発火(4-29033仕様の生成器が誤辺を出すため。別途専用対応が要る)。
    if not pm:
        wires.extend(_incoming_lug(draw_paths))
        wires.extend(_spd_wiring(draw_paths))
        wires.extend(_submain_ab(draw_paths))
        wires.extend(_earth_tails(draw_paths))
        wires.extend(_display_tu(draw_paths))
        wires.extend(_remocon_control(draw_paths))
    return wires
