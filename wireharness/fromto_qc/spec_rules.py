# -*- coding: utf-8 -*-
"""製作仕様書(公共建築工事標準仕様書ベース)の属性ルールを決定的に引く。

現場回答(2026-10-19・手配図面5-21199の雲マーク=参照箇所)で確定:
太物/ハーネス作成時に参照する唯一の仕様書は、手配図面バンドル同梱の製作仕様書
(共通 000-P001/P002・制御盤 P005・分電盤 P006)＋社内確認表(Z1xx)。本モジュールは
その『標準ルール』を決定的に引く(標準表は全製番共通、社内確認表の特殊/要求行だけが製番差)。

  ・電線種別(回路→HIV/IV/EM-IE)   ・電線サイズ(基準定格電流→太さ / 制御1.25・計器二次2 下限)
  ・電線被覆色(JEM1122)            ・相色別(配線方式→R/S/T/N の色)
  ・圧着端子(主回路=裸丸形/制御=Y形) ・接地線サイズ(主幹定格→太さ)   ・CT要否

◎誤答ゼロ:
  ・太物サイズ等は候補(人の確認前提)。配線方式が不明な時は最多の『低圧 三相3線式』を既定。
  ・エコ電線(EM-IE)採否は社内確認表(spec_sheet)で製番ごとに切替。本表はEM-IE/HIVを別に持つ。
"""

# ---- 電線被覆色(JEM1122) ----
# 回路種別 → 被覆色。相のあるもの(主回路)は相色別(下記)で決まる。
CIRCUIT_COLOR = {
    'control': '黄',        # 交流/直流 制御回路
    'control_ac': '黄',
    'control_dc': '黄',
    'meter_secondary': '黄',  # 計器用変成器二次回路
    'ground': '緑',         # 接地回路
    'hv': '黒',             # 高圧回路
}

# ---- 相色別(導体の配置及び色別) ----
# (電圧種別, 配線方式) → {相: 色}。製作仕様書P001の表より。
# 低圧 三相3線式が最多(R赤/S白/T青)。三相4線/単相3線では N は白。
PHASE_COLOR = {
    ('低圧', '三相3線式'):      {'R': '赤', 'S': '白', 'T': '青'},
    ('低圧', '三相3線式400V系'): {'R': '赤', 'S': '黒', 'T': '青'},
    ('低圧', '三相3線式550V系'): {'R': '赤', 'S': '黒', 'T': '青'},
    ('低圧', '三相4線式'):      {'R': '赤', 'S': '黒', 'T': '青', 'N': '白'},
    ('低圧', '単相2線式'):      {'R': '赤', 'S': '黒'},          # S非接地側=黒(接地側=白)
    ('低圧', '単相3線式'):      {'R': '赤', 'N': '白', 'T': '黒'},
    ('高圧', '三相3線式'):      {'R': '赤', 'S': '白', 'T': '青'},
}
DEFAULT_WIRING = ('低圧', '三相3線式')


def phase_color(phase, voltage='低圧', wiring='三相3線式'):
    """相(R/S/T/N) → 被覆色。配線方式が不明なら低圧三相3線式。該当なしは空文字。"""
    p = str(phase or '').strip().upper()
    table = PHASE_COLOR.get((voltage, wiring)) or PHASE_COLOR[DEFAULT_WIRING]
    return table.get(p, '')


def circuit_color(kind):
    """回路種別 → 被覆色(非相)。control→黄 / ground→緑 / hv→黒。該当なしは空。"""
    return CIRCUIT_COLOR.get(kind, '')


# ---- 電線種別(回路→種別) ----
# 一般: 主回路=HIV・EM-IE / 制御=IV・EM-IE / 計器二次=IV・EM-IE。非常主回路=HIV。
WIRE_TYPE = {
    'main': ('HIV', 'EM-IE'),
    'control': ('IV', 'EM-IE'),
    'meter_secondary': ('IV', 'EM-IE'),
    'emergency_main': ('HIV',),
    'emergency_control': ('HIV',),
}


