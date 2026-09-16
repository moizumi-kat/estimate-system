# -*- coding: utf-8 -*-
"""内部配置図の解析（製造ルール R-D/R-E の基盤）。

内部配置図から次を取り出す:
  - 機器の物理座標（盤面配置）
  - ダクト(DCT)の位置（水平/垂直チャンネル）
  - 盤の分割（列盤なら複数盤。機器X座標のクラスタで推定）
提供:
  - device_pos(name)     … 機器の座標
  - panel_of(name)       … 盤インデックス（列盤の盤間判定用）
  - duct_direction(name, term_y=None) … 上/下（機器/端子とダクトの位置関係）
  - missing(devices)     … From-To に在るが配置図に無い機器（＝検図で設計へ）

R-D 盤間判定: from/to が別盤 → 盤間。R-E ダクト方向: 端子の上下で上/下。
どちらも配置図が前提。配置図に機器が無ければ検図(defect)として報告。
"""
import ezdxf
import collections
from .geometry import norm

SKIP = {'', 'TB取付金具', '端子ｶﾊﾞｰ', '端子カバー'}
# 扉付け機器（盤本体の内部配置図に無いのが正常＝検図の不足検出から除外）
DOOR_BASE = {'WL', 'RL', 'GL', 'YL', 'OL', 'BZ', 'BS', 'PB', 'PBS', 'AM', 'VM', 'COS', 'SL',
             '入', '切', '3'}


def _base(sym):
    return ''.join(ch for ch in sym.split('-')[0] if not ch.isdigit())


