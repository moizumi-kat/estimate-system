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
# 学習結果(可変状態)の置き場所。環境変数 HARNESS_STATE を指定すると永続領域へ外部化する
# (AWS/EC2 の再デプロイ=git pull でコード木が更新されても運用中の学習が巻き戻らない)。
# 未指定ならパッケージ内(従来どおり=ローカル挙動は不変)。初回は同梱のリポジトリ値からシード。
_STATE = os.environ.get('HARNESS_STATE')


def _state_path(name):
    if _STATE:
        os.makedirs(_STATE, exist_ok=True)
        dst = os.path.join(_STATE, name)
        src = os.path.join(HERE, name)
        if not os.path.exists(dst) and os.path.exists(src):
            try:
                import shutil
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            except Exception:
                pass
        return dst
    return os.path.join(HERE, name)


LEARNED_PATH = _state_path('learned.json')
# 人手で確定(承認)したハーネスシートの蓄積先。確認UIの「確定・出力＋学習」で追記され、
# ここから圧着等を再学習して learned.json に反映する(運用で賢くなる)。
CONFIRMED_DIR = _state_path('confirmed')


def _read_text(path):
    """確定コーパス等のテキストを文字コード自動判定で読む(utf-8-sig→cp932→utf-8)。"""
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'cp932', 'utf-8'):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('cp932', 'replace')


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
    # 機器横断フォールバック: 圧着端子サイズ≒ネジ径は機器の端子で決まり、電線サイズにほぼ非依存。
    # (サイズ,機器) に該当が無い時のため、機器ごとの最頻圧着も学習(空欄も1票として扱う)。
    by_dev = collections.defaultdict(collections.Counter)
    for (sz, dev), cnt in tally.items():
        by_dev[dev] += cnt
    dev_table = {}
    for dev, cnt in by_dev.items():
        n = sum(cnt.values())
        val, v = cnt.most_common(1)[0]
        conf = v / n if n else 0
        if n >= min_n and conf >= min_conf:
            dev_table[dev] = [val, round(conf, 3), n]
    if save:
        k = load()
        k['crimp_table'] = table
        k['crimp_by_device'] = dev_table
        with open(LEARNED_PATH, 'w', encoding='utf-8') as f:
            json.dump(k, f, ensure_ascii=False, indent=1)
    return table


# ---------------------------------------------------------------------------
# 出力の修正を受け付けて学習する(運用学習) — 確認UIの「確定・出力＋学習」から呼ぶ
# ---------------------------------------------------------------------------

def _crimp_tally_from_text(sheet_texts):
    """ハーネスシート本文(文字列)群 → (電線サイズ,機器)→Counter(圧着) の集計。"""
    tally = collections.defaultdict(collections.Counter)
    for raw in sheet_texts:
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
    return tally


def _tables_from_tally(tally, min_n, min_conf):
    """集計 → (crimp_table, crimp_by_device)。件数 min_n 以上・占有率 min_conf 以上のみ採用。"""
    table = {}
    for (sz, dev), cnt in tally.items():
        n = sum(cnt.values())
        val, v = cnt.most_common(1)[0]
        conf = v / n if n else 0
        if n >= min_n and conf >= min_conf:
            table[f'{sz}|{dev}'] = [val, round(conf, 3), n]
    by_dev = collections.defaultdict(collections.Counter)
    for (sz, dev), cnt in tally.items():
        by_dev[dev] += cnt
    dev_table = {}
    for dev, cnt in by_dev.items():
        n = sum(cnt.values())
        val, v = cnt.most_common(1)[0]
        conf = v / n if n else 0
        if n >= min_n and conf >= min_conf:
            dev_table[dev] = [val, round(conf, 3), n]
    return table, dev_table


def confirmed_paths():
    """確定コーパス(人手承認済みハーネスシート)のパス一覧。"""
    return sorted(glob.glob(os.path.join(CONFIRMED_DIR, '*.txt')))


def _safe_name(s):
    return ''.join(c for c in str(s) if c.isalnum() or c in '-_') or 'harness'


