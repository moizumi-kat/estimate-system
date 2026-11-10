# -*- coding: utf-8 -*-
"""④FromToデータ(ハーネス作業者が入力した正解)の読み込み・正規化。

作業者の配線情報リスト(.txt, tab区切り, cp932)を読み、1配線=2端点(渡りは分割された
1本ごと)に正規化する。採点(生成From-Toとの突合)と学習(端子語彙・圧着)に使う共通ローダ。

行の列(先頭に空列があるため split('\t') の index):
  c[1]=号線/色, c[2]=マーカー記号, c[3]=長さ(+'無し'=その端にマーカー無), c[4]=扉等位置,
  c[5]=機器, c[6]=番号, c[7]=端子
仕様ヘッダ行: c[1]=c[2]='*', c[3]=電線種別(エコ/ビニル…非数字), c[4]=サイズ, c[5]=色。
区切り行: 主要列がすべて '*'/''。配線は区切り行の間の端点行(通常2行=両端)。

正解にはハーネス対象(≤5.5sq)のみが入る(太物≥8sqは別工程=別ファイル⑤⑥)。実データ10製番で
サイズ最大5.5sqを確認済み。
"""
import csv
import glob
import os
import re


def _read(path):
    try:
        return open(path, 'rb').read().decode('cp932')
    except Exception:
        return open(path, 'rb').read().decode('cp932', 'replace')


def _is_spec(c):
    return c[1] == '*' and c[2] == '*' and c[3] not in ('', '*') and not c[3][:1].isdigit()


def _is_sep(c):
    return all(x in ('*', '') for x in (c[1], c[2], c[5], c[6], c[7])) and c[3] in ('*', '')


def parse(path):
    """1ファイル → [wire]。wire={'type','size','color','ends':[end,end]}。
    end={'dev','no','term','length','door','gousen','marker','tube'(マーカー有=True)}。

    物理配線は端点2行で1本(渡りは共有点を繰り返した連続ペアとして別々に記録される)。
    区切り行('* *…')やマーカー仕様行は飛ばし、端点行を2つ貯めるごとに1配線を確定する。
    仕様ヘッダ(種別/サイズ/色)を跨ぐときは貯めかけの単独端点を破棄して次群へ。"""
    wires = []
    ctype = csize = ccolor = None
    pend = []

    def emit_pair():
        nonlocal pend
        while len(pend) >= 2:
            a, b = pend[0], pend[1]
            wires.append({'type': ctype, 'size': csize, 'color': ccolor, 'ends': [a, b]})
            pend = pend[2:]

    rows = list(csv.reader(_read(path).splitlines(), delimiter='\t'))
    for r in rows[1:]:                      # 1行目は見出し("配線情報リスト…")
        c = [x.strip() for x in (r + [''] * 10)[:10]]
        if _is_spec(c):
            pend = []                        # 群の切替。半端な端点は捨てる
            ctype = c[3]
            csize = c[4] if c[4] not in ('', '*') else None
            ccolor = c[5] if c[5] not in ('', '*') else None
            continue
        if _is_sep(c):
            continue                         # 区切り行は端点ではない(飛ばすだけ)
        dev = c[5]
        if dev in ('', '*') and c[1] in ('', '*'):
            continue
        ln = c[3]
        tube = not ln.endswith('無し')
        length = re.sub(r'無し$', '', ln)
        pend.append({'dev': dev, 'no': c[6], 'term': c[7], 'length': length,
                     'door': (c[4] if c[4] not in ('', '*') else ''),
                     'gousen': (c[1] if c[1] not in ('', '*') else ''),
                     'marker': (c[2] if c[2] not in ('', '*') else ''), 'tube': tube})
        emit_pair()
    return wires


def endpoint_sym(end):
    """端点 → 機器記号。機器＋番号を '-' 連結(番号無しは機器のみ)。例 MCCB-105, TB-11, LUG。"""
    dev, no = (end['dev'] or '').strip(), (end['no'] or '').strip()
    # SPD等は番号欄に端子(ET等)が入る場合がある→番号が端子的なら機器のみ
    if no and not re.fullmatch(r'[A-Za-z]+', no):
        return f'{dev}-{no}' if no else dev
    return dev


def endpoint_key(end):
    """端点 → (機器記号, 端子) の正規化キー。採点の突合単位。"""
    return (endpoint_sym(end), (end['term'] or '').strip())


def to_edges(wires):
    """[wire] → set(frozenset{endpoint_key, endpoint_key})。渡り1本=1エッジ。"""
    edges = set()
    for w in wires:
        es = w['ends']
        for i in range(len(es) - 1):        # 通常は2端点だが保険でチェーン展開
            a, b = endpoint_key(es[i]), endpoint_key(es[i + 1])
            if a != b:
                edges.add(frozenset((a, b)))
    return edges


def load_seiban(seiban_dir):
    """製番フォルダ → {'files','wires','edges','sizes'}。④FromToデータ_*.txt を全読み。"""
    files = sorted(glob.glob(os.path.join(seiban_dir, 'FromToデータ_*.txt')))
    wires = []
    for f in files:
        wires.extend(parse(f))
    sizes = {}
    for w in wires:
        s = w.get('size') or '?'
        sizes[s] = sizes.get(s, 0) + 1
    return {'files': files, 'wires': wires, 'edges': to_edges(wires), 'sizes': sizes}
