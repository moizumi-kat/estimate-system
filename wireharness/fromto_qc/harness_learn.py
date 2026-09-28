# -*- coding: utf-8 -*-
"""過去のハーネスデータ(台帳)から知識を学習し、新製番の生成に汎化する。

方針(茂泉様):
  ・過去のハーネスデータを元に学習し、新しい製番でも同様にハーネスデータを出力する。
  ・電気的に問題ない結果を出す。図面不備は前工程(設計)へ指摘＋解決案。

学習するもの(全て過去台帳↔図面の実データから抽出。ハードコードしない):
  1. 標準電線     … 盤種別(制御盤/分電盤)ごとの (種別,サイズ) 最頻値＝未記入時の補完既定。
  2. 盤ルール相関 … 中欠用⟺NCV / アース⟺L_EARTH 等の「台帳の要素⟺図面の特徴」の相関。
                    全製番で相関が成り立つ＝ルールとして採用してよい根拠。
  3. 命名別名     … 台帳の簡易機器名 ⟷ 図面のPARTS(部品種別)名 の対応(略称関係)。
出力: learned.json（produce_seiban が読み込んで新製番に適用）。
"""
import os
import re
import csv
import glob
import json
import collections
import ezdxf
from .geometry import norm

HERE = os.path.dirname(os.path.abspath(__file__))
LEARNED_PATH = os.path.join(HERE, 'learned.json')


def _panel_kind(files):
    """図面ファイル群 → 盤種別。結線図H系/外形図G系があれば分電盤、他は制御盤。"""
    for p in files:
        if re.search(r'-[HG]\d', os.path.basename(str(p)).upper()):
            return '分電盤'
    return '制御盤'


def _ledger_specs(txts):
    """台帳 → [(種別,サイズ,本数)] の集計 Counter[(kind,size)]。"""
    ks = collections.Counter()
    for p in txts:
        kk = sz = ''
        for r in csv.reader(open(p, encoding='cp932', errors='replace'), delimiter='\t'):
            c = [x.strip() for x in (r + [''] * 11)[:11]]
            if c[1] == '*' and c[3] not in ('', '*'):
                kk, sz = c[3], c[4]
            elif c[5] and c[5] != '*':
                ks[(kk, sz)] += 1
    return ks


def _ledger_devices(txts):
    """台帳の機器名集合(norm)。"""
    devs = set()
    for p in txts:
        for r in csv.reader(open(p, encoding='cp932', errors='replace'), delimiter='\t'):
            c = [x.strip() for x in (r + [''] * 11)[:11]]
            if c[5] and c[5] != '*':
                devs.add(norm(c[5]))
    return devs


def _drawing_parts(dxfs):
    """図面のPARTS(部品種別)名集合と、L_EARTH/NCV等の特徴フラグ。"""
    parts = set()
    feat = {'L_EARTH': False, 'NCV': False}
    for p in dxfs:
        try:
            doc = ezdxf.readfile(p)
        except Exception:
            continue
        if any(l.dxf.name == 'L_EARTH' for l in doc.layers):
            feat['L_EARTH'] = True
        for e in doc.modelspace():
            if e.dxftype() != 'INSERT' or not e.attribs:
                continue
            a = {x.dxf.tag: (x.dxf.text or '').strip() for x in e.attribs}
            if a.get('PARTS'):
                parts.add(a['PARTS'])
            blob = ' '.join(a.values())
            if '中性線欠相' in blob or 'NCV' in blob or '中欠' in blob:
                feat['NCV'] = True
    return parts, feat


