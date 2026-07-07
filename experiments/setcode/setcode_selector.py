#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配電盤セットコード・セレクタ（決定的・AI推測なし）＋不明仕様の確認ゲート。

パイプライン:
  図面 → 抽出(盤属性) → 【確認ゲート】読み切れない仕様をユーザーに問う → セレクタ → セットコード

方針(CLAUDE.md準拠):
- 選定は決定的(属性の完全一致＋容量は仕様以上で最近傍上位=切上)。AIはコードを生成しない。
- 読み切れない仕様は推測せず、選定前に確認ゲートで人に問う。既定=普通角。迷ったら△。
"""
import json, re, os

HERE = os.path.dirname(os.path.abspath(__file__))
CAND = json.load(open(os.path.join(HERE, 'cand_haiden_sets.json'), encoding='utf-8'))

def capval(s):
    m = re.search(r'(\d+)', s or '')
    return int(m.group(1)) if m else None

# ---- 確認ゲート: 抽出結果のうち読み切れない仕様を洗い出す ----
# 既定値。計器種別の標準は「普通角」。
DEFAULTS = {'meter': '普通角'}

# 各仕様の選択肢(UIはコンボボックス/プルダウンで提示。自由入力させない)
OPTIONS = {
    'meter': ['普通角', '広角', 'マルチ'],
    'phase': ['1φ3W', '3φ3W', 'スコット'],
    'role':  ['受電盤', '饋電盤', '一段積', '二段積', '三段積', '母線連絡', '母線連絡+一段積'],
    'vcb':   ['8KA', '12.5KA'],
    'op':    ['手動', '電動', '電磁', '電磁引出PF'],
    'cap':   [],  # 容量は数値入力(KVA)
}

def needs_confirm(attrs):
    """抽出属性のうち None/空 の必須項目を返す(=ユーザーに問うべき仕様)。"""
    missing = []
    st = attrs.get('settype')
    req = {'低圧': ['meter', 'phase', 'cap'],
           '高圧': ['role', 'meter', 'vcb', 'op'],
           '段積': ['role', 'meter', 'vcb'],
           '段積VCS': ['role', 'op']}.get(st, [])
    for k in req:
        if not attrs.get(k):
            missing.append(k)
    return missing

def apply_defaults(attrs):
    """未確定仕様に既定値を当てる(ユーザー確認の代わり/併用)。"""
    a = dict(attrs)
    for k in needs_confirm(a):
        if k in DEFAULTS:
            a[k] = DEFAULTS[k]
    return a

# ---- セレクタ ----
def select(attrs):
    """盤属性 -> (code, conf, note)。確定=◎/容量切上=○/不明=△。"""
    st = attrs.get('settype')
    pool = [c for c in CAND if c['settype'] == st]
    if st == '低圧':
        cs = [c for c in pool if c['meter'] == attrs.get('meter') and c['phase'] == attrs.get('phase')]
        kva = capval(attrs.get('cap'))
        if kva is None:
            return '', '△', '容量不明→確認'
        cs = [c for c in cs if capval(c['cap']) >= kva]
        if not cs:
            return '', '△', '該当容量なし→確認'
        best = min(cs, key=lambda c: capval(c['cap']))
        conf = '◎' if capval(best['cap']) == kva else '○'
        note = '' if conf == '◎' else '容量切上 %d→%dKVA' % (kva, capval(best['cap']))
        return best['code'], conf, note
    if st in ('高圧',):
        cs = [c for c in pool if c['role'] == attrs.get('role') and c['meter'] == attrs.get('meter')
              and c['vcb'] == attrs.get('vcb') and c['op'] == attrs.get('op')]
        if len(cs) == 1:
            return cs[0]['code'], '◎', ''
        return '', '△', '高圧セット 属性不一致/複数→確認'
    if st == '段積':
        cs = [c for c in pool if c['role'] == attrs.get('role') and c['meter'] == attrs.get('meter')
              and c['vcb'] == attrs.get('vcb')]
        if len(cs) == 1:
            return cs[0]['code'], '◎', ''
        return '', '△', '段積セット 属性不一致/複数→確認'
    if st == '段積VCS':
        cs = [c for c in pool if c['role'] == attrs.get('role') and c['op'] == attrs.get('op')]
        if len(cs) == 1:
            return cs[0]['code'], '◎', ''
        return '', '△', 'VCS段積 属性不一致/複数→確認'
    return '', '△', '盤種不明→確認'

def run(panels):
    """panels: [{name, attrs}] を確認ゲート→選定。未確定があれば confirm リストを返す。"""
    confirm = []
    for p in panels:
        miss = needs_confirm(p['attrs'])
        if miss:
            # UIはコンボボックスで options から選択、default を初期選択にする
            confirm.append({'panel': p['name'],
                            'ask': [{'spec': k, 'options': OPTIONS.get(k, []),
                                     'default': DEFAULTS.get(k, '')} for k in miss]})
    return confirm  # 空なら選定へ / 非空ならユーザー確認(コンボボックス)を促す

if __name__ == '__main__':
    # 木村病院 低圧系(単線結線図から読取った属性; 計器種別は器具表/標準で普通→この案件はマルチ)
    demo = [
        {'name': '低圧電灯盤 No.1', 'attrs': {'settype': '低圧', 'meter': 'マルチ', 'phase': '1φ3W', 'cap': '150KVA'}},
        {'name': '低圧電灯盤 No.3', 'attrs': {'settype': '低圧', 'meter': 'マルチ', 'phase': '1φ3W', 'cap': '100KVA'}},
        {'name': '低圧動力盤 No.1', 'attrs': {'settype': '低圧', 'meter': 'マルチ', 'phase': '3φ3W', 'cap': '300KVA'}},
        {'name': 'スコット盤 No.1', 'attrs': {'settype': '低圧', 'meter': 'マルチ', 'phase': 'スコット', 'cap': '100KVA'}},
    ]
    gate = run(demo)
    print('確認ゲート(未確定仕様):', gate or 'なし=そのまま選定')
    for p in demo:
        code, conf, note = select(apply_defaults(p['attrs']))
        print('  %-16s -> %s %s %s' % (p['name'], code, conf, note))
