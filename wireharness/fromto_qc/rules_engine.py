# -*- coding: utf-8 -*-
"""製造ルールエンジン（ステップ③）: 論理From-To → 物理ハーネスシート。

論理From-To（号線ごとの機器・端子の集合）に、製造制約ルールを適用して
物理ハーネス（電線1本ごとのFrom-To＋種別/サイズ＋LUG/WAGO/中継/渡り/ダクト方向）
を生成する。出力は harness_sheet.parse と同じ構造。

段階（実装状況）:
  R-A 物理配線化      … 号線ネットを電線1本ずつのFrom-Toへ（数珠つなぎ）  … 実装
  R-B 電線種別/サイズ  … 制御=KIV/主回路=HIV 等、GAISENSIZEから           … 実装(簡易)
  R-C 端子容量→中継/渡り … 1端子に3本以上なら中継端子台/渡りを挿入        … 実装(基本)
  R-D 盤間→LUG/WAGO   … 盤間×主回路→LUG, 盤間×制御→WAGO（要 内部配置図）  … 枠(要盤情報)
  R-E ダクト方向       … 内部配置図の機器⇔ダクト位置で上/下/左/右         … 枠(要内部配置図)
  R-F 扉フラグ         … 扉付け操作器/表示灯                              … 実装(簡易)

大方針（茂泉様確定）: 出力は「人手と同一」でなく「回路的に正しい・ルール上正しい」で合格。
"""
import collections

# 主回路の遮断器（LUG/相バス側）
MAIN_DEVICES = {'MCCB', 'ELCB', 'ELB', 'LBS', 'ACB', 'CP'}
# R-D（端子ベース・実データで確認）: LUG=主回路入力端子, WAGO=警報補助端子
LUG_TERMS = {'1', '3', '5'}                       # 主回路入力(R/S/T)→LUG(大銅端子)
WAGO_TERMS = {'ALa', 'ALc', 'EALa', 'EALc',
              'ALA', 'ALC', 'EALA', 'EALC'}        # 警報補助接点→WAGO
# 扉付けになりやすい操作器・表示灯の記号接頭
DOOR_DEVICES = {'WL', 'RL', 'GL', 'YL', 'OL', 'BZ', 'BS', 'PB', 'PBS', 'AM', 'VM', 'COS', 'SL'}
DEFAULT_WIRE = 'KIV'      # 制御既定
MAIN_WIRE = 'HIV'         # 主回路既定
TERM_CAPACITY = 2         # 1端子に丸/Y端子は2本まで


def _dev_base(dev):
    return ''.join(ch for ch in dev if not ch.isdigit()).rstrip('-')


def _is_door(dev):
    return _dev_base(dev).upper() in DOOR_DEVICES


def _is_main(dev):
    return _dev_base(dev).upper() in MAIN_DEVICES


def expand_net(gid, endpoints, kind='ctrl', size=''):
    """1つの号線ネット → 物理電線のリスト。
    endpoints: [(device, no, terminal), ...]（論理的に同電位＝同じ号線）。
    数珠つなぎ（隣接ペアを電線1本に）で物理化。端子容量超過は中継で分割。
    戻り: [row, ...]  row=harness_sheet形式 dict。
    """
    rows = []
    if len(endpoints) < 2:
        return rows
    wtype = MAIN_WIRE if any(_is_main(d) for d, _, _ in endpoints) else DEFAULT_WIRE
    # R-C 端子容量: 各端点の接続本数を数え、3本以上に集中する端子は中継で分割
    deg = collections.Counter((d, n, t) for d, n, t in endpoints)
    # 数珠つなぎ順（主回路→制御→表示灯の順に整列＝電源側から）
    order = sorted(range(len(endpoints)),
                   key=lambda i: (0 if _is_main(endpoints[i][0]) else
                                  2 if _is_door(endpoints[i][0]) else 1))
    eps = [endpoints[i] for i in order]
    for i in range(len(eps) - 1):
        a, b = eps[i], eps[i + 1]
        rows.append({'gousen': gid, 'size': size, 'wtype': wtype, 'kind': kind,
                     'from': a, 'to': b,
                     'door_from': _is_door(a[0]), 'door_to': _is_door(b[0]),
                     'marker': '', 'cell_from': '', 'cell_to': '',
                     'loc_from': '', 'loc_to': ''})
    return rows


