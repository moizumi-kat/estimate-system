# -*- coding: utf-8 -*-
"""
電気図面（BricsCAD/AutoCAD DXF）の「構造モデル」抽出。

このモジュールは決定論的（Vision不使用）に、DXFから
  - 端子ピン（機器記号:端子番号:座標）
  - 電線（結線ライン。ブロック内に隠れた電線幾何も展開して収集）
  - ネット（結線の連結成分。T字分岐も結合）
  - 号線（SENBANブロックの線番）
  - 機器の外形枠（PMT属性）
を取り出す。QCチェックと From-To 抽出、Vision結果の突合の共通土台。

設計メモ（実データ 5-29026 等で確認した事実）:
  * シーケンス図: 機器INSERTが TB属性（端子オフセット）＋TERMINAL属性（端子番号）を持つ。
  * 端子はブロック定義内の TEMPLATE レイヤ POINT でも表現される（TB属性が空の部品向け）。
  * スケルトン図: 機器記号に端子点が無く、主回路電線は _DENSEN1 等のブロック内に描かれる。
    → 端子レベルが取れないので「外形枠に電線端点が入れば接続」の機器レベルで補完する。
  * 単位は mm（$INSUNITS=4）。
"""
import re
import math
import collections
import ezdxf

# 電線が乗るレイヤ（直接／ブロック展開の双方で収集）
WIRE_LAYERS = {'L_CONTROL', 'L_CONTROL_H', 'L_MAIN', 'L_EARTH', 'DENSEN'}
# 端子/機器としてカウントしない付属・銘板系
SKIP_DEVICES = {'銘板', '端子ｶﾊﾞｰ', 'TB取付金具', 'ﾊﾝﾄﾞﾙ', '系統情報',
                'CABLE1', 'CABLE2', 'CH', 'CP', 'CPMAIN1', '補助接点ﾕﾆｯﾄ'}


def norm(s):
    """機器記号の正規化キー（比較・突合用）。"""
    return re.sub(r'[-_\s()（）]', '', str(s or '')).upper()


def _nums(s):
    return [float(x) for x in re.split(r'[,\s]+', str(s).strip()) if re.match(r'^-?\d+(\.\d+)?$', x)]


def _rot(dx, dy, deg):
    r = math.radians(deg or 0)
    c, s = math.cos(r), math.sin(r)
    return (dx * c - dy * s, dx * s + dy * c)


def pt_seg_dist(px, py, ax, ay, bx, by):
    """点と線分の距離、および線分内の正規化位置 t（0..1）。"""
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    tc = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + tc * dx), py - (ay + tc * dy)), t


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


class Terminal:
    __slots__ = ('device', 'name', 'x', 'y', 'source')

    def __init__(self, device, name, x, y, source):
        self.device = device      # 機器記号 例 "52-102"
        self.name = name          # 端子番号 例 "13"（不明は "?"）
        self.x = x
        self.y = y
        self.source = source      # 'TB' | 'TEMPLATE'

    def __repr__(self):
        return f"{self.device}:{self.name}@({self.x:.0f},{self.y:.0f})"


class Device:
    __slots__ = ('sym', 'x', 'y', 'box')

    def __init__(self, sym, x, y, box):
        self.sym = sym
        self.x = x
        self.y = y
        self.box = box            # (xmin,ymin,xmax,ymax) 外形枠