def learn(seibans, train_dir, drawing_dir, save=True):
    """過去の (台帳↔図面) から知識を学習して返す。save=True で learned.json 保存。"""
    spec_by_kind = {'制御盤': collections.Counter(), '分電盤': collections.Counter()}
    alias_votes = collections.Counter()          # (台帳名, PARTS) の共起票
    rule_rows = {'中欠用': [], 'アース': []}
    for SB in seibans:
        txts = sorted(set(glob.glob(f'{train_dir}/{SB}-*.txt')) | set(glob.glob(f'{train_dir}/{SB}.txt')))
        dxfs = glob.glob(f'{drawing_dir}/{SB}-*.DXF') + glob.glob(f'{drawing_dir}/{SB}-*.dxf')
        if not txts or not dxfs:
            continue
        kind = _panel_kind(dxfs)
        spec_by_kind[kind] += _ledger_specs(txts)
        ldevs = _ledger_devices(txts)
        parts, feat = _drawing_parts(dxfs)
        part_pairs = [(pc, norm(pc)) for pc in parts if len(norm(pc)) >= 2]
        # 命名別名: 台帳名が図面PARTS名の「接頭」(略称)なら対応候補として投票。
        # 接頭に限定して部分文字列の偶然一致(ﾏﾙﾁ⊄CT等)を排除。
        for ld in ldevs:
            if len(ld) < 2:
                continue
            for pc, pn in part_pairs:
                if pn.startswith(ld) and ld != pn:
                    alias_votes[(ld, pc)] += 1
        # 盤ルール相関: 中欠用⟺NCV / アース⟺L_EARTH
        has_chu = any('中欠' in norm(d) for d in ldevs)
        rule_rows['中欠用'].append((has_chu, feat['NCV']))
        has_earth = any('ET' in norm(d) or 'アース' in norm(d) for d in ldevs)
        rule_rows['アース'].append((has_earth, feat['L_EARTH']))

    def _default(counter):
        return list(counter.most_common(1)[0][0]) if counter else ['HIV', '1.25']

    def _corr(rows):
        agree = sum(1 for a, b in rows if a == b)
        return {'一致': agree, '全体': len(rows), '相関成立': agree == len(rows)}

    # 別名は「2製番以上で共起した対応」だけ採用(保守的)
    aliases = {}
    for (ld, pc), v in alias_votes.items():
        if v >= 2:
            aliases.setdefault(ld, [])
            if pc not in aliases[ld]:
                aliases[ld].append(pc)

    knowledge = {
        'wire_spec_default': {k: _default(c) for k, c in spec_by_kind.items()},
        'wire_spec_freq': {k: [[list(t), n] for t, n in c.most_common(8)]
                           for k, c in spec_by_kind.items()},
        'panel_rules': {r: _corr(rows) for r, rows in rule_rows.items()},
        'name_aliases': aliases,
        'learned_from': [SB for SB in seibans],
    }
    if save:
        with open(LEARNED_PATH, 'w', encoding='utf-8') as f:
            json.dump(knowledge, f, ensure_ascii=False, indent=1)
    return knowledge


def learn_crimp(sheet_txt_paths, min_n=3, min_conf=0.7, save=True):
    """既存ハーネスシート(.txt)から (電線サイズ, 機器)→圧着端子サイズ の対応を学習する。

    圧着端子サイズはハーネスシートの各端点行 col3 に入る(4/5/6/6無し/3.5/つなぎ 等)。
    (サイズ,機器)ごとに最頻値を採り、件数 min_n 以上・占有率 min_conf 以上のときだけ採用
    （曖昧なものは空欄＝誤答を出さない。◎誤答ゼロ優先）。learned.json の 'crimp_table' に統合。
    キーは "サイズ|機器(norm)"、値は [圧着, 占有率, 件数]。
    """
    tally = collections.defaultdict(collections.Counter)   # (size,dev) -> Counter(col3)
    for p in sheet_txt_paths:
        try:
            raw = open(p, 'rb').read().decode('cp932', 'replace')
        except Exception:
            continue
        size_now = ''
        for r in csv.reader(raw.splitlines(), delimiter='\t'):
            c = [x.strip() for x in (r + [''] * 11)[:11]]
            if c[1] == '*':
                if c[3] not in ('', '*'):
                    size_now = c[4]
                continue
            dev = c[5]
            if dev and dev != '*':
                tally[(size_now, norm(dev))][c[3]] += 1
    table = {}
    for (sz, dev), cnt in tally.items():
        n = sum(cnt.values())
        val, v = cnt.most_common(1)[0]
        conf = v / n if n else 0
        if n >= min_n and conf >= min_conf:
            table[f'{sz}|{dev}'] = [val, round(conf, 3), n]
    if save:
        k = load()
        k['crimp_table'] = table
        with open(LEARNED_PATH, 'w', encoding='utf-8') as f:
            json.dump(k, f, ensure_ascii=False, indent=1)
    return table


def crimp_of(size, device):
    """学習済み (電線サイズ, 機器)→圧着端子サイズ。無ければ ''（空欄＝出さない）。"""
    t = load().get('crimp_table', {})
    v = t.get(f'{size}|{norm(device)}')
    return v[0] if v else ''


def wire_type(kind, gousen='', color=''):
    """種別ラベル(ハーネスシート col3 のヘッダ)。アース=緑、他は kind(既定 HIV)。"""
    if (color or '') == '緑' or str(gousen or '').upper().startswith('E') or (kind or '').upper() in ('EARTH', 'E'):
        return '緑'
    return kind or 'HIV'


def load():
    """learned.json を読む。無ければ空。"""
    try:
        return json.load(open(LEARNED_PATH, encoding='utf-8'))
    except Exception:
        return {}


def default_wire(kind):
    """学習済みの盤種別ごと標準電線 (種別,サイズ)。無ければ HIV/1.25。"""
    k = load().get('wire_spec_default', {}).get(kind)
    return tuple(k) if k else ('HIV', '1.25')
