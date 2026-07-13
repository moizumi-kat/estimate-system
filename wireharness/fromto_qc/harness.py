# -*- coding: utf-8 -*-
"""全シート・全属性の決定論的融合によるハーネスデータ生成（Vision不使用）。

方針（ユーザ要望）:
  シーケンス＋スケルトン＋内部配置図の“全情報・全属性”を融合して、Vision無しで
  ハーネスデータ(From-To＋物理位置＋配線長＋電線サイズ)を最大限生成する。
  決定論で埋まらない難所だけを Vision に回すため、各号線に信頼度フラグを付ける。

各シートの役割（実データで確認）:
  - シーケンス図 … 制御の接続論理＋端子番号(TB/TERMINAL属性)＋号線(SENBAN)。
  - スケルトン図 … 主回路の接続＋相号線(SOU)＋端子台の端子名(TERMINAL=U,V,W,E)＋電線サイズ(GAISENSIZE)。
  - 内部配置図 … 盤面の物理座標＋ダクト(DCT)経路＋端子台構成。配線そのものは無い。

融合:
  1) 各シートを『属性端子ピン起点＋電線連結』でネット化（geometry の既定）。
  2) 号線idでシート横断マージ（同じ号線=同じ物理ネット）。
  3) crossref で機器に物理座標・端子高さzを付与、端子台は中継候補に。
  4) スケルトンTBから電線サイズ(GAISENSIZE)を号線/端子台に添付。
  5) routing で機器to機器＋配線長。DCT経路があれば測長を経路長に差し替え可能。
  6) 信頼度: 物理座標のある機器が2つ以上で連結が取れた号線=確定。取れない=Vision要。
"""
import re
import collections
import ezdxf
from .geometry import DrawingModel, norm
from . import crossref, routing, qc


def _sidn(s):
    return re.sub(r'[^0-9A-Z]', '', str(s).upper())


def wire_sizes(skel_paths):
    """スケルトンTBブロックの GAISENSIZE から 端子台→電線サイズ を集める。"""
    out = {}
    for p in skel_paths:
        doc = ezdxf.readfile(p)
        for e in doc.modelspace():
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {at.dxf.tag: at.dxf.text.strip() for at in e.attribs}
            if a.get('DEVICE') == 'TB' and a.get('GAISENSIZE'):
                sym = f"TB-{a.get('DEVICE1','')}".rstrip('-')
                out[norm(sym)] = {'size': a['GAISENSIZE'],
                                  'terminals': [x.strip() for x in a.get('TERMINAL', '').split(',') if x.strip()]}
    return out


def fuse(seq_paths, skel_paths, layout_path):
    """全シートを融合したハーネスモデルを返す。
    戻り: {'nets': {号線: {...}}, 'ledger': crossref台帳, 'positions', 'tbs'}"""
    seq_models = [DrawingModel(p) for p in seq_paths]
    skel_models = [DrawingModel(p) for p in skel_paths]
    all_models = seq_models + skel_models
    # QC: 浮き電線・スナップ隙間＝決定論トレースが不確かな箇所（Vision/人で確認すべき所）
    qc_issues = []
    for m in all_models:
        qc_issues += qc.check(m)
    ledger = crossref.merge_devices(all_models, layout_path) if layout_path else \
        crossref.merge_devices(all_models, seq_paths[0])
    pos = crossref.device_positions(ledger)
    tbs = crossref.terminal_blocks(ledger)
    sizes = wire_sizes(skel_paths)

    # 号線idでシート横断マージ
    nets = {}
    for m in all_models:
        for n in m.nets:
            if not n['id']:
                continue
            sid = _sidn(n['id'])
            slot = nets.setdefault(sid, {'id': sid, 'kind': n['kind'], 'devices': set(),
                                         'terminals': [], 'sheets': 0})
            slot['devices'] |= set(n['devices'])
            slot['terminals'] += n['terminals']
            slot['sheets'] += 1
            if n['kind'] == 'ctrl':
                slot['kind'] = 'ctrl'      # 制御を優先

    # 各号線を配線＋信頼度付け
    for sid, net in nets.items():
        have = [d for d in sorted(net['devices']) if d in pos]
        net['devices'] = sorted(net['devices'])
        net['n_phys'] = len(have)
        # 端子台の電線サイズ（この号線が触るTBのサイズ）
        net['wire_size'] = ''
        for d in net['devices']:
            if norm(d) in sizes:
                net['wire_size'] = sizes[norm(d)]['size']
                break
        # 信頼度は2軸で正直に付ける（生成時に人手正解は無いので“正しさ”ではなく“状態”）:
        #   conn  … 電線連結で2機器以上を追えた（接続を決定論で生成できた）
        #   geom_only … 号線に機器が1つ以下（連結が途切れ、Visionで補うべき候補）
        #   位置(length)の可否は別フラグ has_pos。
        if len(net['devices']) >= 2:
            net['connectivity'] = 'conn'
        else:
            net['connectivity'] = 'geom_only'      # 決定論で結線しきれない→Vision候補
        net['has_pos'] = len(have) >= 2
        if net['has_pos']:
            r = routing.optimal_wiring(have, pos, terminal_blocks=tbs)
            net['wires'] = r['wires']
            net['relays'] = r['relays']
            net['length'] = r['total']
        else:
            net['wires'] = []
            net['relays'] = []
            net['length'] = 0
    return {'nets': nets, 'ledger': ledger, 'positions': pos, 'tbs': tbs,
            'sizes': sizes, 'qc_issues': qc_issues}


def vision_targets(fused):
    """Vision に回すべき号線（決定論で結線しきれない／機器1つ以下）を返す。"""
    return [sid for sid, n in fused['nets'].items() if n['connectivity'] == 'geom_only']


def summary(fused):
    nets = fused['nets']
    conn = sum(1 for n in nets.values() if n['connectivity'] == 'conn')
    withpos = sum(1 for n in nets.values() if n['has_pos'])
    qcs = collections.Counter(i['type'] for i in fused.get('qc_issues', []))
    return {'nets': len(nets), 'conn': conn, 'geom_only(vision候補)': len(nets) - conn,
            '測長可(物理位置あり)': withpos,
            'conn_pct': round(conn / max(len(nets), 1) * 100, 1),
            'qc要確認': dict(qcs)}
