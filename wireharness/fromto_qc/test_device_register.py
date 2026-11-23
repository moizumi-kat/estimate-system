# -*- coding: utf-8 -*-
"""device_register の検証・プレビュー登録の固定テスト(B: 機器登録バックエンド)。
terminal_library.json は書き換えない(apply=False とバリデーションのみ)。"""
from . import device_register as DR


def test_validate_pinout_ok_and_bad():
    ok, _ = DR.validate_pinout(['1', '4', '5', '8'], com=['1', '4'], no=['5', '8'])
    assert ok
    ok2, probs = DR.validate_pinout(['1', '2'], no=['9'])    # 9 は pins に無い
    assert not ok2 and any('9' in p for p in probs)
    ok3, probs3 = DR.validate_pinout(['1', '1'])             # 重複
    assert not ok3 and any('重複' in p for p in probs3)


def test_register_preview_ok_no_source():
    # 出所は不要。照合規則(型式)あり・apply=False → プレビューが返り、ファイルは書き換えない。
    r = DR.register_pattern('meterswitch_BHN_V_test', ['1', '5', '6', '8'],
                            source='', desc='電圧計切替(試験)', maker='正興電機',
                            match_type_contains='BHN-V', apply=False)
    assert r['ok'], r
    assert 'preview' in r and 'meterswitch_BHN_V_test' in r['preview']['pattern']


def test_register_rejects_no_match_rule():
    r = DR.register_pattern('x_test', ['1', '2'], source='', apply=False)
    assert not r['ok']


def test_maker_model_in_preview_and_list_format():
    # メーカ名/型式がピン配に入り、型式が照合キーにもなる。
    r = DR.register_pattern('t2', ['1', '5', '6', '8'], source='手本', maker='正興電機',
                            model='BHN-V', apply=False)
    pdef = r['preview']['pattern']['t2']
    assert pdef.get('maker') == '正興電機' and pdef.get('model') == 'BHN-V'
    assert r['preview']['match'].get('type_contains') == 'BHN-V'   # 型式→照合キー
    # list_patterns は dict 形式(pins/maker/model/user_added)
    lp = DR.list_patterns()
    assert 'relay_RU4S' in lp and 'pins' in lp['relay_RU4S'] and 'user_added' in lp['relay_RU4S']


if __name__ == '__main__':
    test_validate_pinout_ok_and_bad()
    test_register_preview_ok_no_source()
    test_register_rejects_no_match_rule()
    test_maker_model_in_preview_and_list_format()
    print('device_register tests: OK')
