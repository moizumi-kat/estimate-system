# -*- coding: utf-8 -*-
"""機器ピン配の登録バックエンド(B: 現場で機器情報を登録するインターフェースの中核)。

茂泉様方針: 端子番号は機器TYPEのピン配から選ぶ。未登録機器は『要確認』で現場に提示し(A)、
人がその機器のピン配を登録できるようにする(B)。本モジュールは terminal_library.json へ
安全に追記する(バックアップ＋検証＋冪等)。Web UI(harness_app/terminal_app)から呼ぶ。

メーカ名・型式は必須(推測登録を避け、提供資料/手本/メーカー資料で裏取りした上で登録する)。
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
    """登録済みパターンの一覧(UI表示用)。{name: {pins, maker, model, source, user_added}}。"""
    lib = _load()
    from . import pin_audit as PA
    out = {}
    for name, p in lib.get('patterns', {}).items():
        out[name] = {'pins': sorted(PA.pattern_all_pins(p)),
                     'maker': p.get('maker', ''), 'model': p.get('model', ''),
                     'source': p.get('source', ''), 'user_added': bool(p.get('user_added'))}
    return out


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


def _build_pdef(pins, source, desc, com, no, nc, coil, maker, model):
    label = ' '.join(x for x in (maker, model) if x)
    full_desc = (desc or model or '') + (f'（{label}）' if label else '') + (f'（出所: {source}）' if source else '')
    pdef = {'desc': full_desc, 'pins': [str(x) for x in pins], 'stud': 'socket', 'z': 55,
            'maker': maker or '', 'model': model or '', 'source': source, 'user_added': True}
    for nm, arr in (('com', com), ('no', no), ('nc', nc), ('coil', coil)):
        if arr:
            pdef[nm] = [str(x) for x in arr]
    return pdef


def _build_rule(pattern_name, match_type_contains, match_parts, match_pole):
    rule = {'pattern': pattern_name}
    if match_type_contains:
        rule['type_contains'] = match_type_contains
    if match_parts:
        rule['parts'] = match_parts
        if match_pole:
            rule['pole'] = match_pole
    return rule


def _rules_for(lib, pattern_name):
    return [r for r in lib.get('match', []) if r.get('pattern') == pattern_name]


def get_pattern(pattern_name):
    """1パターンの定義＋照合規則を返す(編集フォームのプリフィル用)。無ければ None。"""
    lib = _load()
    p = lib.get('patterns', {}).get(pattern_name)
    if p is None:
        return None
    return {'pattern': dict(p), 'rules': _rules_for(lib, pattern_name)}


def register_pattern(pattern_name, pins, source, desc='', com=None, no=None, nc=None,
                     coil=None, match_type_contains=None, match_parts=None, match_pole=None,
                     maker='', model='', apply=True):
    """機器ピン配を1件登録(terminal_library.json へ追記)。
      pattern_name, pins(必須), maker(メーカ名)・model(型式)(必須), source(任意・出所メモ),
      com/no/nc/coil(任意), match_type_contains / match_parts(+match_pole)=照合規則。
    apply=False ならプレビュー。戻り: dict(ok, problems, backup?).
    """
    if not match_type_contains and not match_parts and not model:
        return {'ok': False, 'problems': ['照合規則(型式に含む文字 か PARTS)が必要です']}
    ok, probs = validate_pinout(pins, com, no, nc, coil)
    if not ok:
        return {'ok': False, 'problems': probs}
    lib = _load()
    if pattern_name in lib.get('patterns', {}):
        return {'ok': False, 'problems': [f'パターン {pattern_name} は既に存在します(変更は「編集」から)']}
    # 型式が指定され type_contains未指定なら、型式そのものを照合キーにする
    if model and not match_type_contains:
        match_type_contains = model
    pdef = _build_pdef(pins, source, desc, com, no, nc, coil, maker, model)
    rule = _build_rule(pattern_name, match_type_contains, match_parts, match_pole)
    if not apply:
        return {'ok': True, 'problems': [], 'preview': {'pattern': {pattern_name: pdef}, 'match': rule}}
    bak = _backup()
    lib.setdefault('patterns', {})[pattern_name] = pdef
    lib.setdefault('match', [])
    if match_type_contains:
        lib['match'].insert(0, rule)      # 具体的な型式照合は先頭寄りへ
    else:
        lib['match'].append(rule)
    _save(lib)
    from . import terminals as T
    T._LIB = None
    return {'ok': True, 'problems': [], 'backup': bak,
            'resolved_pattern': T.resolve_pattern(match_parts or '', match_type_contains or '')}


def update_pattern(pattern_name, pins, source, desc='', com=None, no=None, nc=None, coil=None,
                   match_type_contains=None, match_parts=None, match_pole=None, maker='', model=''):
    """既存パターンの内容・照合規則を差し替える(編集)。◎誤答ゼロ: 検証してから保存・バックアップ。"""
    ok, probs = validate_pinout(pins, com, no, nc, coil)
    if not ok:
        return {'ok': False, 'problems': probs}
    lib = _load()
    if pattern_name not in lib.get('patterns', {}):
        return {'ok': False, 'problems': [f'パターン {pattern_name} は存在しません']}
    if model and not match_type_contains:
        match_type_contains = model
    bak = _backup()
    old = lib['patterns'][pattern_name]
    pdef = _build_pdef(pins, source, desc, com, no, nc, coil, maker, model)
    pdef['user_added'] = old.get('user_added', False)      # 組込みは組込みのまま(編集は許す)
    lib['patterns'][pattern_name] = pdef
    # この pattern の既存照合規則を入れ替える
    lib['match'] = [r for r in lib.get('match', []) if r.get('pattern') != pattern_name]
    rule = _build_rule(pattern_name, match_type_contains, match_parts, match_pole)
    if match_type_contains:
        lib['match'].insert(0, rule)
    else:
        lib['match'].append(rule)
    _save(lib)
    from . import terminals as T
    T._LIB = None
    return {'ok': True, 'problems': [], 'backup': bak}


def delete_pattern(pattern_name):
    """登録済みパターンを削除(＋その照合規則)。組込み(user_added でない)は誤削除防止のため不可。"""
    lib = _load()
    p = lib.get('patterns', {}).get(pattern_name)
    if p is None:
        return {'ok': False, 'problems': [f'パターン {pattern_name} は存在しません']}
    if not p.get('user_added'):
        return {'ok': False, 'problems': ['組込みパターンは削除できません（内容の修正は「編集」から可能です）']}
    bak = _backup()
    del lib['patterns'][pattern_name]
    lib['match'] = [r for r in lib.get('match', []) if r.get('pattern') != pattern_name]
    _save(lib)
    from . import terminals as T
    T._LIB = None
    return {'ok': True, 'problems': [], 'backup': bak}


def _save(lib):
    with open(_LIB_PATH, 'w', encoding='utf-8') as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)


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