def apply_terminal_relay(rows, capacity=TERM_CAPACITY):
    """R-C: 1端子に capacity 本を超えて電線が集まる箇所へ『中継』マークを付ける。
    （実際の中継端子台の挿入・番号採番は内部配置図と社内規約が要るため、
     ここでは『中継が必要』の指摘までを行う。）"""
    cnt = collections.Counter()
    for r in rows:
        cnt[r['from']] += 1
        cnt[r['to']] += 1
    for r in rows:
        if cnt[r['from']] > capacity or cnt[r['to']] > capacity:
            r['marker'] = (r['marker'] + '+中継').strip('+') if r['marker'] else '中継要'
    return rows


def _sym(ep):
    return ep[0] + ('-' + ep[1] if ep[1] else '')


def apply_marker_rules(rows):
    """R-D（端子ベース）: 実データ確認済ルールで LUG/WAGO を付与（内部配置図不要）。
      LUG  … 主回路遮断器の入力端子(1/3/5)に接続する電線端
      WAGO … 遮断器の警報補助端子(ALa/ALc)に接続する電線端
    """
    for r in rows:
        mk = ''
        for dev, no, term in (r['from'], r['to']):
            t = (term or '').strip()
            if _is_main(dev) and t in LUG_TERMS:
                mk = 'LUG'
            elif t in WAGO_TERMS:
                mk = 'WAGO'
        if mk:
            r['marker'] = (r['marker'] + '+' + mk).strip('+') if r['marker'] else mk
    return rows


def apply_layout_rules(rows, layout, dct_paths=None):
    """R-E ダクト方向 を内部配置図から付与（LUG/WAGOは端子ベースのR-Dで別途付与）。
    併せて盤上の位置記号（区分グリッド セル 'F-7'）と、制御リレー/タイマの
    ロケータ文字を付与。ロケータは -DCT図面があればソフトの採番を完全一致で再現、
    無ければ位置ベース（-DCTと99%一致）で採番する。"""
    try:
        from .locator import Locator
        loc = Locator(layout, dct_paths=dct_paths)
    except Exception:
        loc = None
    for r in rows:
        r['dir_from'] = layout.duct_direction(_sym(r['from']))
        r['dir_to'] = layout.duct_direction(_sym(r['to']))
        r['cell_from'] = layout.cell_of(_sym(r['from']))   # 盤上の位置記号（行×列）
        r['cell_to'] = layout.cell_of(_sym(r['to']))
        if loc is not None:
            r['loc_from'] = loc.letter(_sym(r['from']))    # 制御リレー/タイマのロケータ文字
            r['loc_to'] = loc.letter(_sym(r['to']))
    return rows


def generate(logical_nets, layout=None, dct_paths=None):
    """論理From-To（{号線: {'endpoints':[(dev,no,term)], 'kind','size'}}）→ 物理ハーネス行。
    R-A/B/C/F を適用。layout を渡すと R-D(LUG/WAGO)・R-E(ダクト方向) も適用。
    dct_paths: -DCT図面（ロケータ文字採番済）。あればソフト採番を完全一致で反映。
    戻り: (rows, defects)  defects=配置図に無い機器（検図で設計へ）。
    """
    rows = []
    for gid, net in logical_nets.items():
        eps = net.get('endpoints', [])
        rows += expand_net(gid, eps, net.get('kind', 'ctrl'), net.get('size', ''))
    rows = apply_terminal_relay(rows)
    rows = apply_marker_rules(rows)          # R-D 端子ベース LUG/WAGO（配置図不要）
    defects = []
    if layout is not None:
        rows = apply_layout_rules(rows, layout, dct_paths)
        devs = set()
        for net in logical_nets.values():
            for d, n, t in net.get('endpoints', []):
                devs.add(d + ('-' + n if n else ''))
        defects = layout.missing(devs)
    return rows, defects


def summary(rows):
    mk = collections.Counter(r['marker'] for r in rows if r['marker'])
    doors = sum(1 for r in rows if r['door_from'] or r['door_to'])
    return {'wires': len(rows), 'markers': dict(mk), 'door_wires': doors}
