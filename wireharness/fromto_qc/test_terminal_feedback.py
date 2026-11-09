# -*- coding: utf-8 -*-
"""terminal_feedback(現場確認の修正保存＋学習)の固定テスト。HARNESS_STATEを一時領域へ隔離。
パス類は呼び出し時に環境変数を読むため reload 不要。環境変数は各テストで必ず復元する。"""
import os
import csv
import tempfile
import contextlib
from . import terminal_rules as TR
from . import terminal_feedback as TFB


@contextlib.contextmanager
def _isolated_state():
    old = os.environ.get('HARNESS_STATE')
    os.environ['HARNESS_STATE'] = tempfile.mkdtemp()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop('HARNESS_STATE', None)
        else:
            os.environ['HARNESS_STATE'] = old


def _csv(path, seiban, rows):
    """rows: [(機器,号線,自動,確定,区分,除外,X,Y), ...]。"""
    head = ['製番', 'シート', '機器', '接続号線', '自動端子番号', '確定端子番号', '区分', '除外', 'X', 'Y']
    with open(path, 'w', encoding='cp932', newline='') as f:
        w = csv.writer(f)
        w.writerow(head)
        for (dev, g, a, fin, ku, ex, x, y) in rows:
            w.writerow([seiban, 'F001', dev, g, a, fin, ku, ex, x, y])


def test_ingest_and_promote_on_agreement():
    with _isolated_state():
        cd = tempfile.mkdtemp()
        _csv(os.path.join(cd, 'a.csv'), 'S1',
             [('XX-1', 'g1', '13', '17', '要確認', '', 100, 200),
              ('XX-1', 'g2', '14', '14', '確定', '', 100, 100)])
        _csv(os.path.join(cd, 'b.csv'), 'S2',
             [('XX-1', 'g1', '13', '17', '要確認', '', 100, 200),
              ('XX-1', 'g2', '14', '14', '確定', '', 100, 100)])
        s1 = TFB.ingest_csv(os.path.join(cd, 'a.csv'))
        assert s1['差異(修正)'] == 1 and s1['製番'] == ['S1'], s1
        TFB.ingest_csv(os.path.join(cd, 'b.csv'))
        r = TFB.learn(min_n=2)
        assert r['term'] == {'XX|0': '17'}, r['term']          # 一致した修正のみ採用
        m = TR.load_model()
        assert TR.term_correction('XX-1', 0, m) == '17'
        assert TR.term_correction('XX-1', 1, m) is None        # 修正なしは採用しない


def test_below_min_n_not_promoted():
    with _isolated_state():
        cd = tempfile.mkdtemp()
        _csv(os.path.join(cd, 'a.csv'), 'S1',
             [('ZZ-1', 'g1', '13', '17', '要確認', '', 100, 200)])
        TFB.ingest_csv(os.path.join(cd, 'a.csv'))
        assert TFB.learn(min_n=2)['term'] == {}                # 1製番では採用しない


def test_conflict_rejected():
    with _isolated_state():
        cd = tempfile.mkdtemp()
        _csv(os.path.join(cd, 'a.csv'), 'S1', [('WW-1', 'g', '13', '17', '要確認', '', 100, 200)])
        _csv(os.path.join(cd, 'b.csv'), 'S2', [('WW-1', 'g', '13', '17', '要確認', '', 100, 200)])
        _csv(os.path.join(cd, 'c.csv'), 'S3', [('WW-1', 'g', '13', '19', '要確認', '', 100, 200)])
        for x in ('a', 'b', 'c'):
            TFB.ingest_csv(os.path.join(cd, x + '.csv'))
        r = TFB.learn(min_n=2)
        assert r['term'] == {}, r['term']                      # 矛盾は採用しない(◎誤答ゼロ)
        assert r['stats']['矛盾により非採用'] == 1, r['stats']


def test_blank_correction_promoted():
    with _isolated_state():
        cd = tempfile.mkdtemp()
        _csv(os.path.join(cd, 'a.csv'), 'S1', [('QQ-1', 'g', '5', '', '要確認', '', 100, 200)])
        _csv(os.path.join(cd, 'b.csv'), 'S2', [('QQ-1', 'g', '5', '', '要確認', '', 100, 200)])
        TFB.ingest_csv(os.path.join(cd, 'a.csv'))
        TFB.ingest_csv(os.path.join(cd, 'b.csv'))
        assert TFB.learn(min_n=2)['term'] == {'QQ|0': ''}      # 空(番号なし)の修正も採用可
        assert TR.term_correction('QQ-1', 0, TR.load_model()) == ''


if __name__ == '__main__':
    test_ingest_and_promote_on_agreement()
    test_below_min_n_not_promoted()
    test_conflict_rejected()
    test_blank_correction_promoted()
    print('terminal_feedback tests: OK')