def wire_type(circuit, eco=False):
    """回路種別 → 電線種別。eco=True(社内確認表でエコ電線採用)なら EM-IE 系を優先。
    該当なしは None。"""
    opts = WIRE_TYPE.get(circuit)
    if not opts:
        return None
    if eco and 'EM-IE' in opts:
        return 'EM-IE'
    return opts[0]


# ---- 電線サイズ(基準定格電流→絶縁電線の太さ[sq]) ----
# 製作仕様書P005(制御盤)の表。基準周囲温度40℃。HIV・EM-IE列 と 特殊電線列。
# (しきい電流[A], HIV・EM-IE[sq], 特殊電線[sq])。電流がしきい値『以下』の最初の行を採る。
_SIZE_TABLE = [
    (15,  '2',   '2'),
    (20,  '3.5', '2'),
    (32,  '5.5', '3.5'),
    (40,  '8',   '5.5'),
    (63,  '14',  '8'),
    (75,  '22',  '14'),
    (100, '38',  '22'),
    (125, '38',  '38'),
    (150, '60',  '38'),
    (175, '60',  '60'),
    (225, '100', '60'),
    (300, '150', '100'),
    (350, '200', '150'),
    (400, '250', '150'),       # 又は 100×2本 / 150は100×2本
    (500, '325', '200'),       # 又は 150×2本 / 200は100×2本
    (600, '200×2', '250'),     # 200×2本 / 250又は150×2本
    (630, '200×2', '325'),     # 200×2本 / 325又は150×2本
]
CONTROL_MIN_SIZE = '1.25'        # 制御回路の電線サイズ下限
METER_SECONDARY_MIN_SIZE = '2'   # 計器用変成器二次回路の電線サイズ下限


def wire_size(amps, special=False):
    """基準定格電流[A] → 絶縁電線の太さ[sq](候補)。special=True で特殊電線列。
    400A超など複数本記載は代表値を返す。範囲外(630A超)は None。"""
    try:
        a = float(amps)
    except (TypeError, ValueError):
        return None
    for thresh, std, spc in _SIZE_TABLE:
        if a <= thresh:
            return spc if special else std
    return None


# ---- 接地線サイズ(主幹定格電流→接地線太さ[sq]) ----
_GROUND_TABLE = [
    (100,  'M6',  '5.5'),
    (200,  'M6',  '14'),
    (400,  'M10', '22'),
    (600,  'M10', '38'),
    (1000, 'M10', '60'),
    (1600, 'M12', '100'),
    (2500, 'M12', '150'),
]


def ground_wire_size(amps):
    """主幹定格電流[A] → 接地線太さ[sq](候補)。範囲外は None。"""
    try:
        a = float(amps)
    except (TypeError, ValueError):
        return None
    for thresh, _screw, size in _GROUND_TABLE:
        if a <= thresh:
            return size
    return None


def ground_screw(amps):
    """主幹定格電流[A] → 接地端子のねじ径(最小)。範囲外は None。"""
    try:
        a = float(amps)
    except (TypeError, ValueError):
        return None
    for thresh, screw, _size in _GROUND_TABLE:
        if a <= thresh:
            return screw
    return None


# ---- 圧着端子 ----
def crimp_terminal(circuit, panel='制御盤'):
    """回路種別 → 圧着端子の形。主回路/計器二次=裸丸形(JIS C2805)、制御=裸差込Y形。
    ただし配電盤の制御回路は裸丸形を使用(特記)。"""
    if circuit == 'control':
        return '裸丸形' if panel == '配電盤' else 'Y形'
    return '裸丸形'


# ---- CT(変流器)要否 ----
def needs_ct(amps=None, voltage=None):
    """電流計が20A超、または400V回路なら CT(変流器)を設ける。"""
    try:
        if amps is not None and float(amps) > 20:
            return True
    except (TypeError, ValueError):
        pass
    try:
        if voltage is not None and float(voltage) >= 400:
            return True
    except (TypeError, ValueError):
        pass
    return False
