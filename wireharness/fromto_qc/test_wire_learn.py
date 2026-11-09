# -*- coding: utf-8 -*-
"""wire_learn(補完配線の横展開)の固定テスト。"""
from . import wire_learn as wl


def _w(fd, ft, td, tt):
    return {'from': {'機器': fd, '番号': '', '端子': ft},
            'to': {'機器': td, '番号': '', '端子': tt}}


def test_suggest_consistency_gate():
    # 同じ流儀Sの2製番で同じ補完配線 → 3番目(同流儀)へ横展開。1製番だけの配線は展開しない。
    cfg = {
        'X': {'style': 'S', 'added_wires': [_w('TB', '', 'TB', ''), _w('52', '13', 'TB', '')]},
        'Y': {'style': 'S', 'added_wires': [_w('TB', '', 'TB', '')]},          # TB渡りは2製番一致
        'Z': {'style': 'S', 'added_wires': []},
        'W': {'style': 'T', 'added_wires': [_w('TB', '', 'TB', '')]},          # 別流儀は数えない
    }
    import types
    orig = wl.seiban_config.added_wires
    wl.seiban_config.added_wires = lambda sb: []       # Z は自製番の確認なし
    try:
        sug = wl.suggest('Z', style='S', configs=cfg)
        keys = {wl._wkey(w) for w in sug}
        assert wl._wkey(_w('TB', '', 'TB', '')) in keys        # 2製番一致→展開
        assert wl._wkey(_w('52', '13', 'TB', '')) not in keys  # 1製番のみ→展開しない
    finally:
        wl.seiban_config.added_wires = orig


def test_suggest_excludes_own():
    cfg = {
        'X': {'style': 'S', 'added_wires': [_w('TB', '', 'TB', '')]},
        'Y': {'style': 'S', 'added_wires': [_w('TB', '', 'TB', '')]},
    }
    orig = wl.seiban_config.added_wires
    wl.seiban_config.added_wires = lambda sb: [_w('TB', '', 'TB', '')]  # 自製番で既に確認済
    try:
        sug = wl.suggest('X', style='S', configs=cfg)
        assert sug == []           # 自製番に有れば横展開候補に出さない
    finally:
        wl.seiban_config.added_wires = orig


if __name__ == '__main__':
    test_suggest_consistency_gate(); test_suggest_excludes_own()
    print('wire_learn tests: OK')
