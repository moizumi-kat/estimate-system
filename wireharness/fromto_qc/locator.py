# -*- coding: utf-8 -*-
"""盤ロケータ文字の自動採番（製造側・位置ベース）。

茂泉様確定方針:
  ロケータ文字（A,B,C…）は、ソフトがハーネスシート作成時に内部配置図上の部品へ
  割り当てる。対象は「制御リレー・タイマ類」。人手の採番を再現するのではなく、
  盤面位置から一意・決定論で採番する（回路的に正しい＝作業者が盤上で探せる位置記号）。

採番規則:
  内部配置図の制御リレー/タイマを、盤面の読み順（上→下＝枠行A→H、同行内は左→右）で
  並べ、A,B,C… を順に割り当てる。列盤（複数盤）は盤ごとに左盤→右盤の順で通し。

対象部品（制御リレー・タイマ）:
  補助リレー(X/Y付: 86X,30X,TX,STX,BSX,ALX,52Y,30Y…)・タイマ(27T,TLR,T…)。
  除外: 遮断器/計器(AM,VM)/変成器(CT,PT,ZCT)/表示灯/端子台/ヒューズ/SPD。
"""
import re
from .geometry import norm
from .device_master import Master

# 明確に制御リレー/タイマでないもの（末尾Tだが計器/変成器 等）
NOT_RELAY = {'CT', 'PT', 'VT', 'ZCT', 'TB', 'TC', 'PMT', 'TR', 'T/U'}
# 明確に対象外の種別
EXCLUDE_BASE = {'MCCB', 'ELCB', 'ELB', 'CP', 'ACB', 'LBS', 'AM', 'VM',
                'WL', 'RL', 'GL', 'YL', 'OL', 'BZ', 'BS', 'PB', 'PBS', 'COS', 'SL',
                'TB', 'F', 'SPD', 'CABLE', 'BOX', 'CT', 'PT', 'ZCT'}


def is_control_relay(base):
    """基底記号が制御リレー/タイマか。"""
    b = base.upper()
    if not b or b in EXCLUDE_BASE or b in NOT_RELAY:
        return False
    if 'X' in b:                     # 補助リレー(86X,30X,TX,STX,BSX,ALX…)
        return True
    if 'Y' in b:                     # 補助リレー(52Y,30Y…)
        return True
    if b.endswith('T') and b not in NOT_RELAY:   # タイマ(27T,TLR…)
        return True
    return False


def assign(layout, include=None):
    """Layout から制御リレー/タイマを盤面位置順に A,B,C… 採番。
    include: 追加で対象にする基底記号集合（任意）。
    戻り: (letter_of, device_of, order)
      letter_of: {norm(機器名): 'A'} , device_of: {'A': 機器名}, order: [(letter, 機器名, x, y)]
    """
    include = {s.upper() for s in (include or set())}
    cands = []
    for name, (x, y) in layout.devices.items():
        base = Master.split(name)[0].upper()
        if is_control_relay(base) or base in include:
            cands.append((name, x, y))
    # 盤面読み順: 盤(左→右) → 行(上=大きいy→下) → 列(左=小さいx→右)
    def panel_idx(x):
        for i, (x0, x1) in enumerate(getattr(layout, 'panels', [(-1e9, 1e9)])):
            if x0 <= x <= x1:
                return i
        return 0
    # 行のまとまりを作るため y を枠行にスナップ（枠が無ければ y そのまま）
    rows = getattr(layout, 'rows', [])
    def row_key(y):
        if rows:
            # 最も近い枠行の中心 y（上ほど大きい）→ 昇順indexで上から
            best = min(range(len(rows)), key=lambda i: abs(rows[i][1] - y))
            return best                    # rows は上→下でindex増加
        return -y                          # 枠が無ければ y 降順（上から）
    cands.sort(key=lambda c: (panel_idx(c[1]), row_key(c[2]), c[1]))
    letter_of, device_of, order = {}, {}, []
    for i, (name, x, y) in enumerate(cands):
        L = _letter(i)
        letter_of[norm(name)] = L
        device_of[L] = name
        order.append((L, name, x, y))
    return letter_of, device_of, order


def _letter(i):
    """0→A, 25→Z, 26→AA …（26進のような連番）。"""
    s = ''
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(ord('A') + r) + s
    return s


class Locator:
    """Layout から採番したロケータの参照。"""

    def __init__(self, layout, include=None):
        self.letter_of, self.device_of, self.order = assign(layout, include)

    def letter(self, device):
        """機器名→ロケータ文字（制御リレー/タイマのみ）。無ければ ''。"""
        return self.letter_of.get(norm(device), '')

    def device(self, letter):
        return self.device_of.get(str(letter).upper(), '')
