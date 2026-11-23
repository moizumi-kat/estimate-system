# -*- coding: utf-8 -*-
"""機器ピン配の登録バックエンド(B: 現場で機器情報を登録するインターフェースの中核)。

茂泉様方針: 端子番号は機器TYPEのピン配から選ぶ。未登録機器は『要確認』で現場に提示し(A)、
人がその機器のピン配を登録できるようにする(B)。本モジュールは terminal_library.json へ
安全に追記する(バックアップ＋検証＋冪等)。Web UI(harness_app/terminal_app)から呼ぶ。

登録の出所(source)は必ず残す(提供資料/手本/メーカー資料など。推測登録を避け、裏取りを明示)。
"""
import json
import os
import shutil
import time

_LIB_PATH = os.path.join(os.path.dirname(__file__), 'terminal_library.json')


def _load():
    with open(_LIB_PATH, encoding='utf-8') as f:
        return json.load(f)


def _backup():
    dst = _LIB_PATH + '.bak_' + time.strftime('%Y%m%d_%H%M%S')
    shutil.copy2(_LIB_PATH, dst)
    return dst


def list_patterns():
    """登録済みパターン名とピン数の一覧(UI表示用)。"""
    lib = _load()
    from . import pin_audit as PA
    return {name: sorted(PA.pattern_all_pins(p)) for name, p in lib.get('patterns', {}).items()}


def validate_pinout(pins, com=None, no=None, nc=None, coil=None):
    """登録前の最小検証。pins(全端子)は非空・重複なし、com/no/nc/coil は pins の部分集合。
    戻り: (ok, 問題リスト)。◎誤答ゼロ: 矛盾した登録を弾く。"""
    probs = []
    pins = [str(x) for x in (pins or [])]
    if not pins:
        probs.append('pins(全端子)が空です')
    if len(pins) != len(set(pins)):
        probs.append('pins に重複があります')
    ps = set(pins)
    for nm, arr in (('com', com), ('no', no), ('nc', nc), ('coil', coil)):
        for x in (arr or []):
            if str(x) not in ps:
                probs.append(f'{nm} の端子 {x} が pins に含まれていません')
    if no is not None and com is not None and len(no) != len(com):
        probs.append('no と com の極数が一致しません(各NO接点に対応COMが要る)')
    return (not probs), probs


def register_pattern(pattern_name, pins, source, desc='', com=None, no=None, nc=None,
                     coil=None, match_type_contains=None, match_parts=None, match_pole=None,
                     apply=True):
    """機器ピン配を1件登録(terminal_library.json へ追記)。
      pattern_name: 例 'relay_XXX' / 'meterswitch_BHN_V'
      pins: 全端子番号のリスト(必須)
      source: 出所(必須・例『正興電機 BHN-V 展開図 N-8』)。裏取り明示のため空は不可。
      com/no/nc/coil: 任意(接点折返し等に使う)
      match_type_contains / match_parts(+match_pole): 図面のTYPE/PARTSからこのパターンを引く規則
    apply=False なら検証だけして書き込まない(プレビュー)。戻り: dict(ok, problems, backup?).
    """
    if not source or not str(source).strip():
        return {'ok': False, 'problems': ['source(出所)は必須です。裏取り元を記載してください']}
    if not match_type_contains and not match_parts:
        return {'ok': False, 'problems': ['照合規則(type_contains か parts)が必要です']}
    ok, probs = validate_pinout(pins, com, no, nc, coil)
    if not ok:
        return {'ok': False, 'problems': probs}
    lib = _load()
    if pattern_name in lib.get('patterns', {}):
        return {'ok': False, 'problems': [f'パターン {pattern_name} は既に存在します(別名にするか編集してください)']}
    pdef = {'desc': (desc or pattern_name) + f'（出所: {source}）',
            'pins': [str(x) for x in pins], 'stud': 'socket', 'z': 55}
    for nm, arr in (('com', com), ('no', no), ('nc', nc), ('coil', coil)):
        if arr:
            pdef[nm] = [str(x) for x in arr]
    rule = {'pattern': pattern_name}
    if match_type_contains:
        rule['type_contains'] = match_type_contains
    if match_parts:
        rule['parts'] = match_parts
        if match_pole:
            rule['pole'] = match_pole
    if not apply:
        return {'ok': True, 'problems': [], 'preview': {'pattern': {pattern_name: pdef}, 'match': rule}}
    bak = _backup()
    lib.setdefault('patterns', {})[pattern_name] = pdef
    # type_contains規則は具体的な順を優先するため先頭寄りへ、parts規則は末尾でよい
    lib.setdefault('match', [])
    if match_type_contains:
        lib['match'].insert(0, rule)
    else:
        lib['match'].append(rule)
    with open(_LIB_PATH, 'w', encoding='utf-8') as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)
    # 検証: 読み直せること＋resolve_patternが引けること
    from . import terminals as T
    T._LIB = None
    pat = T.resolve_pattern(match_parts or '', match_type_contains or '')
    return {'ok': True, 'problems': [], 'backup': bak, 'resolved_pattern': pat}


def pending_confirmations(sheet):
    """produce 済みシートから『要確認』端子を集めて UI へ渡す。
    戻り: [{device,no,terminal,note}]。A(flag_sheet)で付けた pin_confirm を拾う。"""
    out = []
    for row in (sheet or []):
        for m in row.get('members', []):
            if m.get('pin_confirm'):
                out.append({'device': m.get('device', ''), 'no': m.get('no', ''),
                            'terminal': m.get('terminal', ''), 'note': m['pin_confirm'],
                            'gousen': row.get('gousen', '')})
    return out