class DrawingModel:
    """1枚のDXFの構造モデル。"""
    def __init__(self, path):
        self.path = path
        self.doc = ezdxf.readfile(path)
        self.msp = self.doc.modelspace()
        self.terminals = self._terminals()
        self.devices = self._devices()
        self.segments = self._wire_segments()
        raw_sb = self._senban()
        # senban は (号線, x, y) の3要素で公開（後方互換）。kind は並行リストで保持。
        #   kind='ctrl' … SENBANブロックの『線番』（制御号線）
        #   kind='main' … SOU属性（スケルトンの相/主回路ラベル）
        self.senban = [(v, x, y) for v, x, y, _ in raw_sb]
        self.senban_kind = [k for *_, k in raw_sb]
        self.nets = self._build_nets()

    # ---- 端子ピン ----
    def _terminals(self):
        terms = []
        for e in self.msp:
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {at.dxf.tag: at.dxf.text for at in e.attribs}
            dev = a.get('DEVICE', '')
            if not dev or dev in SKIP_DEVICES:
                continue
            sym = f"{dev}-{a.get('DEVICE1','')}" if a.get('DEVICE1') else dev
            ins = e.dxf.insert
            deg = e.dxf.rotation or 0
            names = []
            for k in ('TERMINAL1', 'TERMINAL2', 'TERMINAL3', 'TERMINAL4', 'TERMINAL5', 'TERMINAL6'):
                if a.get(k):
                    names += [x.strip() for x in a[k].split(',')]
            added = []
            tb = a.get('TB', '')
            if tb.strip():
                nums = _nums(tb)
                pts = list(zip(nums[0::2], nums[1::2]))
                for i, (dx, dy) in enumerate(pts):
                    rx, ry = _rot(dx, dy, deg)
                    nm = names[i] if i < len(names) else (names[-1] if names else '?')
                    p = (round(ins.x + rx, 1), round(ins.y + ry, 1))
                    added.append(p)
                    terms.append(Terminal(sym, nm, p[0], p[1], 'TB'))
            # ブロック定義内 TEMPLATE レイヤの POINT（TB属性が無い部品の端子）
            blk = self.doc.blocks.get(e.dxf.name)
            if blk is not None:
                for be in blk:
                    if be.dxftype() == 'POINT' and be.dxf.layer == 'TEMPLATE':
                        dx, dy = be.dxf.location.x, be.dxf.location.y
                        rx, ry = _rot(dx, dy, deg)
                        p = (round(ins.x + rx, 1), round(ins.y + ry, 1))
                        if all(abs(p[0] - q[0]) + abs(p[1] - q[1]) > 3 for q in added):
                            terms.append(Terminal(sym, '?', p[0], p[1], 'TEMPLATE'))
        return terms

    # ---- 機器（外形枠）----
    def _devices(self):
        devs = []
        for e in self.msp:
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {at.dxf.tag: at.dxf.text for at in e.attribs}
            dev = a.get('DEVICE', '')
            if not dev or dev in SKIP_DEVICES:
                continue
            sym = f"{dev}-{a.get('DEVICE1','')}" if a.get('DEVICE1') else dev
            ins = e.dxf.insert
            deg = e.dxf.rotation or 0
            box = None
            try:
                l, t, r, b = [float(x) for x in a.get('PMT', '').split(',')[:4]]
                cs = [_rot(l, t, deg), _rot(r, t, deg), _rot(l, b, deg), _rot(r, b, deg)]
                xs = [ins.x + c[0] for c in cs]
                ys = [ins.y + c[1] for c in cs]
                box = (min(xs) - 10, min(ys) - 10, max(xs) + 10, max(ys) + 10)
            except Exception:
                box = (ins.x - 18, ins.y - 18, ins.x + 18, ins.y + 18)
            devs.append(Device(sym, ins.x, ins.y, box))
        return devs

    # ---- 電線（直接 + ブロック展開）----
    def _wire_segments(self):
        segs = []

        def add(e):
            if e.dxftype() == 'LINE':
                segs.append(((round(e.dxf.start.x, 1), round(e.dxf.start.y, 1)),
                             (round(e.dxf.end.x, 1), round(e.dxf.end.y, 1))))
            elif e.dxftype() == 'LWPOLYLINE':
                pts = [(round(x, 1), round(y, 1)) for x, y, *_ in e.get_points()]
                for p, q in zip(pts, pts[1:]):
                    segs.append((p, q))
        for e in self.msp:
            if e.dxftype() in ('LINE', 'LWPOLYLINE') and e.dxf.layer in WIRE_LAYERS:
                add(e)
            elif e.dxftype() == 'INSERT':
                try:
                    for ve in e.virtual_entities():
                        if ve.dxftype() in ('LINE', 'LWPOLYLINE') and ve.dxf.layer in WIRE_LAYERS:
                            add(ve)
                except Exception:
                    pass
        return [s for s in segs if s[0] != s[1]]

    def _senban(self):
        """号線(線番)ラベルを収集。図面により2系統あり、種別(kind)を付ける:
          - SENBANブロックの『線番』属性（制御=シーケンス図）→ kind='ctrl'
          - SOU1/SOU2/SOU3 属性（主回路=スケルトン図の相/号線ラベル）→ kind='main'
        戻り: [(号線, x, y, kind), ...]。同じ号線名でも 制御(ctrl) と 相(main) で意味が違う
        （例 "102S"）ため、ネット構築時に種別を分離して名前衝突による誤結合を防ぐ。
        """
        out = []
        for e in self.msp:
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {at.dxf.tag: at.dxf.text for at in e.attribs}
            if e.dxf.layer == 'SENBAN' and a.get('線番', '').strip():
                out.append((a['線番'].strip(), e.dxf.insert.x, e.dxf.insert.y, 'ctrl'))
            for j, k in enumerate(('SOU1', 'SOU2', 'SOU3')):
                v = a.get(k, '').strip()
                if v and v.lower() != 'sq':
                    out.append((v, e.dxf.insert.x, e.dxf.insert.y - j * 30, 'main'))
        return out

    # ---- ネット ----
    def _build_nets(self, tol=8):
        """号線(SENBAN/SOU)ラベルがあれば号線でネットを分離（母線経由の過剰結合を回避）。
        号線が無ければ幾何連結でフォールバック。"""
        if self.senban and self.segments:
            return self._build_nets_by_senban(tol)
        return self._build_nets_geometric(tol)

    def _build_nets_by_senban(self, tol=8):
        segs = self.segments
        # 号線ラベルに種別(kind)を添えて扱う。号線名→kind の対応も作る。
        sb = [(v, x, y, k) for (v, x, y), k in zip(self.senban, self.senban_kind)]
        id2kind = {}
        for v, x, y, k in sb:
            id2kind.setdefault(v, k)

        def midp(s):
            return ((s[0][0] + s[1][0]) / 2, (s[0][1] + s[1][1]) / 2)

        # 各セグメントを最寄り号線ラベルに割付け
        seg_sid = []
        for s in segs:
            mx, my = midp(s)
            seg_sid.append(min(sb, key=lambda z: (z[1] - mx) ** 2 + (z[2] - my) ** 2)[0])
        groups = collections.defaultdict(lambda: {'terminals': [], 'devices': set(), 'nodes': []})
        for i, (a, b) in enumerate(segs):
            groups[seg_sid[i]]['nodes'] += [a, b]
        # 端子 → 最寄りセグメントの号線
        for t in self.terminals:
            bi, bd = -1, 1e9
            for i, (a, b) in enumerate(segs):
                d, _ = pt_seg_dist(t.x, t.y, a[0], a[1], b[0], b[1])
                if d < bd:
                    bd, bi = d, i
            if bi >= 0 and bd <= tol * 3:
                g = groups[seg_sid[bi]]
                g['terminals'].append(t)
                g['devices'].add(t.device)
        # 端子が取れない機器（スケルトンのMCCB等）は外形枠で補完
        no_term = {t.device for t in self.terminals}
        for dv in self.devices:
            if dv.sym in no_term:
                continue
            x0, y0, x1, y1 = dv.box
            for i, (a, b) in enumerate(segs):
                if any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in (a, b)):
                    groups[seg_sid[i]]['devices'].add(dv.sym)
        nets = []
        for sid, g in groups.items():
            if len(g['devices']) < 2 and len(g['terminals']) < 2:
                continue
            nodes = g['nodes'] or [(t.x, t.y) for t in g['terminals']]
            cx = sum(n[0] for n in nodes) / len(nodes)
            cy = sum(n[1] for n in nodes) / len(nodes)
            nets.append({'id': sid, 'kind': id2kind.get(sid, 'ctrl'),
                         'terminals': g['terminals'], 'devices': sorted(g['devices']),
                         'nodes': nodes, 'center': (cx, cy)})
        return nets

    def _build_nets_geometric(self, tol=8):
        segs = self.segments
        if not segs:
            return []

        def qn(p):
            return (round(p[0] / tol) * tol, round(p[1] / tol) * tol)

        uf = UF()
        for a, b in segs:
            uf.union(qn(a), qn(b))
        eps = set()
        for a, b in segs:
            eps.add(a)
            eps.add(b)
        # T字分岐: 端点が別線分の途中に乗るなら結合
        for (px, py) in list(eps):
            for (ax, ay), (bx, by) in segs:
                if (px, py) in ((ax, ay), (bx, by)):
                    continue
                d, t = pt_seg_dist(px, py, ax, ay, bx, by)
                if d <= tol and 0.02 < t < 0.98:
                    uf.union(qn((px, py)), qn((ax, ay)))
        comp_nodes = collections.defaultdict(list)
        for a, b in segs:
            comp_nodes[uf.find(qn(a))].append(a)
            comp_nodes[uf.find(qn(b))].append(b)

        nets = []
        for root, nodes in comp_nodes.items():
            # 端子ピンをこのネットに割付（線分に近い端子）
            tset = []
            for tm in self.terminals:
                best = min((pt_seg_dist(tm.x, tm.y, a[0], a[1], b[0], b[1])[0] for a, b in segs), default=1e9)
                if best <= tol * 3 and uf.find(qn(_nearest_node(tm.x, tm.y, nodes))) == root:
                    tset.append(tm)
            # 機器を外形枠で割付
            dset = set()
            for dv in self.devices:
                x0, y0, x1, y1 = dv.box
                if any(x0 <= nx <= x1 and y0 <= ny <= y1 for nx, ny in nodes):
                    dset.add(dv.sym)
            if len(dset) < 2 and len(tset) < 2:
                continue
            cx = sum(n[0] for n in nodes) / len(nodes)
            cy = sum(n[1] for n in nodes) / len(nodes)
            sid = ''
            kind = 'ctrl'
            if self.senban:
                bi = min(range(len(self.senban)),
                         key=lambda i: (self.senban[i][1] - cx) ** 2 + (self.senban[i][2] - cy) ** 2)
                sid = self.senban[bi][0]
                kind = self.senban_kind[bi]
            nets.append({'id': sid, 'kind': kind, 'terminals': tset, 'devices': sorted(dset),
                         'nodes': nodes, 'center': (cx, cy)})
        return nets


def _nearest_node(x, y, nodes):
    return min(nodes, key=lambda n: (n[0] - x) ** 2 + (n[1] - y) ** 2)
