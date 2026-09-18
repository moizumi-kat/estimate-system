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
# 公式作図仕様(OFFICIAL_ANALYSIS_SPEC): 制御=L_CONTROL / 電源母線=L_MAIN / 外部制御=L_OUTSIDE。
# L_OUTSIDE(端子台の先＝手元操作盤・外部機器)は端子台_LU*を介して内部と繋がる。
# スケルトン(主回路)は L_EARTH/DENSEN も使うため後方互換で残す。
WIRE_LAYERS = {'L_CONTROL', 'L_CONTROL_H', 'L_MAIN', 'L_OUTSIDE', 'L_EARTH', 'DENSEN'}
# 公式仕様のしきい値（作図単位=mm想定）
POINT_TOL = 5          # 同一節点判定
TERMINAL_TOL = 15      # 端子台_LU*の中継結合
SENBAN_SEG_MAXDIST = 250   # 線番→配線の割当
FOOT_MARGIN = 0        # 機器外形枠の距離マージン（人の「端点と部品の距離」判断に対応。調整可）
MUTUAL_NEAREST = False  # 相互最近傍で結線判定。全製番で効果なし＋一部を悪化(成分の誤併合)のため既定OFF。実装は保持
MUTUAL_MAXDIST = 120   # 相互最近傍を認める最大距離（これ以上離れていれば結線しない）
# 端子台DEVICE1が「未入力」とみなす値（設計が番号を入れていないプレースホルダ）
UNFILLED_DEVICE1 = {'*', '＊', '?', '？', '-', 'ー', '−', '‐'}


def _mk_sym(dev, d1):
    """機器記号を生成。DEVICE1が未入力('*'等)なら番号なし（総称化）。"""
    d1 = (d1 or '').strip()
    return f"{dev}-{d1}" if d1 and d1 not in UNFILLED_DEVICE1 else dev