def record_confirmed_sheet(seiban, rows_11):
    """人手で確定したハーネスシート(11列の行リスト)を確定コーパスに保存(製番ごと最新で上書き)。"""
    os.makedirs(CONFIRMED_DIR, exist_ok=True)
    p = os.path.join(CONFIRMED_DIR, _safe_name(seiban) + '.txt')
    lines = ['\t'.join('' if x is None else str(x) for x in r) for r in rows_11]
    with open(p, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('\r\n'.join(lines) + '\r\n')
    return p


def learn_corrections(corrections, save=True):
    """人手の『修正(diff)』だけを権威データとして学習する(確認UIの確定時)。

    確定シート"全体"ではなく、自動生成値と異なるセル(＝人が直した所)だけを学習する。
    これにより、1製番の自動生成値が歴史的コーパス由来の既存知識を退行させることを防ぐ(回帰ゼロ)。
      ・圧着(crimp)の修正 → (サイズ|機器) を正解として上書き学習(占有率1.0=権威)。
      ・(機器) 粗いフォールバックは、既知機器は温存し、未知機器のみ・修正が一意のとき追加。
      ・同一キーに矛盾する修正があれば採らない(誤答ゼロ)。
    corrections: [{'field':'crimp','size':..,'device':..,'to':..}, ...]
    """
    k = load()
    ct = dict(k.get('crimp_table', {}))
    cd = dict(k.get('crimp_by_device', {}))
    bykey = collections.defaultdict(collections.Counter)
    bydev = collections.defaultdict(collections.Counter)
    for c in corrections or []:
        if c.get('field') != 'crimp':
            continue
        sz = (c.get('size') or '').strip()
        dev = norm(c.get('device') or '')
        val = (c.get('to') or '').strip()
        if not dev:
            continue
        bykey[(sz, dev)][val] += 1
        bydev[dev][val] += 1
    applied = []
    for (sz, dev), cnt in bykey.items():
        if len(cnt) != 1:                 # 矛盾する修正は採らない
            continue
        val = next(iter(cnt))
        key = f'{sz}|{dev}'
        before = ct.get(key, ['', 0, 0])[0]
        if before == val:
            continue
        ct[key] = [val, 1.0, int(sum(cnt.values()))]   # 権威: 占有率1.0
        applied.append({'key': key, 'before': before, 'after': val})
    dev_added = 0
    for dev, cnt in bydev.items():
        if dev in cd or len(cnt) != 1:    # 既知機器は温存、矛盾は不可
            continue
        val = next(iter(cnt))
        cd[dev] = [val, 1.0, int(sum(cnt.values()))]
        dev_added += 1
    k['crimp_table'] = ct
    k['crimp_by_device'] = cd
    if save:
        with open(LEARNED_PATH, 'w', encoding='utf-8') as f:
            json.dump(k, f, ensure_ascii=False, indent=1)
    return {'修正学習(サイズ|機器)': len(applied),
            '追加(機器)': dev_added, '変更点': applied}


def relearn_from_confirmed(min_n=3, min_conf=0.7, save=True):
    """確定コーパス(全体)から圧着を再学習し、既存 learned.json へ"追加のみ"で反映する(管理/バッチ用)。

    既存キーは一切上書きしない(回帰ゼロ)。確定シートで新しく現れた (サイズ,機器) の
    うち空欄でない値のみ追加する。人手修正の反映は learn_corrections で行う(こちらは権威上書き)。
    """
    paths = confirmed_paths()
    texts = []
    for p in paths:
        try:
            texts.append(_read_text(p))
        except Exception:
            continue
    tally = _crimp_tally_from_text(texts)
    newtab, newdev = _tables_from_tally(tally, min_n, min_conf)
    k = load()
    base_t = dict(k.get('crimp_table', {}))
    base_d = dict(k.get('crimp_by_device', {}))
    added = 0
    for key, val in newtab.items():
        if key not in base_t and val[0] != '':     # 新規かつ非空のみ追加
            base_t[key] = val
            added += 1
    added_dev = 0
    for key, val in newdev.items():
        if key not in base_d and val[0] != '':
            base_d[key] = val
            added_dev += 1
    k['crimp_table'] = base_t
    k['crimp_by_device'] = base_d
    k['confirmed_count'] = len(paths)
    if save:
        with open(LEARNED_PATH, 'w', encoding='utf-8') as f:
            json.dump(k, f, ensure_ascii=False, indent=1)
    return {'確定シート数': len(paths), '追加(サイズ|機器)': added, '追加(機器)': added_dev}


def crimp_of(size, device):
    """学習済み 圧着端子サイズ。まず (サイズ,機器)、無ければ (機器)横断。共に無ければ ''。"""
    k = load()
    v = k.get('crimp_table', {}).get(f'{size}|{norm(device)}')
    if v:
        return v[0]
    d = k.get('crimp_by_device', {}).get(norm(device))
    return d[0] if d else ''


def crimp_known(size, device):
    """圧着が学習で確定できるか((サイズ,機器) or (機器)横断で該当)。"""
    k = load()
    return (f'{size}|{norm(device)}' in k.get('crimp_table', {})
            or norm(device) in k.get('crimp_by_device', {}))


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
