# -*- coding: utf-8 -*-
"""設計図面の不備を学習する(ループA)。

二つの学習のうち「設計からの図面不備」側。教師データは製番ごとの3点セット:
  ・修正前図面(DXF)  … 不備のある状態
  ・不備の内容        … 図面の改訂履歴(FRAMEブロックの RDITAILE 等)＋【修正箇所】PDFの指摘文
  ・修正後図面(DXF)  … 直した状態。before→after の構造差分＝"実際の直し方"

これらから不備ケースを蓄積(design_cases.json)し、①設計不備の検出を 2層(誤答ゼロ)で支援する:
  層1  パターン化できる不備(記載漏れ・相割付・サイズ/回路番号/号線 未記入 等)→ 自動指摘＋実績修正案
  層2  深い回路論理の設計判断 → 断定せず「過去に類似指摘あり」と人へ参照提示

方針(茂泉様): 修正案は必ず実績(before→after)由来。AIは創作しない。図面判断は実データで裏取り。
"""
import os
import re
import json
import collections

import ezdxf

from .geometry import norm

HERE = os.path.dirname(os.path.abspath(__file__))
CASES_PATH = os.path.join(HERE, 'design_cases.json')

# 図枠(FRAME)ブロックの改訂履歴 属性。REVDATEn=日付 / RDITAILEn=修正内容 / RNAMEn=担当。
# 製番により FA2_FRAME / FA3_FRAME 等、連番 n も 1..N と異なるため、正規表現で広く拾う。
_REV_DATE = re.compile(r'^REVDATE(\d+)$')
_REV_DETAIL = re.compile(r'^R(?:DITAILE|DETAIL|DTAIL)(\d+)$')  # 綴り揺れ(RDITAILE)に耐える
_REV_NAME = re.compile(r'^RNAME(\d+)$')


def _attr_map(insert):
    if not insert.attribs:
        return {}
    return {a.dxf.tag: (a.dxf.text or '').strip() for a in insert.attribs}


def extract_revisions(doc):
    """図枠ブロックの改訂履歴 → [{'n','date','detail','name'}]（detail＝修正内容の記録）。"""
    rev = {}
    for e in doc.modelspace():
        if e.dxftype() != 'INSERT' or not e.attribs:
            continue
        a = _attr_map(e)
        for tag, val in a.items():
            if not val:
                continue
            for rx, key in ((_REV_DATE, 'date'), (_REV_DETAIL, 'detail'), (_REV_NAME, 'name')):
                m = rx.match(tag)
                if m:
                    rev.setdefault(m.group(1), {})[key] = val
    out = [dict(n=n, **v) for n, v in sorted(rev.items())]
    return [r for r in out if r.get('detail') or r.get('date')]


def drawing_features(doc):
    """差分用の構造特徴を抽出。
    返り: {'blocks':Counter(name), 'attrs':Counter((name,tag,val)), 'texts':Counter((layer,str))}。"""
    blocks = collections.Counter()
    attrs = collections.Counter()
    texts = collections.Counter()
    for e in doc.modelspace():
        t = e.dxftype()
        if t in ('TEXT', 'MTEXT'):
            s = (e.text if t == 'MTEXT' else e.dxf.text) or ''
            s = s.strip()
            if s:
                texts[(e.dxf.layer, s)] += 1
        elif t == 'INSERT':
            nm = e.dxf.name
            blocks[nm] += 1
            for k, v in _attr_map(e).items():
                if v:
                    attrs[(nm, k, v)] += 1
    return {'blocks': blocks, 'attrs': attrs, 'texts': texts}


def _is_frame(name):
    return 'FRAME' in str(name).upper()