class Layout:
    def __init__(self, path):
        self.doc = ezdxf.readfile(path)
        self.msp = self.doc.modelspace()
        self.devices = self._devices()          # norm(name) -> (x,y)
        self.hducts = self._hducts()            # 水平ダクトの Y 中心（昇順）
        self.vducts = self._vducts()            # 垂直ダクトの X 中心（昇順）
        self.panels = self._panels()            # [(x0,x1), ...] 盤のX範囲
        # 図面枠の区分記号（作業者が盤上で機器を探すための位置グリッド）
        self.rows, self.cols = self._frame()    # rows: [(letter,y),..] cols: [(num,x),..]

    def _devices(self):
        out = {}
        for e in self.msp:
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
            dev = a.get('DEVICE', '').strip()
            if not dev or dev in SKIP:
                continue
            sym = dev + '-' + a.get('DEVICE1', '') if a.get('DEVICE1', '').strip() else dev
            out[norm(sym)] = (e.dxf.insert.x, e.dxf.insert.y)
        return out

    def _hducts(self):
        ys = []
        for e in self.msp:
            if e.dxftype() == 'LINE' and e.dxf.layer == 'DCT':
                if abs(e.dxf.start.y - e.dxf.end.y) < 5:
                    ys.append((e.dxf.start.y + e.dxf.end.y) / 2)
            elif e.dxftype() == 'LWPOLYLINE' and e.dxf.layer == 'DCT':
                pts = [(x, y) for x, y, *_ in e.get_points()]
                for p, q in zip(pts, pts[1:]):
                    if abs(p[1] - q[1]) < 5:
                        ys.append((p[1] + q[1]) / 2)
        # 近接Yを1本のダクトにまとめる（上端/下端の2線→中心1本）
        ys.sort()
        merged = []
        for y in ys:
            if merged and abs(y - merged[-1]) < 60:
                merged[-1] = (merged[-1] + y) / 2
            else:
                merged.append(y)
        return merged

    def _vducts(self):
        xs = []
        for e in self.msp:
            if e.dxftype() == 'LINE' and e.dxf.layer == 'DCT':
                if abs(e.dxf.start.x - e.dxf.end.x) < 5:
                    xs.append((e.dxf.start.x + e.dxf.end.x) / 2)
            elif e.dxftype() == 'LWPOLYLINE' and e.dxf.layer == 'DCT':
                pts = [(x, y) for x, y, *_ in e.get_points()]
                for p, q in zip(pts, pts[1:]):
                    if abs(p[0] - q[0]) < 5:
                        xs.append((p[0] + q[0]) / 2)
        xs.sort()
        merged = []
        for x in xs:
            if merged and abs(x - merged[-1]) < 60:
                merged[-1] = (merged[-1] + x) / 2
            else:
                merged.append(x)
        return merged

    def _panels(self):
        """機器X座標のヒストグラムから盤の切れ目を推定（列盤対応）。
        大きなX方向の空白を盤境界とみなす。単盤なら1つ。"""
        xs = sorted(p[0] for p in self.devices.values())
        if not xs:
            return [(-1e9, 1e9)]
        panels = []
        start = xs[0]
        prev = xs[0]
        GAP = 1500          # この幅以上の空白を盤境界とみなす
        for x in xs[1:]:
            if x - prev > GAP:
                panels.append((start - 100, prev + 100))
                start = x
            prev = x
        panels.append((start - 100, prev + 100))
        return panels

    def _frame(self):
        """図面枠の区分記号を読み取る。
        行記号(A,B,C..) … 左右の枠縁に等間隔で並ぶ単独アルファベット → (letter, y)
        列記号(1,2,3..) … 上下の枠縁に等間隔で並ぶ単独数字         → (num, x)
        記号はブロック内に描かれることが多いのでINSERTも展開して走査する。
        枠が見つからなければ空リスト（zone_of等は None を返す）。
        """
        import re
        try:
            emin = self.doc.header.get('$EXTMIN')
            emax = self.doc.header.get('$EXTMAX')
            x0, y0 = emin[0], emin[1]
            x1, y1 = emax[0], emax[1]
        except Exception:
            xs = [p[0] for p in self.devices.values()] or [0]
            ys = [p[1] for p in self.devices.values()] or [0]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        w = max(x1 - x0, 1.0)
        h = max(y1 - y0, 1.0)
        mx = 0.06 * w          # 縁とみなす帯幅（左右6%）
        my = 0.06 * h          # 縁とみなす帯幅（上下6%）

        def texts():
            for e in self.msp:
                yield e
                if e.dxftype() == 'INSERT':
                    try:
                        for ve in e.virtual_entities():
                            yield ve
                    except Exception:
                        pass

        def txt(e):
            t = e.dxftype()
            try:
                if t == 'TEXT':
                    return (e.dxf.text or '').strip(), e.dxf.insert.x, e.dxf.insert.y
                if t == 'MTEXT':
                    s = re.sub(r'\\[A-Za-z][^;]*;', '', (e.text or '')).replace('{', '').replace('}', '').strip()
                    return s, e.dxf.insert.x, e.dxf.insert.y
            except Exception:
                pass
            return None, None, None

        letters = {}                       # letter -> list of y（左右縁）
        bottom, top = {}, {}               # num -> list of x（上下それぞれの縁）
        for e in texts():
            s, x, y = txt(e)
            if s is None:
                continue
            if re.fullmatch(r'[A-Za-zＡ-Ｚ]', s):
                if x <= x0 + mx or x >= x1 - mx:      # 左右の縁 → 行記号
                    letters.setdefault(s.upper(), []).append(y)
            elif re.fullmatch(r'[0-9]{1,2}', s):
                if y <= y0 + my:                      # 下の縁 → 列記号
                    bottom.setdefault(s, []).append(x)
                elif y >= y1 - my:                    # 上の縁 → 列記号
                    top.setdefault(s, []).append(x)

        def med(v):
            v = sorted(v)
            return v[len(v) // 2]

        rows = sorted(((L, med(ys)) for L, ys in letters.items()), key=lambda r: -r[1])   # 上(大y)→下

        def build_cols(edge):
            # 数字→縁上のx中央値。数字の昇順にxが単調増加する列だけ採用（表内の数字＝同一xを排除）。
            pts = sorted(((int(n), n, med(xs)) for n, xs in edge.items()), key=lambda r: r[0])
            keep = []
            for _, n, x in pts:
                if keep and x <= keep[-1][1] + 1.0:   # 前の列より右に無い＝枠でない
                    continue
                keep.append((n, x))
            return keep

        cb, ct = build_cols(bottom), build_cols(top)
        cols = cb if len(cb) >= len(ct) else ct       # 列がより多く並ぶ縁を採用
        return rows, cols

    def zone_at(self, x, y):
        """座標(x,y)が入る行記号(A,B,..)を返す。枠が無ければ None。"""
        if not self.rows:
            return None
        # 各行の中心yに最も近い記号
        best = None
        bd = 1e18
        for L, cy in self.rows:
            d = abs(cy - y)
            if d < bd:
                bd = d
                best = L
        return best

    def col_at(self, x, y):
        """座標(x,y)が入る列記号(1,2,..)を返す。枠が無ければ None。"""
        if not self.cols:
            return None
        best = None
        bd = 1e18
        for n, cx in self.cols:
            d = abs(cx - x)
            if d < bd:
                bd = d
                best = n
        return best

    def cell_at(self, x, y):
        """座標(x,y)のグリッドセル 'F-7' を返す。行のみ有れば 'F'。無ければ ''。"""
        r = self.zone_at(x, y)
        c = self.col_at(x, y)
        if r and c:
            return f"{r}-{c}"
        return r or ''

    def zone_of(self, name):
        """機器の行記号(A,B,..)。配置図に無ければ None。"""
        p = self.device_pos(name)
        return self.zone_at(*p) if p else None

    def cell_of(self, name):
        """機器のグリッドセル 'F-7'（作業者が盤上で探す位置）。無ければ ''。"""
        p = self.device_pos(name)
        return self.cell_at(*p) if p else ''

    # ---- 提供メソッド ----
    def device_pos(self, name):
        return self.devices.get(norm(name))

    def panel_of(self, name):
        p = self.device_pos(name)
        if p is None:
            return None
        for i, (x0, x1) in enumerate(self.panels):
            if x0 <= p[0] <= x1:
                return i
        return None

    def duct_direction(self, name, term_y=None):
        """機器（または端子Y）に対し、最寄りダクトが上か下か → '上'/'下'。
        端子Yがあれば端子で、無ければ機器中心で判定。ダクトが無ければ ''。"""
        p = self.device_pos(name)
        if p is None or not self.hducts:
            return ''
        y = term_y if term_y is not None else p[1]
        above = [d for d in self.hducts if d >= y]
        below = [d for d in self.hducts if d < y]
        da = min(above) - y if above else 1e9
        db = y - max(below) if below else 1e9
        return '上' if da <= db else '下'

    def is_cross_panel(self, dev_a, dev_b):
        """2機器が別盤か（盤間）。どちらか不明なら None。"""
        pa, pb = self.panel_of(dev_a), self.panel_of(dev_b)
        if pa is None or pb is None:
            return None
        return pa != pb

    def missing(self, devices):
        """From-To に在るが配置図(盤本体)に無い機器（検図で設計へ回す対象）。
        扉付け機器は盤本体の配置図に無いのが正常なので除外する。"""
        out = set()
        for d in devices:
            base = _base(d).upper()
            if base in DOOR_BASE or _base(d) in DOOR_BASE:
                continue
            if norm(d) not in self.devices:
                out.add(d)
        return sorted(out)
