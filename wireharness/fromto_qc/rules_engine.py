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
MAIN_DEVICES = {'MCCB', 'ELCB', 'ELB', 'LBS', 'ACB'}
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
        rows.append({'gousen': gid, 'size': size, 'wtype': wtype,
                     'from': a, 'to': b,
                     'door_from': _is_door(a[0]), 'door_to': _is_door(b[0]),
                     'marker': ''})
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


def generate(logical_nets):
    """論理From-To（{号線: {'endpoints':[(dev,no,term)], 'kind','size'}}）→ 物理ハーネス行。
    R-A/B/C/F を適用。R-D(LUG/WAGO)・R-E(ダクト方向)は内部配置図が要るため未適用。
    """
    rows = []
    for gid, net in logical_nets.items():
        eps = net.get('endpoints', [])
        rows += expand_net(gid, eps, net.get('kind', 'ctrl'), net.get('size', ''))
    rows = apply_terminal_relay(rows)
    return rows


def summary(rows):
    mk = collections.Counter(r['marker'] for r in rows if r['marker'])
    doors = sum(1 for r in rows if r['door_from'] or r['door_to'])
    return {'wires': len(rows), 'markers': dict(mk), 'door_wires': doors}
