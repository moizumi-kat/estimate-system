# -*- coding: utf-8 -*-
"""terminal_rules の固定テスト(手本非依存): devtype/pattern/確定判定(方針B)。"""
from . import terminal_rules as TR


def test_devtype():
    assert TR.devtype('52-101') == '52'
    assert TR.devtype('52A-1') == '52A'
    assert TR.devtype('MCCB-105') == 'MCCB'
    assert TR.devtype('FAN-305') == 'FAN'
    assert TR.devtype('30X1A') == '30X'
    assert TR.devtype('SPDX1') == 'SPDX'
    assert TR.devtype('27X-1') == '27X'
    assert TR.devtype('BZ1') == 'BZ'


def test_pattern():
    assert TR.pattern('13') == 'a接点'
    assert TR.pattern('24') == 'a接点'
    assert TR.pattern('11') == 'b接点'
    assert TR.pattern('22') == 'b接点'
    assert TR.pattern('+') == '極性'
    assert TR.pattern('-') == '極性'
    assert TR.pattern('A1') == 'コイルA'
    assert TR.pattern('5') == '主回路'
    assert TR.pattern('ALa') == '他'


def test_is_confirmed_policyB():
    # 手本と矛盾した機器タイプ(②: FAN, 52 等)は標準パターンでも確定にしない
    model = {'proven': {('RS', 'a接点')}, 'contradicted': {'FAN', '52', 'WHM'}}
    assert TR.is_confirmed('FAN-305', '+', model) is False       # ②: 要確認維持
    assert TR.is_confirmed('52-101', '13', model) is False       # ②: 要確認維持
    assert TR.is_confirmed('WHM-1', '13', model) is False        # ②: 要確認維持
    # 無矛盾タイプ×標準パターン → 確定(方針B)
    assert TR.is_confirmed('30X1A', '13', model) is True         # ③→確定
    assert TR.is_confirmed('RU4S-1', '14', model) is True
    assert TR.is_confirmed('WL-1', '-', model) is True
    # proven(手本一致)は確定
    assert TR.is_confirmed('RS-1', '13', model) is True
    # 非標準パターンで未実証 → 要確認
    assert TR.is_confirmed('ABC-1', 'XYZ', model) is False


def test_template_name():
    model = {'proven': set(), 'contradicted': {'SPD', 'TH', 'WHM', 'FAN'},
             'templates': {'SPD': {'0': 'COM上', '1': 'NO 上'},
                           'TH': {'0': 'CM 下', '1': 'CL 下'},
                           'WHM': {'0': 'CA 下'}}}
    assert TR.template_name('SPD-1', 0, model) == 'COM上'
    assert TR.template_name('SPD-1', 1, model) == 'NO 上'
    assert TR.template_name('TH-305', 0, model) == 'CM 下'
    assert TR.template_name('WHM-1', 0, model) == 'CA 下'
    assert TR.template_name('WHM-1', 1, model) is None     # 未学習順位→割付なし
    assert TR.template_name('FAN-1', 0, model) is None     # テンプレート無し→要確認
    assert TR.template_name('30X1A', 0, model) is None     # 対象外機器


def test_selector_not_confirmed():
    # 選択SW(43/43S/CS/COS/SS)は位置接点で標準規約では確定しない(要確認)。
    model = {'proven': set(), 'contradicted': {'43S'}, 'templates': {}}
    assert TR.is_selector('43S-305') is True
    assert TR.is_selector('43-305') is True
    assert TR.is_selector('CS-1') is True
    assert TR.is_selector('30X1A') is False
    # 43S は contradicted、43 は選択SWゲートで、どちらも標準パターンでも確定しない
    assert TR.is_confirmed('43S-305', '13', model) is False
    assert TR.is_confirmed('43-305', '11', model) is False
    # 通常リレーの a接点は確定
    assert TR.is_confirmed('30X1A', '13', model) is True


def test_selector_func_pairs():
    # 富士AR22体系: a接点(末尾3/4)=NO、b接点(末尾1/2)=NC。在庫を機能別ペアに分ける。
    model = {'proven': set(), 'contradicted': {'43S'}, 'templates': {},
             'selector_inv': {'43S': ['13', '14', '21', '22', '33', '34', '41', '42']}}
    pairs = TR.selector_func_pairs('43S-305', model)
    assert pairs['a'] == [('13', '14'), ('33', '34')]     # NO接点
    assert pairs['b'] == [('21', '22'), ('41', '42')]     # NC接点


def test_blank_type():
    # 番号を持たない機器タイプ(手本で端子番号が空欄=番号なし)。例: FAN(交流送風機)。
    model = {'blank_types': {'FAN'}}
    assert TR.is_blank_type('FAN-305', model) is True
    assert TR.is_blank_type('FAN-1', model) is True
    assert TR.is_blank_type('WHM-1', model) is False
    assert TR.is_blank_type('OL-1A', model) is False
    assert TR.is_blank_type('FAN-1', {}) is False          # モデルに無ければ False


def test_selector_template():
    # 切替SW(LS_CHROB, 富士AR22PR-311B)の位置テンプレート: 上→左→右 = 13/21/14。
    model = {'selector_templates': {'43': {'0': '13', '1': '21', '2': '14'}}}
    tp = TR.selector_template('43-306', model)
    assert tp == {'0': '13', '1': '21', '2': '14'}
    assert TR.selector_template('43-101', model) == tp     # 同タイプは同テンプレート
    assert TR.selector_template('43S-305', model) is None  # 別タイプ(記号判定)はテンプレート無し
    assert TR.selector_template('43-1', {}) is None


def test_gousen_norm_keeps_polarity():
    # 号線ラベルは末尾の極性(+/-)が意味を持ち別ノードになるため保持する。
    from .geometry import gousen_norm, norm
    assert gousen_norm('2DI1-') == '2DI1-'
    assert gousen_norm('2DI1+') == '2DI1+'
    assert gousen_norm('485-') == '485-'
    assert gousen_norm(' 2di1- ') == '2DI1-'               # 空白除去・大文字化
    # 機器記号用 norm はハイフンを除去する(号線用と区別)
    assert norm('2DI1-') == '2DI1'


def test_empty_model_all_unconfirmed():
    model = {'proven': set(), 'contradicted': set()}
    # モデルが空でも、標準パターンは方針Bで確定(規約由来)。非標準は要確認。
    assert TR.is_confirmed('30X1A', '13', model) is True
    assert TR.is_confirmed('X-1', 'ALa', model) is False


if __name__ == '__main__':
    test_devtype(); test_pattern(); test_is_confirmed_policyB()
    test_template_name(); test_selector_not_confirmed(); test_selector_func_pairs()
    test_blank_type(); test_selector_template(); test_gousen_norm_keeps_polarity()
    test_empty_model_all_unconfirmed()
    print('terminal_rules tests: OK')
