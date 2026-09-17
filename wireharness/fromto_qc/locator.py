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

# -DCT図面のロケータ表記: relay の DEVICE1 = '{レール番号}-{文字}'（例 '2-B'）
_DCT_RE = re.compile(r'^(\d+)-([A-Z]{1,2})$')


def letters_from_dct(dct_paths):
    """-DCT図面（ソフトがロケータ文字を採番・表示した後）から letter→relay を読む。
    relay の DEVICE1='{rail}-{letter}' を解釈。これはソフトの採番結果そのもの。
    戻り: {norm(relay機器名): {'letter':L, 'rail':rail, 'x':x, 'y':y}}。
    """
    import ezdxf
    out = {}
    for p in dct_paths or []:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for e in doc.modelspace():
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
            dv = a.get('DEVICE', '').strip()
            m = _DCT_RE.match(a.get('DEVICE1', '').strip())
            if dv and m:
                out[norm(dv)] = {'letter': m.group(2), 'rail': m.group(1),
                                 'x': e.dxf.insert.x, 'y': e.dxf.insert.y}
    return out

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


def assign(layout, include=None, rail_tol=40):
    """Layout の制御リレー/タイマに、ソフトと同じ規則でロケータ文字を採番（位置ベース）。
    規則（-DCT実データで検証・一致率99%）:
      レール = X近接クラスタ（縦レール）。レールは左→右。
      レール内は 上→下（Y降順）に A,B,C…。文字はレール毎にリセット。
    include: 追加で対象にする基底記号集合（任意）。
    戻り: (letter_of, device_of, order)  letter_of={norm(機器名):'A'}。
    """
    include = {s.upper() for s in (include or set())}
    cands = []
    for name, (x, y) in layout.devices.items():
        base = Master.split(name)[0].upper()
        if is_control_relay(base) or base in include:
            cands.append((name, x, y))
    # レール分割: X昇順で近接をまとめる
    cands.sort(key=lambda c: c[1])
    rails, cur, lastx = [], [], None
    for name, x, y in cands:
        if lastx is None or abs(x - lastx) <= rail_tol:
            cur.append((name, x, y))
        else:
            rails.append(cur)
            cur = [(name, x, y)]
        lastx = x
    if cur:
        rails.append(cur)
    letter_of, device_of, order = {}, {}, []
    for rail in rails:
        for i, (name, x, y) in enumerate(sorted(rail, key=lambda r: -r[2])):  # 上→下
            L = _letter(i)
            letter_of[norm(name)] = L
            device_of.setdefault(L, name)
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


def _digits(s):
    m = re.search(r'(\d+)$', str(s))
    return m.group(1) if m else ''


def alias_map(layout=None, dct_paths=None):
    """制御リレーの実機器名 → ロケータ別名集合 {文字, 文字+回路番号}。
    人手ハーネスはリレーを「文字」や「文字+回路」で参照するため、照合・出力で
    実機器名と等価に扱えるようにする。-DCTがあればソフト採番、無ければ位置ベース。
    戻り: {norm(実機器名): set(別名)}。
    """
    loc = Locator(layout=layout, dct_paths=dct_paths)
    out = {}
    for dev, L in loc.letter_of.items():
        Ln = norm(L)
        out[dev] = {Ln, norm(Ln + _digits(dev))}
    return out


class Locator:
    """ロケータ文字の参照。
    -DCT図面があれば ソフトの採番を完全一致で再現（DEVICE1='rail-letter'を読む）。
    無ければ 位置ベース自動採番（フォールバック）。
    """

    def __init__(self, layout=None, dct_paths=None, include=None):
        self.source = 'position'
        self.letter_of, self.device_of, self.rail_of = {}, {}, {}
        # 1) -DCT があればソフトの採番を直接採用（完全一致）
        if dct_paths:
            dct = letters_from_dct(dct_paths)
            if dct:
                self.source = 'dct'
                for dev, info in dct.items():
                    self.letter_of[dev] = info['letter']
                    self.rail_of[dev] = info['rail']
                    self.device_of.setdefault(info['letter'], dev)
        # 2) 無ければ位置ベース（フォールバック）
        if self.source == 'position' and layout is not None:
            self.letter_of, self.device_of, self.order = assign(layout, include)

    def letter(self, device):
        """機器名→ロケータ文字（制御リレー/タイマのみ）。無ければ ''。"""
        return self.letter_of.get(norm(device), '')

    def rail(self, device):
        return self.rail_of.get(norm(device), '')

    def device(self, letter):
        return self.device_of.get(str(letter).upper(), '')
