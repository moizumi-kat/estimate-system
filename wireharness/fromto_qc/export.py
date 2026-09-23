# -*- coding: utf-8 -*-
"""From-To（等電位ノード＋物理配線）を §3スキーマで書き出す（製造アドオン フェーズB→A 受け渡し）。

docs/fromto_addon_spec.md の出力仕様に対応:
  §3.1 号線（等電位ノード）  … nodes[].members = [{device, terminal}]  … 第一級・一意
  §3.2 物理配線（渡り）      … wires[] = [{from, to}]  … 規則生成（最近傍デイジーチェーン）
生成物:
  build_nodes(...)     → 号線→ノード情報（端子粒度・座標付き、複数シートをマージ）
  export_fromto(...)   → <seiban>_nodes.json / <seiban>_fromto.csv を書き出し

号線ラベルで等電位ノードを組む設計規約（同じ号線＝同じ線／母線＝1ノード）に従う。
渡りは一意でないので規則生成（順番違いは電気的に同じ＝ノード単位で正しければ良い）。
"""
import os
import csv
import json
import collections
from . import tracer
from .equipotential import generate_wiring


def build_nodes(seq_paths, skel_paths=None, seiban=''):
    """複数シートを号線でマージし、端子粒度のノードを返す。
    戻り: {号線: {'kind', 'members': set((機器,端子,x,y)), 'devices': set(機器)}}"""
    skel_paths = skel_paths or []
    merged = {}
    for p in list(seq_paths) + list(skel_paths):
        for g, nd in tracer.trace_nodes(p).items():
            m = merged.setdefault(g, {'kind': nd['kind'], 'members': set(), 'devices': set()})
            m['members'] |= nd['members']
            m['devices'] |= nd['devices']
    return merged


def _split(sym):
    """'DTX-11' → ('DTX','11')、'TB' → ('TB','')。"""
    if '-' in sym:
        d, n = sym.rsplit('-', 1)
        return d, n
    return sym, ''


def node_records(nodes):
    """§3.1 nodes[] を作る。members は {device, no, terminal} の一覧（重複排除・整列）。
    渡り生成のため端子座標も保持した内部表現を返す（JSON化時に座標は落とす）。"""
    recs = []
    for g in sorted(nodes):
        nd = nodes[g]
        mem = []
        seen = set()
        for (dev, term, x, y) in sorted(nd['members']):
            d, n = _split(dev)
            key = (d, n, term)
            if key in seen:
                continue
            seen.add(key)
            mem.append({'device': d, 'no': n,
                        'terminal': (term if term and term != '?' else ''),
                        'x': x, 'y': y})
        recs.append({'gousen': g, 'kind': nd['kind'], 'members': mem})
    return recs


def watari_of(node_rec, max_degree=2):
    """§3.2 渡り（端子ペア列）を規則生成。端子座標の無いメンバーは除外。
    戻り: [{'from': {device,no,terminal}, 'to': {device,no,terminal}}]"""
    idx = {}
    terms = []
    for m in node_rec['members']:
        if m['x'] is None or m['y'] is None:
            continue
        key = f"{m['device']}-{m['no']}:{m['terminal']}"
        idx[key] = m
        terms.append((key, m['x'], m['y']))
    pairs = generate_wiring(terms, max_degree=max_degree)
    out = []
    for a, b in pairs:
        ma, mb = idx[a], idx[b]
        out.append({'from': {'device': ma['device'], 'no': ma['no'], 'terminal': ma['terminal']},
                    'to': {'device': mb['device'], 'no': mb['no'], 'terminal': mb['terminal']}})
    return out


def export_fromto(seq_paths, skel_paths=None, seiban='', out_dir='.', with_watari=True):
    """§3スキーマで JSON（ノード＋渡り）と CSV（フラット From-To）を書き出す。
    戻り: {'nodes_json': path, 'fromto_csv': path, 'node_count', 'wire_count'}"""
    os.makedirs(out_dir, exist_ok=True)
    nodes = build_nodes(seq_paths, skel_paths, seiban)
    recs = node_records(nodes)

    # JSON（§3.1 nodes ＋ §3.2 wires）。座標はメンバーからは落とす（内部用途のみ）。
    nodes_out = []
    csv_rows = []
    wire_count = 0
    for r in recs:
        watari = watari_of(r) if with_watari else []
        wire_count += len(watari)
        nodes_out.append({
            'gousen': r['gousen'], 'kind': r['kind'],
            'members': [{'device': m['device'], 'no': m['no'], 'terminal': m['terminal']}
                        for m in r['members']],
            'wires': watari,
        })
        for w in watari:
            f, t = w['from'], w['to']
            csv_rows.append([r['gousen'], r['kind'],
                             f['device'], f['no'], f['terminal'],
                             t['device'], t['no'], t['terminal']])
        # 渡りが作れない（メンバー0/1・座標無し）ノードも、メンバーだけは CSV に残す
        if not watari:
            for m in r['members']:
                csv_rows.append([r['gousen'], r['kind'],
                                 m['device'], m['no'], m['terminal'], '', '', ''])

    base = seiban or 'fromto'
    nodes_json = os.path.join(out_dir, f"{base}_nodes.json")
    fromto_csv = os.path.join(out_dir, f"{base}_fromto.csv")
    with open(nodes_json, 'w', encoding='utf-8') as f:
        json.dump({'seiban': seiban, 'nodes': nodes_out}, f, ensure_ascii=False, indent=2)
    with open(fromto_csv, 'w', encoding='cp932', errors='replace', newline='') as f:
        w = csv.writer(f)
        w.writerow(['号線', '種別', 'From機器', 'From番号', 'From端子',
                    'To機器', 'To番号', 'To端子'])
        w.writerows(csv_rows)
    return {'nodes_json': nodes_json, 'fromto_csv': fromto_csv,
            'node_count': len(nodes_out), 'wire_count': wire_count}
