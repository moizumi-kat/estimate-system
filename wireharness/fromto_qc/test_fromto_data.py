# -*- coding: utf-8 -*-
"""fromto_data(FromToData生成)の固定テスト。DXF非依存部(端点分解・仮番号採番)を中心に。"""
from . import fromto_data as fd


def test_split_ep():
    assert fd._split_ep('ELCB-104:1') == ('ELCB', '104', '1', None)
    assert fd._split_ep('LUG:') == ('LUG', '', '', None)
    assert fd._split_ep('52-102:54') == ('52', '102', '54', None)
    # 端子台: 番号は確定せずグループへ退避
    assert fd._split_ep('TB-12:104U') == ('TB', '', '104U', 'TB-12')
    assert fd._split_ep('TB:回路301(予測)') == ('TB', '', '', 'TB:回路301(予測)')
    assert fd._split_ep('TB:仮56') == ('TB', '', '', 'TB:仮56')


def test_provisional_numbering(monkeypatch=None):
    # 同じTBグループは同じ仮番号、別グループは別番号。機器端点はそのまま。
    orig = fd.harness_draft.build
    fd.harness_draft.build = lambda sd, seiban=None: {'wires': [
        {'from': '52-102:54', 'to': 'TB:回路102', 'tier': '確度:精査', 'sources': ['edge_trace']},
        {'from': '52-104:54', 'to': 'TB:回路102', 'tier': '確度:精査', 'sources': ['edge_trace']},
        {'from': '86X-601:13', 'to': 'TB:仮56', 'tier': '確度:精査', 'sources': ['edge_trace']},
    ], 'summary': {}}
    try:
        res = fd.build_fromto('x')
        rows = res['rows']
        # 1本目と2本目のTBは同一グループ(回路102)→同じ仮番号
        assert rows[0]['to']['機器'] == 'TB'
        assert rows[0]['to']['番号'] == rows[1]['to']['番号']
        # 仮56は別グループ→別番号
        assert rows[2]['to']['番号'] != rows[0]['to']['番号']
        # 全TB番号が '仮' で始まる
        assert all(r['to']['番号'].startswith('仮') for r in rows)
        # 機器端点はそのまま
        assert rows[0]['from'] == {'機器': '52', '番号': '102', '端子': '54'}
        # legend に仮番号→ヒント
        assert set(res['tb_legend']) == {rows[0]['to']['番号'], rows[2]['to']['番号']}
    finally:
        fd.harness_draft.build = orig


if __name__ == '__main__':
    test_split_ep(); test_provisional_numbering()
    print('fromto_data tests: OK')