# 端子/機器としてカウントしない付属・銘板系
SKIP_DEVICES = {'銘板', '端子ｶﾊﾞｰ', 'TB取付金具', 'ﾊﾝﾄﾞﾙ', '系統情報',
                'CABLE', 'CABLE1', 'CABLE2', 'CH', 'CP', 'CPMAIN1', '補助接点ﾕﾆｯﾄ',
                'Lug', 'NP'}


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
        self.termblocks = self._term_blocks()
        self.wire_layers = self._decide_wire_layers()
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
            sym = _mk_sym(dev, a.get('DEVICE1',''))
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
            sym = _mk_sym(dev, a.get('DEVICE1',''))
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

    # ---- 端子台（_LU, DEVICE空欄も接続先として拾う）----
    def _term_blocks(self):
        """端子台ブロック(_LU*)を接続先として収集。DEVICE属性が空の端子台も拾う。
        ラベルあり(_terminals/_devicesで既に拾える)は除外。
        戻り: [(sym, x, y, box), ...]  sym は 'TB-<番号>'（DEVICE1があれば）または 'TB'。
        """
        out = []
        for e in self.msp:
            if e.dxftype() != 'INSERT' or not e.dxf.name.startswith('_LU'):
                continue
            a = {at.dxf.tag: at.dxf.text for at in e.attribs} if e.attribs else {}
            dev = (a.get('DEVICE', '') or '').strip()
            if dev and dev not in SKIP_DEVICES:
                continue                      # ラベル済み端子台は既存処理で拾う
            d1 = (a.get('DEVICE1', '') or '').strip()
            # 空欄・プレースホルダ('*'等)は「番号未入力」とみなし総称TB（→ P1で設計へ提案）
            sym = f"TB-{d1}" if d1 and d1 not in UNFILLED_DEVICE1 else 'TB'
            ins = e.dxf.insert
            deg = e.dxf.rotation or 0
            try:
                l, t, r, b = [float(x) for x in a.get('PMT', '').split(',')[:4]]
                cs = [_rot(l, t, deg), _rot(r, t, deg), _rot(l, b, deg), _rot(r, b, deg)]
                xs = [ins.x + c[0] for c in cs]
                ys = [ins.y + c[1] for c in cs]
                box = (min(xs) - 5, min(ys) - 5, max(xs) + 5, max(ys) + 5)
            except Exception:
                box = (ins.x - 15, ins.y - 15, ins.x + 15, ins.y + 15)
            out.append((sym, ins.x, ins.y, box))
        return out

    def _decide_wire_layers(self):
        """このシートで結線に使う電線レイヤを決める。
        制御シート(L_CONTROL有)では L_MAIN は端子ストリップ/母線であり、別号線を橋渡し
        して号線を潰す（実データ 5-21071 で確認）。よって制御シートでは L_MAIN を除外する。
        スケルトン(L_CONTROL無)では主回路号線が L_MAIN に乗るため従来通り含める。"""
        has_ctrl = any(e.dxftype() in ('LINE', 'LWPOLYLINE')
                       and e.dxf.layer in ('L_CONTROL', 'L_CONTROL_H')
                       for e in self.msp)
        return (WIRE_LAYERS - {'L_MAIN'}) if has_ctrl else set(WIRE_LAYERS)

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
            if e.dxftype() in ('LINE', 'LWPOLYLINE') and e.dxf.layer in self.wire_layers:
                add(e)
            elif e.dxftype() == 'INSERT':
                try:
                    for ve in e.virtual_entities():
                        if ve.dxftype() in ('LINE', 'LWPOLYLINE') and ve.dxf.layer in self.wire_layers:
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

    def _junction_points(self):
        """公式仕様の接続機構ブロックの位置を返す [(x, y, tol), ...]。
          _crossPoint1 … T字接続マーカ（POINT_TOL）
          _LU*         … 端子台=中継。中心±TERMINAL_TOL の配線端点を全結合（内外橋渡し）
        """
        pts = []
        for e in self.msp:
            if e.dxftype() != 'INSERT':
                continue
            nm = (e.dxf.name or '').upper()
            ins = e.dxf.insert
            if nm == '_CROSSPOINT1':
                pts.append((ins.x, ins.y, POINT_TOL))
            elif nm.startswith('_LU'):
                pts.append((ins.x, ins.y, TERMINAL_TOL))
        return pts

    # ---- ネット ----
    def _build_nets(self, tol=8):
        """ネット構築。属性端子ピン＋電線連結を辿る方式を優先（過剰収集を回避）。
        号線ラベルはあるが電線が無い等の例外時のみ従来方式へフォールバック。"""
        if self.segments and self.senban:
            nets = self._build_nets_connectivity(tol)
            if nets:
                return nets
            return self._build_nets_by_senban(tol)
        if self.senban and self.segments:
            return self._build_nets_by_senban(tol)
        return self._build_nets_geometric(tol)

    def _build_nets_connectivity(self, tol=8):
        """部品属性を最大活用するネット構築。
        考え方: 属性端子ピン(TB+TERMINAL, 100%電線端点に一致)を起点に、電線の連結成分
        (union-find＋T字分岐)を作り、成分ごとに『実際に繋がる端子・機器』だけを集める。
        号線ラベルは“ラベル自身の位置”に最も近い電線端点の成分へ与える（最寄りセグメント割付
        の様に一つのラベルを遠方まで広げない）。同じ号線が複数成分に付く場合のみ連結
        （作図のスナップ隙間を号線で橋渡し）。
        """
        segs = self.segments

        def qn(p):
            return (round(p[0] / tol) * tol, round(p[1] / tol) * tol)

        uf = UF()
        for a, b in segs:
            uf.union(qn(a), qn(b))
        nodes_all = set()
        for a, b in segs:
            nodes_all.add(a)
            nodes_all.add(b)
        # T字分岐: 端点が別線分の途中に乗れば結合
        for (px, py) in list(nodes_all):
            for (ax, ay), (bx, by) in segs:
                if (px, py) in ((ax, ay), (bx, by)):
                    continue
                d, t = pt_seg_dist(px, py, ax, ay, bx, by)
                if d <= tol and 0.02 < t < 0.98:
                    uf.union(qn((px, py)), qn((ax, ay)))

        # 公式仕様の接続機構:
        #  ・接続点マーカ _crossPoint1 … マーカ位置 POINT_TOL 内を通る配線端点を結合(T字)
        #  ・端子台 _LU* … 中心 TERMINAL_TOL 内の配線端点を全て結合(内部L_CONTROL⇔外部L_OUTSIDE橋渡し)
        for cx, cy, ctol in self._junction_points():
            near = [p for p in nodes_all if abs(p[0] - cx) <= ctol and abs(p[1] - cy) <= ctol]
            for p in near[1:]:
                uf.union(qn(near[0]), qn(p))

        # 号線ラベル → ラベルに最も近い『電線セグメント(垂線)』の成分root（＋種別）
        # 【重要】端点最寄りではなくセグメント最寄り（仕様: SENBAN_SEG_MAXDIST）。
        # 線の途中に置かれた号線を、遠い別線の端点ではなく“乗っている線”へ正しく割り当てる。
        node_list = list(nodes_all)

        def _label_root(x, y):
            best_root = None
            bd = SENBAN_SEG_MAXDIST
            for a, b in segs:
                d, _t = pt_seg_dist(x, y, a[0], a[1], b[0], b[1])
                if d < bd:
                    bd = d
                    best_root = uf.find(qn(a))
            if best_root is None and node_list:
                best_root = uf.find(qn(_nearest_node(x, y, node_list)))
            return best_root

        label_root = {}    # root -> [(号線, kind), ...]
        for (v, x, y), k in zip(self.senban, self.senban_kind):
            r = _label_root(x, y)
            if r is not None:
                label_root.setdefault(r, []).append((v, k))

        # 同じ号線名を持つ成分同士を連結（スナップ隙間の橋渡し）
        id_first_root = {}
        for root, labs in list(label_root.items()):
            for (v, k) in labs:
                if v in id_first_root:
                    uf.union(root, id_first_root[v])
                else:
                    id_first_root[v] = root

        # 端子ピン → その端子が乗る電線端点の成分（一致=確定的に接続）
        comp_terms = collections.defaultdict(list)
        for tm in self.terminals:
            nn = _nearest_node(tm.x, tm.y, node_list) if node_list else None
            if nn is None:
                continue
            if abs(nn[0] - tm.x) + abs(nn[1] - tm.y) <= tol * 3:
                comp_terms[uf.find(qn(nn))].append(tm)

        # 端子を持たない機器(スケルトンのMCCB等) → 外形枠に電線端点が入る成分へ。
        # 人は「線の末端と部品の距離」で結線判断するため、枠を FOOT_MARGIN だけ広げて
        # 枠のわずか外にある電線端点も接続とみなす（取りこぼし=未結線Aの主因を回収）。
        M = FOOT_MARGIN
        no_term = {t.device for t in self.terminals}
        comp_footdev = collections.defaultdict(set)
        for dv in self.devices:
            if dv.sym in no_term:
                continue
            x0, y0, x1, y1 = dv.box
            for nd in node_list:
                if x0 - M <= nd[0] <= x1 + M and y0 - M <= nd[1] <= y1 + M:
                    comp_footdev[uf.find(qn(nd))].add(dv.sym)

        # 端子台(_LU, DEVICE空欄含む) → ボックスに電線端点が入る成分へ 'TB' を接続先付与。
        # これで内部L_CONTROLと外部L_OUTSIDEの境界にある端子台が接続先として数えられ、
        # 43-102 のような1機器ネットが {43-102, TB} になって形成される。
        for sym, tx, ty, (bx0, by0, bx1, by1) in getattr(self, 'termblocks', []):
            for nd in node_list:
                if bx0 - M <= nd[0] <= bx1 + M and by0 - M <= nd[1] <= by1 + M:
                    comp_footdev[uf.find(qn(nd))].add(sym)

        # 相互最近傍による結線（茂泉様提案）: 枠内包含で拾えない端点を、
        # 「線端の最近傍機器」と「機器の最近傍線端」が一致するペアだけ結線する。
        # 固定公差に頼らず誤結線を避ける。端子を持たない機器の外形枠に対して適用。
        if MUTUAL_NEAREST and node_list:
            def pt_box_dist(px, py, box):
                x0, y0, x1, y1 = box
                dx = max(x0 - px, 0, px - x1)
                dy = max(y0 - py, 0, py - y1)
                return (dx * dx + dy * dy) ** 0.5
            foot = [dv for dv in self.devices if dv.sym not in no_term]
            if foot:
                # 機器→最近傍線端
                dev_near = {}
                for dv in foot:
                    best = None
                    bd = MUTUAL_MAXDIST
                    for nd in node_list:
                        d = pt_box_dist(nd[0], nd[1], dv.box)
                        if d < bd:
                            bd = d
                            best = nd
                    if best is not None:
                        dev_near[dv.sym] = best
                # 線端→最近傍機器
                for nd in node_list:
                    best = None
                    bd = MUTUAL_MAXDIST
                    for dv in foot:
                        d = pt_box_dist(nd[0], nd[1], dv.box)
                        if d < bd:
                            bd = d
                            best = dv.sym
                    # 相互一致（機器の最近傍線端＝この線端）なら結線
                    if best is not None and dev_near.get(best) == nd:
                        comp_footdev[uf.find(qn(nd))].add(best)

        # 成分ごとの号線ラベル（連結後のrootで引き直す。連結でrootがずれるため）
        # ここも端点最寄りではなくセグメント最寄りで割当（_label_root と同一基準）。
        root_labels = collections.defaultdict(list)
        for (v, x, y), k in zip(self.senban, self.senban_kind):
            r = _label_root(x, y)
            if r is not None:
                root_labels[uf.find(r)].append((v, k))

        nets = []
        seen_roots = set(comp_terms) | set(comp_footdev) | set(root_labels)
        for root in seen_roots:
            terms = comp_terms.get(root, [])
            devs = {t.device for t in terms} | comp_footdev.get(root, set())
            labs = root_labels.get(root, [])
            if not labs:
                continue                       # 号線が付かない成分は出力しない
            # 号線id・種別＝この成分の最多ラベル
            cnt = collections.Counter(v for v, _ in labs)
            sid = cnt.most_common(1)[0][0]
            kind = dict((v, k) for v, k in labs).get(sid, 'ctrl')
            # 1機器の成分も残す（渡り/別シートで同一号線が1機器ずつ出る場合、
            # ここで捨てると harness.fuse の号線マージができない）。空だけ除外。
            if not devs and not terms:
                continue
            nodes = [(t.x, t.y) for t in terms] or [(0, 0)]
            nets.append({'id': sid, 'kind': kind, 'terminals': terms,
                         'devices': sorted(devs), 'nodes': nodes,
                         'center': (sum(n[0] for n in nodes) / len(nodes),
                                    sum(n[1] for n in nodes) / len(nodes))})
        return nets

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