def diff_features(fb, fa):
    """before/after 構造特徴の差分。図枠(FRAME)自身の改訂属性は除外して実体変更だけを見る。"""
    def _drop_frame_attr(counter):
        return collections.Counter({k: v for k, v in counter.items() if not _is_frame(k[0])})

    blocks_add = fa['blocks'] - fb['blocks']
    blocks_rem = fb['blocks'] - fa['blocks']
    attrs_b = _drop_frame_attr(fb['attrs'])
    attrs_a = _drop_frame_attr(fa['attrs'])
    attrs_add = attrs_a - attrs_b
    attrs_rem = attrs_b - attrs_a
    # 属性"変更" = 同一(block,tag)で値だけ違う(remove 旧 + add 新)。相割付やSPEC定格変更を捉える。
    rem_by_bt = collections.defaultdict(list)
    for (nm, tag, val), n in attrs_rem.items():
        rem_by_bt[(nm, tag)].append((val, n))
    add_by_bt = collections.defaultdict(list)
    for (nm, tag, val), n in attrs_add.items():
        add_by_bt[(nm, tag)].append((val, n))
    changed = []
    used_add = set()
    used_rem = set()
    for bt in set(rem_by_bt) & set(add_by_bt):
        for (ov, on) in rem_by_bt[bt]:
            for (nv, nn) in add_by_bt[bt]:
                changed.append({'block': bt[0], 'tag': bt[1], 'before': ov, 'after': nv})
                used_rem.add((bt[0], bt[1], ov))
                used_add.add((bt[0], bt[1], nv))
    def _list(counter, used):
        return [{'block': k[0], 'tag': k[1], 'value': k[2], 'n': n}
                for k, n in counter.items() if k not in used]
    return {
        'blocks_added': [{'name': k, 'n': n} for k, n in blocks_add.items()],
        'blocks_removed': [{'name': k, 'n': n} for k, n in blocks_rem.items()],
        'attrs_added': _list(attrs_add, used_add),
        'attrs_removed': _list(attrs_rem, used_rem),
        'attrs_changed': changed,
        'texts_added': [{'layer': k[0], 'text': k[1], 'n': n} for k, n in (fa['texts'] - fb['texts']).items()],
        'texts_removed': [{'layer': k[0], 'text': k[1], 'n': n} for k, n in (fb['texts'] - fa['texts']).items()],
    }


def _devices_in_diff(diff):
    devs = set()
    for grp in ('attrs_added', 'attrs_removed'):
        for a in diff[grp]:
            if a['tag'] in ('DEVICE', 'DEVICE1'):
                devs.add(a['value'])
    for c in diff['attrs_changed']:
        pass
    for b in diff['blocks_added'] + diff['blocks_removed']:
        devs.add(b['name'])
    return sorted(devs)


def classify(detail_texts):
    """改訂内容/指摘文 → 大分類(キーワードベース・実績の文言から)。未知は'その他'。"""
    blob = ' '.join(detail_texts)
    rules = [
        ('線番・号線', ['線番', '号線', '回路番号']),
        ('端子台・記載漏れ', ['端子台', '端子', 'TB', '記載', '抜け', '漏れ', 'ない']),
        ('相・電圧', ['相', 'Φ', '1Φ3W', '3Φ', '電圧', 'V相', 'U相', 'W相']),
        ('定格・仕様変更', ['定格', 'AT', 'AF', '容量', 'SPEC', '仕様']),
        ('接点・警報', ['接点', '警報', 'ブザー', 'SPD', 'アラーム']),
        ('電源経路', ['電源', '制御電源', '警報電源', '主幹']),
        ('サイズ・電線', ['サイズ', '電線', 'IV', 'HIV', 'KIV', 'スケア']),
    ]
    hit = [name for name, kws in rules if any(k in blob for k in kws)]
    return hit or ['その他']


_EMPTY_DIFF = {'blocks_added': [], 'blocks_removed': [], 'attrs_added': [],
               'attrs_removed': [], 'attrs_changed': [], 'texts_added': [], 'texts_removed': []}


def build_case(seiban, sheet, before_path, after_path=None, pdf_text=''):
    """1ケース(修正前/修正後/不備内容)→ 構造化した不備ケース。
    after_path=None なら『不備のみ(修正後図面なし)』ケースとして差分は空で記録する。"""
    db = ezdxf.readfile(before_path)
    if after_path:
        da = ezdxf.readfile(after_path)
        revisions = extract_revisions(da) or extract_revisions(db)
        diff = diff_features(drawing_features(db), drawing_features(da))
    else:
        da = None
        revisions = extract_revisions(db)
        diff = dict(_EMPTY_DIFF)
    rev_details = [r['detail'] for r in revisions if r.get('detail')]
    pdf_lines = [ln.strip() for ln in (pdf_text or '').splitlines()
                 if ln.strip() and ln.strip() not in ('kensa', '四角形', '矢印', 'テキスト ボックス', 'テキストボックス')]
    defect_texts = rev_details + pdf_lines
    return {
        'seiban': seiban,
        'sheet': sheet,
        'revisions': revisions,
        'defect_texts': defect_texts,
        'categories': classify(defect_texts),
        'devices': _devices_in_diff(diff),
        'diff': diff,
        'sources': {'before': os.path.basename(before_path),
                    'after': os.path.basename(after_path) if after_path else '',
                    'pdf': bool(pdf_text)},
    }


