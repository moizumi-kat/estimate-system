# -*- coding: utf-8 -*-
"""alias.learn の最小テスト（合成データ）。`python -m wireharness.fromto_qc.test_alias` で実行。

保守的学習の要件を固定する:
  - 同じ別名対応が「異なる号線で複数回」一貫して現れた時だけ採用する。
  - 共通母線など高次数の機器は別名先にしない（次数で減点）。
  - 単一号線の共起ノイズは採用しない（min_votes=2 で弾く）。
"""
from . import alias


class _T:
    def __init__(self, device, name):
        self.device, self.name = device, name


class _M:
    def __init__(self, nets):
        self.nets = nets


def _net(nid, terms, devs):
    return {'id': nid, 'kind': 'ctrl',
            'terminals': [_T(d, n) for d, n in terms], 'devices': devs}


def test_learns_recurring_alias_and_rejects_bus():
    draw = _M([
        _net('N1', [('43-1', '13'), ('52-1', 'A1'), ('TB-1', '')], ['43-1', '52-1', 'TB-1']),
        _net('N2', [('43-1', '21'), ('51-1', '95'), ('TB-1', '')], ['43-1', '51-1', 'TB-1']),
        _net('N3', [('TB-1', ''), ('52-1', 'A2')], ['TB-1', '52-1']),   # TBを高次数に
    ])
    human = [
        {'id': 'N1', 'kind': 'ctrl', 'ends': [('切', '1', '13'), ('52', '1', 'A1'), ('TB', '1', '')]},
        {'id': 'N2', 'kind': 'ctrl', 'ends': [('切', '1', '21'), ('51', '1', '95'), ('TB', '1', '')]},
    ]
    al = alias.learn([(human, [draw])], min_votes=2, min_ratio=0.6)
    assert al.get('1') == '431', al          # 切1(→ck '1') が 43-1 に対応
    assert 'TB1' not in al.values()          # 共通母線は別名先にしない


def test_abstains_on_single_net():
    draw = _M([_net('N1', [('X-1', ''), ('Y-1', '')], ['X-1', 'Y-1'])])
    human = [{'id': 'N1', 'kind': 'ctrl', 'ends': [('A', '1', ''), ('Y', '1', '')]}]
    al = alias.learn([(human, [draw])], min_votes=2, min_ratio=0.6)
    assert al == {}, al                      # 単一号線だけの共起では学習しない


if __name__ == '__main__':
    test_learns_recurring_alias_and_rejects_bus()
    test_abstains_on_single_net()
    print('alias tests: OK')
