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
# 学習結果(ケースベース)の置き場所。harness_learn と同じ HARNESS_STATE 永続領域に外部化
# (未指定ならパッケージ内=従来どおり)。初回はリポジトリ同梱値からシード。
from . import harness_learn as _HL_state
CASES_PATH = _HL_state._state_path('design_cases.json')

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


def _orphan_contacts_strict(control_models, physical_models):
    """R4(親無し接点)の誤答ゼロ版。横断検証に基づく補正:
    ・対象は『警報/補助』接点のみ(MCCB/ELCB一律は除外＝過検出源)。
    ・物理図(スケルトン＋内部配置図)が無ければ判定不能＝スキップ(誤検出防止)。
    正常全セット(5-29026-5/4-21088/5-12043)で過検出0件を確認済み。"""
    from .geometry import norm as _n, SKIP_DEVICES as _SKIP
    if not physical_models:
        return []
    phys = set()
    for m in physical_models:
        for sym, dev, dev1, parts in _dc_records(m):
            phys.add(_n(sym))
            phys.add(_cat(dev, parts, dev1))
    out = []
    seen = set()
    for m in control_models:
        for e in m.msp:
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {at.dxf.tag: (at.dxf.text or '').strip() for at in e.attribs}
            dev = a.get('DEVICE', '')
            if not dev or dev in _SKIP:
                continue
            cmnt = a.get('CMNTJ1', '') + a.get('CMNTJ2', '')
            if not ('警報' in cmnt or '補助' in cmnt):      # 警報/補助接点のみ
                continue
            sym = f"{dev}-{a.get('DEVICE1','')}" if a.get('DEVICE1') else dev
            key = _n(sym)
            if key in seen or key in phys or _cat(dev, a.get('PARTS', ''), a.get('DEVICE1', '')) in phys:
                continue
            seen.add(key)
            out.append({'場所': sym, 'cmnt': cmnt or '接点'})
    return out


# defect_check の内部関数を薄く借用(R4厳格版用)
def _dc_records(model):
    from . import defect_check as _dc
    return _dc._dev_records(model)


def _cat(dev, parts, dev1):
    from . import defect_check as _dc
    return _dc._cat_key(dev, parts, dev1)


def review_design_items(seq_paths=None, skel_paths=None, layout_paths=None):
    """①設計不備UI向け: 既存の決定論チェッカーを横断検証どおりの範囲で走らせ、
    ケースベースの実績修正案を添えて 2層・明示グループで返す。

    横断検証の結論に厳密に従う(誤答ゼロ):
      【生成時整合性】層1: R4親無し接点(警報/補助のみ・配置図必須)  …正常全セットで過検出0
         (float/near/サイズ未記入/回路・号線無TB は harness_produce 側で層1提示＝生成時矛盾)
      【参考・要確認】層2: R2容量逆転・R3中性線端子(V→N)・R7接地結線漏れ
         …トポロジを持たない発見的ルール。真の不備も拾うが並列分岐等で誤発火し得る→人が判断。
    ※R2は主幹/分岐を位置で推定するため、同一段の並列分岐(定格違い)を逆転と誤認する例を確認
      (4-21088 MCCB109/110, 5-12043 MCCB508/510)。真の主従は結線トポロジが必要なため層2に留置。
    ※R1配置図漏れは除外を重ねても正常図面で過検出が残る(進相ｺﾝﾃﾞﾝｻ/計器/補助ﾘﾚｰ等は
      配置図に載らないのが正常)ため不採用。機器マスタで『配置必須カテゴリ』定義後に再検討。
    戻り: design_feedback と同じ {分類,該当,号線,解決案} 形式(分類にグループのタグを付す)。失敗時は空。
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
    layout = _models(layout_paths)
    items = []

    def _agg(rule, findings, tier, tag):
        if not findings:
            return
        places = [f.get('場所', '') for f in findings if f.get('場所')]
        problem = findings[0].get('問題', '')
        suggest = findings[0].get('提案', '')
        prec = _precedent(_RULE_CATEGORY.get(rule, ''))
        fix = suggest + ('（過去実績: ' + ' / '.join(prec) + '）' if prec else '')
        items.append({'分類': f'{tag}{problem[:28]}',
                      '該当': f'{len(findings)}箇所' + (f'（{places[0]} 他）' if len(places) > 1 else (f'（{places[0]}）' if places else '')),
                      '号線': '', '解決案': fix, 'tier': tier, 'rule': rule})

    # 【生成時整合性】層1: R4親無し接点(警報/補助のみ・配置図必須) — 横断検証で過検出ゼロ。
    # 配置図が無いと親機器の有無を判定できず誤発火し得るため、layout がある時のみ実行。
    try:
        r4 = _orphan_contacts_strict(seq, skel + layout) if layout else []
    except Exception:
        r4 = []
    _agg('R4', [{'場所': x['場所'],
                 '問題': f"制御図に接点があるが親機器が主回路/配置図に無い({x['場所']}:{x['cmnt']})＝変更の取り残しの疑い",
                 '提案': '回路変更で機器が削除されたなら制御図の接点も削除、必要なら主回路/配置図に機器を追加してください。'}
                for x in r4], 1, '【生成時整合性】')
    # 【参考・要確認】層2: 発見的ルール(トポロジ無し)。真の不備も拾うが誤発火し得る→人が判断。
    r2 = []
    for m in skel:
        try:
            r2 += _dc.rule_R2_capacity(m)
        except Exception:
            pass
    _agg('R2', r2, 2, '【参考・要確認】')
    r3 = []
    for m in seq + skel:
        try:
            r3 += _dc.rule_R3_neutral(m)
        except Exception:
            pass
    _agg('R3', r3, 2, '【参考・要確認】')
    r7 = []
    for m in seq + skel:
        try:
            r7 += _dc.rule_R7_earth(m)
        except Exception:
            pass
    _agg('R7', r7, 2, '【参考・要確認】')
    # 注) 端子台記載漏れ/R1配置図漏れは図面属性だけでは誤答ゼロの自動検出が不可。過去ケースは
    #     /design の参照ライブラリで人が参照する運用とする。
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