def _precedent(category, limit=2):
    """ケースベースから当該分類の過去実績(修正の文言)を引く＝実績ベースの修正案根拠。"""
    outs = []
    for c in load_cases().get('cases', []):
        if category in c.get('categories', []):
            for r in c.get('revisions', []):
                if r.get('detail'):
                    outs.append(f"{c['seiban']}:{r['detail']}")
    seen = []
    for o in outs:
        if o not in seen:
            seen.append(o)
    return seen[:limit]


# 検出ルール → ケース分類(実績修正案の紐付け)。横断検証で過検出ゼロを確認した層のみ層1。
_RULE_CATEGORY = {'R2': '定格・仕様変更', 'R3': '相・電圧', 'R7': 'その他'}


def review_design_items(seq_paths=None, skel_paths=None):
    """①設計不備UI向け: 既存の決定論チェッカーを横断検証どおりの範囲で走らせ、
    ケースベースの実績修正案を添えて 2層(層1=自動検出 / 層2=参考)で返す。

    横断検証(design_cases.json の detection)の結論に厳密に従う:
      層1(高信頼・正常図面で過検出ゼロ): R2容量逆転(スケルトン限定)・R3中性線端子(V→N)
      層2(参考・断定しない)           : R7接地結線漏れ 等(正常図面で誤発火あり→人が判断)
    戻り: design_feedback と同じ {分類,該当,号線,解決案} 形式(分類に層のタグを付す)。失敗時は空。
    """
    from . import defect_check as _dc
    from .geometry import DrawingModel as _DM

    def _models(paths):
        out = []
        for p in (paths or []):
            try:
                out.append(_DM(p))
            except Exception:
                pass
        return out

    seq = _models(seq_paths)
    skel = _models(skel_paths)
    items = []

    def _agg(rule, findings, tier, tier_tag):
        if not findings:
            return
        places = [f.get('場所', '') for f in findings if f.get('場所')]
        problem = findings[0].get('問題', '')
        suggest = findings[0].get('提案', '')
        prec = _precedent(_RULE_CATEGORY.get(rule, ''))
        fix = suggest
        if prec:
            fix = suggest + '（過去実績: ' + ' / '.join(prec) + '）'
        items.append({'分類': f'{tier_tag}{problem[:28]}',
                      '該当': f'{len(findings)}箇所' + (f'（{places[0]} 他）' if len(places) > 1 else (f'（{places[0]}）' if places else '')),
                      '号線': '', '解決案': fix, 'tier': tier, 'rule': rule})

    # 層1: R2(スケルトン限定)・R3(シーケンス＋スケルトン) — 横断検証で過検出ゼロ
    r2 = []
    for m in skel:
        try:
            r2 += _dc.rule_R2_capacity(m)
        except Exception:
            pass
    _agg('R2', r2, 1, '【自動検出】')
    r3 = []
    for m in seq + skel:
        try:
            r3 += _dc.rule_R3_neutral(m)
        except Exception:
            pass
    _agg('R3', r3, 1, '【自動検出】')
    # 層2: R7接地結線漏れ — 正常図面で誤発火例あり。断定せず参考提示(人が判断)
    r7 = []
    for m in seq + skel:
        try:
            r7 += _dc.rule_R7_earth(m)
        except Exception:
            pass
    _agg('R7', r7, 2, '【参考・要確認】')
    # 注) 端子台記載漏れ等は図面属性だけでは誤答ゼロの自動検出が不可(例 5-12110-38:
    #     501/502はTB無しで正常・503のみ要追加＝回路の負荷有無が属性に無い)。
    #     またケースのブロック存在だけの照合は正常図面にも誤って付くため per図面の自動照合はしない。
    #     これらは /design の過去ケース一覧(受動的な参照ライブラリ)で人が参照する運用とする。
    return items


def load_cases():
    try:
        return json.load(open(CASES_PATH, encoding='utf-8'))
    except Exception:
        return {'cases': []}


def save_cases(data):
    with open(CASES_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return CASES_PATH


def add_cases(new_cases, save=True):
    """不備ケースを追記(同一 製番+sheet+before/after は最新で置換)。"""
    data = load_cases()
    idx = {(c['seiban'], c['sheet'], c['sources']['before'], c['sources']['after']): i
           for i, c in enumerate(data['cases'])}
    for c in new_cases:
        key = (c['seiban'], c['sheet'], c['sources']['before'], c['sources']['after'])
        if key in idx:
            data['cases'][idx[key]] = c
        else:
            data['cases'].append(c)
    # 分類別の件数サマリ(人が傾向を掴むため)
    cat = collections.Counter()
    for c in data['cases']:
        for k in c.get('categories', []):
            cat[k] += 1
    data['summary'] = {'件数': len(data['cases']), '分類別': dict(cat.most_common())}
    if save:
        save_cases(data)
    return data
