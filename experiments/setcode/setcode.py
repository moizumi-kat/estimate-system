#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配電盤セットコード統合モジュール（app.pyへ後方互換で組み込む本体）。

役割: 抽出された盤(盤名＋盤属性)を受け、
  1) classify() で盤種(settype/role)を判定
  2) needs_confirm() で読み切れない仕様を洗い出し(コンボボックスで人に確認)
  3) select() で決定的にセットコードを1つ選ぶ(属性完全一致＋容量切上)
を行う。展開・単価は積算ソフト管轄なので出力しない(参考のみ)。

セットコード表は cand_haiden_sets.json(承認前の候補)を読む。承認後は db.json へ統合し、
byCode 側から読むよう1行差し替えるだけでよい(下記 _load を参照)。
方針(CLAUDE.md 第6章): 選定は決定的・AI推測なし。読切れない仕様は推測せず確認。迷ったら△。
"""
import json, re, os

HERE = os.path.dirname(os.path.abspath(__file__))

def _load():
    # 承認後: db.json の settype 付きレコードに切替可能。現状は候補ファイル。
    p = os.path.join(HERE, 'cand_haiden_sets.json')
    return json.load(open(p, encoding='utf-8'))

CAND = _load()

DEFAULTS = {'meter': '普通角'}          # 計器種別の標準
OPTIONS = {                              # 確認ゲートのコンボボックス選択肢(自由入力させない)
    'meter': ['普通角', '広角', 'マルチ'],
    'phase': ['1φ3W', '3φ3W', 'スコット'],
    'role':  ['受電盤', '饋電盤', '一段積', '二段積', '三段積', '母線連絡', '母線連絡+一段積'],
    'vcb':   ['8KA', '12.5KA'],
    'op':    ['手動', '電動', '電動引出', '電磁', '電磁引出PF'],
    'cap':   [],  # 容量は数値入力(KVA)
}
REQ = {'低圧': ['meter', 'phase', 'cap'],
       '高圧': ['role', 'meter', 'vcb', 'op'],
       '段積': ['role', 'meter', 'vcb'],
       '段積VCS': ['role', 'op']}


def _cap(s):
    m = re.search(r'(\d+)', s or '')
    return int(m.group(1)) if m else None


def classify(panel_name):
    """盤名から settype と分かる範囲の属性を返す。判別不能は settype=None(セット対象外)。"""
    n = str(panel_name or '')
    if re.search(r'受電', n):
        return {'settype': '高圧', 'role': '受電盤'}
    if re.search(r'饋電|き電', n):
        return {'settype': '高圧', 'role': '饋電盤'}       # 複数VCBなら段積へ(呼出側で上書き)
    if re.search(r'スコット|ｽｺｯﾄ', n):
        return {'settype': '低圧', 'phase': 'スコット'}
    if re.search(r'電灯', n):
        return {'settype': '低圧', 'phase': '1φ3W'}         # 電灯は概ね単相(要確認可)
    if re.search(r'動力', n) and re.search(r'低圧', n):
        return {'settype': '低圧', 'phase': '3φ3W'}         # 低圧動力盤(TR二次)。動力"制御"盤は別(22-29)
    if re.search(r'コンデンサ|ｺﾝﾃﾞﾝｻ', n):
        return {'settype': None, 'note': 'コンデンサ盤=SC/SR個別(45系)＋VCS一段積(16系)'}
    return {'settype': None}


def needs_confirm(attrs):
    """必須属性のうち None/空 を返す(=コンボボックスで人に問うべき仕様)。"""
    st = attrs.get('settype')
    return [k for k in REQ.get(st, []) if not attrs.get(k)]


def confirm_form(attrs):
    """確認ゲートUI用: 未確定仕様の選択肢＋既定を返す。"""
    return [{'spec': k, 'options': OPTIONS.get(k, []), 'default': DEFAULTS.get(k, '')}
            for k in needs_confirm(attrs)]


def apply_defaults(attrs):
    a = dict(attrs)
    for k in needs_confirm(a):
        if k in DEFAULTS:
            a[k] = DEFAULTS[k]
    return a


def select(attrs):
    """盤属性 -> (code, conf, note)。◎確定 / ○容量切上 / △要確認。決定的・AI推測なし。"""
    st = attrs.get('settype')
    pool = [c for c in CAND if c['settype'] == st]
    if st == '低圧':
        cs = [c for c in pool if c['meter'] == attrs.get('meter') and c['phase'] == attrs.get('phase')]
        kva = _cap(attrs.get('cap'))
        if kva is None:
            return '', '△', '容量不明→確認'
        cs = [c for c in cs if _cap(c['cap']) >= kva]
        if not cs:
            return '', '△', '該当容量なし→確認'
        best = min(cs, key=lambda c: _cap(c['cap']))
        if _cap(best['cap']) == kva:
            return best['code'], '◎', ''
        return best['code'], '○', '容量切上 %d→%dKVA' % (kva, _cap(best['cap']))
    if st == '高圧':
        cs = [c for c in pool if c['role'] == attrs.get('role') and c['meter'] == attrs.get('meter')
              and c['vcb'] == attrs.get('vcb') and c['op'] == attrs.get('op')]
        return (cs[0]['code'], '◎', '') if len(cs) == 1 else ('', '△', '高圧セット 属性不一致→確認')
    if st == '段積':
        cs = [c for c in pool if c['role'] == attrs.get('role') and c['meter'] == attrs.get('meter')
              and c['vcb'] == attrs.get('vcb')]
        return (cs[0]['code'], '◎', '') if len(cs) == 1 else ('', '△', '段積セット 属性不一致→確認')
    if st == '段積VCS':
        cs = [c for c in pool if c['role'] == attrs.get('role') and c['op'] == attrs.get('op')]
        return (cs[0]['code'], '◎', '') if len(cs) == 1 else ('', '△', 'VCS段積 属性不一致→確認')
    return '', '△', '盤種セット対象外(個別選定へ)'


def resolve(panel_name, extracted_attrs=None):
    """app.py統合の入口。盤名＋抽出属性 -> セット選定結果 or 確認要求。
    戻り: {settype, code, conf, note, confirm}(confirm非空なら選定前に人へ)。"""
    attrs = dict(classify(panel_name))
    if extracted_attrs:
        for k, v in extracted_attrs.items():
            if v:
                attrs[k] = v
    if attrs.get('settype') is None:
        return {'settype': None, 'code': '', 'conf': '', 'note': attrs.get('note', 'セット対象外'), 'confirm': []}
    form = confirm_form(attrs)
    code, conf, note = select(apply_defaults(attrs))
    return {'settype': attrs['settype'], 'attrs': attrs, 'code': code, 'conf': conf,
            'note': note, 'confirm': form}


if __name__ == '__main__':
    # 本物見積書で確認した属性で動作確認
    tests = [
        ('低圧電灯盤 No.1', {'meter': '広角', 'cap': '150KVA'}),           # とくなが
        ('低圧動力盤 No.2', {'meter': 'マルチ', 'cap': '300KVA'}),         # 八戸
        ('スコットトランス盤 No.1', {'meter': 'マルチ', 'cap': '100KVA'}),
        ('高圧受電盤', {'meter': '広角', 'vcb': '12.5KA', 'op': '電動'}),  # とくなが
        ('低圧電灯盤 No.9', {'cap': '150KVA'}),                            # 計器未読→確認ゲート
    ]
    for name, ex in tests:
        r = resolve(name, ex)
        gate = '確認:' + str([f['spec'] for f in r['confirm']]) if r['confirm'] else '確認なし'
        print('%-20s -> %s %s  (%s) %s' % (name, r['code'] or '—', r['conf'], gate, r['note']))
