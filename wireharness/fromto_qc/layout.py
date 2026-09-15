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
        self.panels = self._panels()            # [(x0,x1), ...] 盤のX範囲

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
