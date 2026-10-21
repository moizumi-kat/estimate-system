# -*- coding: utf-8 -*-
"""FromToData(From-To結線リスト)を生成する。ハーネスシート化ソフトへの受け渡しが目的。

方針(ユーザー指示 2026-10-20):
  ・成果物は FromToData（どの端子とどの端子が繋がるか）。線長・ハーネスシート整形は対象外。
  ・端子台(TB)の台番号は図面に無く製造/配置で決まるため、ツールは確定せず『仮番号』で出す。
    同じ物理TBグループには同じ仮番号を与える → ユーザは「仮番号→実台番号」を一度対応付けるだけ。
  ・面(盤)を跨ぐ接続は仮の中継TB(edge_trace が '仮<面ペア>' を挿入)として既に分割済み。

harness_draft.build() の統合結線(トレース＋主回路＋計器＋面モデル)を土台に、TB端点を仮番号へ
正規化し、1行1配線の FromToData にする。各行: from/to(機器,番号,端子) ＋ 確度 ＋ ソース。
"""
import csv
import re

from . import harness_draft


def _split_ep(ep):
    """結線端点文字列 → (機器, 番号, 端子, tb_group)。
    例 'ELCB-104:1'→('ELCB','104','1',None) / 'LUG:'→('LUG','','',None)
       'TB-12:104U'→('TB','','104U','TB-12')         # 台番号は仮扱い→tb_groupに退避
       'TB:回路301(予測)'→('TB','','','TB:回路301')    # 台番号未定。回路でグルーピング
       'TB:仮56'→('TB','','','TB:仮56')               # 面跨ぎの仮中継TB
    tb_group が非None の端点は端子台で、番号は呼び側で仮番号に置換する。"""
    dev, term = (str(ep).split(':', 1) + [''])[:2]
    m = re.match(r'(.+?)-(\d+)$', dev)            # '機器-番号'
    base, no = (m.group(1), m.group(2)) if m else (dev, '')
    if base.upper().startswith('TB'):
        # 台番号は確定しない。グループキー＝ 'TB-<台番号>'(主回路側) または 'TB:<端子ヒント>'。
        group = dev if no else f'TB:{term}'
        real_term = term if no else ''           # 'TB-12:104U' の 104U は実端子、回路/仮ヒントは端子でない
        return ('TB', '', real_term, group)
    return (base, no, term, None)


def build_fromto(seiban_dir, seiban=None):
    """製番フォルダ → FromToData。
    戻り: {'rows':[...], 'tb_legend':{仮番号: 元グループ(ヒント)}, 'summary':{確度:件数}}。
    各 row: {'from':{機器,番号,端子}, 'to':{機器,番号,端子}, '確度', 'sources'}。
    端子台の番号は『仮N』(要確認)。同じ物理TBグループは同じ仮番号。"""
    draft = harness_draft.build(seiban_dir, seiban=seiban)
    wires = draft['wires']

    # 1) 端子台グループを集めて仮番号を決定的に採番(グループ名のソート順で安定)。
    groups = set()
    for w in wires:
        for ep in (w['from'], w['to']):
            _, _, _, g = _split_ep(ep)
            if g:
                groups.add(g)
    prov = {g: f'仮{i + 1}' for i, g in enumerate(sorted(groups))}
    legend = {v: k for k, v in prov.items()}      # 仮番号 → 元グループ(実台番号対応用ヒント)

    def end(ep):
        base, no, term, g = _split_ep(ep)
        if g:
            no = prov[g]                          # 端子台の番号は仮番号
        return {'機器': base, '番号': no, '端子': term}

    rows = []
    for w in wires:
        rows.append({'from': end(w['from']), 'to': end(w['to']),
                     '確度': w['tier'], 'sources': w.get('sources', [])})
    return {'rows': rows, 'tb_legend': legend, 'summary': draft['summary']}

# 注) 制御のTB渡り(例 'TB-52: — TB-52:')は端子情報が無く、本数も台ごとに異なる製造側の
#     デイジーチェーンで、④ですら端子空欄で記録される不透明なデータ。トレースから正しい本数・
#     位置を導けないため、候補自動生成は行わない(過剰生成=誤答になる)。接地(E)/相(U/V/W)の
#     TB渡りは main_circuit が端子名付きで生成済み。制御のTB渡りは人の確認/確認ループに委ねる。


_HEADER = ['From場所', 'From機器', 'From番号', 'From端子',
           'To場所', 'To機器', 'To番号', 'To端子', '確度', 'ソース']


def to_csv(result, path):
    """FromToData を CSV に書き出す(線長なし)。末尾に仮TB→ヒントの対応表を付す。"""
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        for r in result['rows']:
            fr, to = r['from'], r['to']
            w.writerow(['', fr['機器'], fr['番号'], fr['端子'],
                        '', to['機器'], to['番号'], to['端子'],
                        r['確度'], '/'.join(r['sources'])])
        w.writerow([])
        w.writerow(['仮TB対応表(ユーザが実台番号を割当)', '仮番号', 'ヒント(回路/面/主回路台)'])
        for prov_no, hint in sorted(result['tb_legend'].items()):
            w.writerow(['', prov_no, hint])
    return path
