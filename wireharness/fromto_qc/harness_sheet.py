# -*- coding: utf-8 -*-
"""図面から抽出した等電位ノード → ターゲットのハーネスシート書式で出力する。

ターゲット台帳(-1.txt等)と同じ項目を、測長を除いて再現する:
  号線 / From・To(場所,機器,番号,端子) / 種別 / サイズ / 色
渡り(端子ペア)は非一意なので規則生成（最近傍デイジーチェーン）。

出所:
  端点(機器:端子)   … tracer.trace_nodes（号線=等電位ノード）
  種別・サイズ       … CABLEブロックの DENSEN 属性（例 'HIV3.5sq'）
  色                 … 相(R/S/T→赤/白/青)・アース(E/号線末尾E→緑)、他は空
  場所               … 号線/機器から 扉/P(外部)/盤内 を推定（属性が無ければ空）
"""
import re
import csv
import collections
import ezdxf
from . import tracer
from .equipotential import generate_wiring
from .geometry import norm


def _wire_specs(paths):
    """CABLEブロックの DENSEN 属性から 回路番号→(種別,サイズ) を集める。
    DENSEN 例 'HIV3.5sq' → 種別'HIV' サイズ'3.5'。'sq'のみ等は不明として空。"""
    out = {}
    for p in paths:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        for e in doc.modelspace():
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in e.attribs}
            if a.get('PARTS') != '電線サイズ':
                continue
            d = a.get('DENSEN', '')
            m = re.match(r'([A-Za-z]*)([0-9.]+)\s*sq', d)
            if m:
                kind = m.group(1) or 'IV'
                size = m.group(2)
                circ = norm(a.get('DEVICE1', ''))
                if circ:
                    out[circ] = (kind, size)
    return out


_COLOR = {'R': '赤', 'S': '白', 'T': '青', 'N': '黒'}


def _color_of(gousen, terminal=''):
    """相/号線末尾から電線色を推定。R/S/T→赤/白/青、末尾E/アース→緑。不明は空。"""
    g = str(gousen or '')
    if g.endswith('E') or 'アース' in g or g.upper().startswith('E'):
        return '緑'
    m = re.search(r'([RSTN])\d*$', g)
    if m:
        return _COLOR.get(m.group(1), '')
    # 主回路の相ラベル R/S/T
    if g in _COLOR:
        return _COLOR[g]
    return ''


def _circuit_of(gousen):
    """号線から回路番号(先頭の数字塊)を取り出す（種別/サイズ照合用）。例 '201E'→'201'。"""
    m = re.match(r'(\d+)', str(gousen))
    return m.group(1) if m else ''


def build_sheet(seq_paths, skel_paths=None, seiban='', place_of=None):
    """ハーネスシートの電線レコードを生成。
    戻り: [{'gousen','kind','size','wires':[{'color','from':{place,device,no,terminal},'to':{...}}]}]
    place_of: 機器キー(norm)→場所 の辞書（台帳や配置図から作る。無ければ空欄）。"""
    skel_paths = skel_paths or []
    specs = _wire_specs(list(seq_paths) + list(skel_paths))
    place_of = place_of or {}

    # ノード（号線→端子粒度メンバー）
    nodes = {}
    for p in list(seq_paths) + list(skel_paths):
        for g, nd in tracer.trace_nodes(p).items():
            m = nodes.setdefault(g, {'kind': nd['kind'], 'members': set()})
            m['members'] |= nd['members']

    def split(dev):
        if '-' in dev:
            d, n = dev.rsplit('-', 1)
            return d, n
        return dev, ''

    out = []
    for g in sorted(nodes):
        nd = nodes[g]
        # 渡り生成用に端子座標を集める
        idx = {}
        terms = []
        for (dev, term, x, y) in sorted(nd['members']):
            d, n = split(dev)
            key = f"{d}-{n}:{term}"
            if key not in idx:
                idx[key] = {'place': place_of.get(norm(dev), ''), 'device': d, 'no': n,
                            'terminal': (term if term and term != '?' else ''), 'x': x, 'y': y}
                terms.append((key, x, y))
        circ = _circuit_of(g)
        kind, size = specs.get(norm(circ), ('', ''))
        wires = []
        for a, b in generate_wiring(terms):
            ma, mb = idx[a], idx[b]
            wires.append({'color': _color_of(g, ma['terminal']),
                          'from': {k: ma[k] for k in ('place', 'device', 'no', 'terminal')},
                          'to': {k: mb[k] for k in ('place', 'device', 'no', 'terminal')}})
        out.append({'gousen': g, 'kind': kind, 'size': size,
                    'members': [idx[k] for k in idx], 'wires': wires})
    return out


def to_csv(sheet, path):
    """ハーネスシートをCSV(cp932)に出力（測長なし）。"""
    with open(path, 'w', encoding='cp932', errors='replace', newline='') as f:
        w = csv.writer(f)
        w.writerow(['号線', '種別', 'サイズ', '色',
                    'From場所', 'From機器', 'From番号', 'From端子',
                    'To場所', 'To機器', 'To番号', 'To端子'])
        for rec in sheet:
            if not rec['wires']:
                for m in rec['members']:
                    w.writerow([rec['gousen'], rec['kind'], rec['size'], '',
                                m['place'], m['device'], m['no'], m['terminal'], '', '', '', ''])
            for wi in rec['wires']:
                fr, to = wi['from'], wi['to']
                w.writerow([rec['gousen'], rec['kind'], rec['size'], wi['color'],
                            fr['place'], fr['device'], fr['no'], fr['terminal'],
                            to['place'], to['device'], to['no'], to['terminal']])
    return path


def place_map_from_harness(harness_paths):
    """台帳の場所列(扉/P等)から 機器キー(norm)→場所 を作る（検証時の場所付与に利用）。"""
    pm = {}
    for p in harness_paths or []:
        try:
            rows = list(csv.reader(open(p, encoding='cp932', errors='replace'), delimiter='\t'))
        except Exception:
            continue
        for r in rows:
            c = [x.strip() for x in (r + [''] * 11)[:11]]
            dev, num, place = c[5], c[6], c[4]
            if dev and dev != '*' and place:
                pm[norm(dev + ('-' + num if num else ''))] = place
    return pm
