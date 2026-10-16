# -*- coding: utf-8 -*-
"""統合 細物ハーネス From-To ドラフト生成(半自動モデル)。

対象は細物(≤5.5sq=2人目・3人目の工程)。太物(≥8sq=1人目)は別工程(⑤⑥)で対象外。
幾何だけでは全数の完全自動は難しいため、生成した配線を『確度』でランク付けして出し、
最終判断は人(確認UI)で行う=◎誤答ゼロは人の確認ゲートで担保。確度は正誤の保証ではなく、
確認の優先順位(確度:高から確認すれば労力最小)。

確度(10製番の④で適合率を実測し設定):
  確度:高   … 複数の独立手法が同一配線を生成(相互裏取り)。最も確からしい。
  確度:中   … 単一のルール生成(主回路 LUG→遮断器→TB相, SPD分離器の定型分岐)。
              製番構造(多盤・母線)で外れることがあるため人が採否。
  確度:精査 … 実配線トレース単独(端子名が不確実)・TB台番号/接地(物理体系差)。重点確認。
各配線 dict: {from,to,sources(list),tier,phase?}。build() で統合・重複排除して返す。
"""
import glob
import os
import re

from . import main_circuit
from . import fixed_wiring
from . import instrument_wiring
from . import edge_trace
from . import device_norm as dn
from . import tb_strip
from . import seiban_config
from . import cross_learn

TIER_HI = '確度:高'
TIER_MID = '確度:中'
TIER_LOW = '確度:精査'


def _layout(seiban_dir):
    return (glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.DXF')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*D0*.dxf')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*E*.DXF')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*E*.dxf')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.DXF')) +
            glob.glob(os.path.join(seiban_dir, '図面データ_*G0*.dxf')))


def _seq(seiban_dir):
    # 制御盤はF系(シーケンス)、分電盤はH系(結線図)に制御(警報回路等)が載る。両方を対象にする。
    pats = ('図面データ_*F*.DXF', '図面データ_*F*.dxf',
            '図面データ_*H*.DXF', '図面データ_*H*.dxf')
    out = []
    for pat in pats:
        out += glob.glob(os.path.join(seiban_dir, pat))
    return out


_RANK = {TIER_HI: 0, TIER_MID: 1, TIER_LOW: 2}


def _circuit_of(sym):
    """機器記号から回路番号(3桁 or AL系)を取り出す。52-101→'101', 86X-103→'103', GL-102→'102'。
    事例分析: 制御配線は回路ごとに同じTB台へ載る(回路順に連番の台)。台番号の絶対値は製番依存で
    確定できないが、回路でのグルーピングは確実=レビューを回路単位にまとめられる。"""
    s = str(sym or '')
    m = re.search(r'(AL\d+|\d{3})(?!.*\d)', s)      # 末尾寄りの3桁 or AL番号
    return m.group(1) if m else None


def _main_tier(w):
    """main_circuit 1配線の単独確度。LUG→遮断器=中(TB台番号非依存)、TB相/接地=精査(体系差)。"""
    fr, to = w.get('from', ''), w.get('to', '')
    if fr.startswith('LUG') or to.startswith('LUG'):
        return TIER_MID
    return TIER_LOW


def _key(fr, to):
    return frozenset((dn.canon(fr.split(':', 1)[0]) + '|' + (fr.split(':', 1) + [''])[1],
                      dn.canon(to.split(':', 1)[0]) + '|' + (to.split(':', 1) + [''])[1]))


def build(seiban_dir, seiban=None):
    """製番フォルダ → 統合ドラフト。戻り: {'wires':[...], 'summary':{確度:件数}}。
    各 wire: {'from','to','sources'(list),'tier','phase'?}。
    確度: 人の確認済 or 複数ソース一致=高。単独は内容別(LUG→遮断器/SPD=中, TB相/接地/トレース=精査)。
    seiban: 確認済設定(seiban_config)を引くキー。未指定ならフォルダ名を使う(学習ループ)。"""
    layout = _layout(seiban_dir)
    seq = _seq(seiban_dir)
    seiban = seiban or os.path.basename(seiban_dir.rstrip('/'))
    try:
        confirmed_tb = seiban_config.tb_assign(seiban)     # {回路: 確認済TB台番号}
    except Exception:
        confirmed_tb = {}
    try:
        confirmed_pins = seiban_config.pin_confirm(seiban)  # {'機器:a?': 確認済ピン}
    except Exception:
        confirmed_pins = {}
    try:
        style = seiban_config.get_style(seiban)             # 流儀(作業者)。あれば同流儀だけで学習
    except Exception:
        style = None
    try:
        learned_pins = cross_learn.learned_pins(style=style)  # {(機器型,マーカー): ピン} 横展開
    except Exception:
        learned_pins = {}

    def _pin(dev, term):
        """接点マーカー(a?/b?)を置換。確認済(この製番)＞横展開(複数製番で一致)＞未確認。
        戻り: (端子, 状態 'confirmed'/'learned'/None)。"""
        v = confirmed_pins.get(f'{dev}:{term}')
        if v:
            return v, 'confirmed'
        lv = learned_pins.get((cross_learn.device_type(dev), term))
        if lv:
            return lv, 'learned'
        return term, None
    acc = {}        # key -> {'from','to','sources':set,'tiers':set,'phase'}

    def add(fr, to, source, base_tier, **extra):
        k = _key(fr, to)
        if k not in acc:
            acc[k] = {'from': fr, 'to': to, 'sources': set(), 'tiers': set(), **extra}
        acc[k]['sources'].add(source)
        acc[k]['tiers'].add(base_tier)
        for kk, vv in extra.items():
            acc[k].setdefault(kk, vv)

    try:
        strip_map = tb_strip.assign(layout)      # 回路 → 主回路TB台(=制御TBと同じ台)
    except Exception:
        strip_map = {}

    for w in main_circuit.generate(layout):
        add(w['from'], w['to'], 'main_circuit', _main_tier(w), phase=w.get('phase'))
    for w in fixed_wiring.generate(layout):
        add(w['from'], w['to'], 'fixed_wiring', TIER_MID, phase=w.get('phase'))
    for w in instrument_wiring.generate(layout):      # 計器配線(CT→AM定型・現場確認済)
        add(w['from'], w['to'], 'instrument_wiring', TIER_MID)
    _PRIO = {'confirmed': 2, 'learned': 1, None: 0}
    for p in seq:
        try:
            _edges, _, pos_edges = edge_trace.trace_edges(p, with_pos=True)
        except Exception:
            continue
        for (da0, ta0, xa, ya), (db0, tb0, xb, yb) in pos_edges:
            ends = [(da0, ta0), (db0, tb0)]
            pos = [(xa, ya), (xb, yb)]              # 物理位置(同一シート内)。名前=流儀の下の正本。
            status = None                           # 適用したピンの最強状態
            res = []
            for (d, t) in ends:
                nt, st = _pin(d, t)
                res.append((d, nt))
                if _PRIO[st] > _PRIO[status]:
                    status = st
            ends = res
            qi = [i for i in (0, 1) if ends[i][1] == '?']
            if not qi:                              # 両端とも端子名あり=通常の渡り
                (da, ta), (db, tb) = ends
                src, tier = ({'confirmed': ('confirmed', TIER_HI),
                              'learned': ('learned', TIER_MID)}
                             .get(status, ('edge_trace', TIER_LOW)))
                add(f'{da}:{ta}', f'{db}:{tb}', src, tier,
                    from_pos=pos[0], to_pos=pos[1])
            elif len(qi) == 1:
                q, o = qi[0], 1 - qi[0]
                # 片端が TB で台番号のみ未定、相手端は解決済み → 可視化(台番号は2人目が割当)。
                # ◎誤答ゼロ: TB台番号は図面に無いため確定せず『要確認』として出す。
                if str(ends[q][0]).upper().startswith('TB'):
                    # 発見ルール(④検証済): 制御TB = その回路の主回路TB台(同じ台)。
                    # 回路でグルーピングし『TB:回路NNN』で出す(回路単位で割当できる)。
                    # 絶対台番号は tb_strip 予測に律速され未確定のため、誤答ゼロを優先し数字は
                    # From-To に入れない。参考として主回路台予測を tb_hint に保持(表示は任意)。
                    circ = _circuit_of(ends[o][0])
                    conf_blk = confirmed_tb.get(circ) if circ else None
                    if conf_blk:
                        # 学習ループ: 人が確認済の台番号を自動反映(確度:高)。
                        add(f'{ends[o][0]}:{ends[o][1]}', f'TB-{conf_blk}:回路{circ}',
                            'confirmed', TIER_HI, tb_pending=True,
                            from_pos=pos[o], to_pos=pos[q])
                    else:
                        # 確定ルール(制御TB=その回路の主回路TB台)で、主回路台(tb_strip)から
                        # 台番号を予測して出す。番号自体は tb_strip 精度に依るため『予測・要確認』。
                        blk = strip_map.get(circ) if circ else None
                        if blk:
                            tb_to = f'TB-{blk}:回路{circ}(予測)'
                        elif circ:
                            tb_to = f'TB:回路{circ}'
                        else:
                            tb_to = 'TB:台番号要確認'
                        add(f'{ends[o][0]}:{ends[o][1]}', tb_to,
                            'edge_trace', TIER_LOW, tb_pending=True,
                            from_pos=pos[o], to_pos=pos[q])
                # 非TBの'?'(動力用多接点など)は確定できないので出さない

    wires = []
    summary = {TIER_HI: 0, TIER_MID: 0, TIER_LOW: 0}
    for v in acc.values():
        srcs = sorted(v['sources'])
        if len(srcs) >= 2:
            tier = TIER_HI                              # 複数手法が一致=相互裏取り
        else:
            tier = min(v['tiers'], key=lambda t: _RANK[t])   # 単独は最良の内容別確度
        w = {'from': v['from'], 'to': v['to'], 'sources': srcs, 'tier': tier}
        if v.get('phase'):
            w['phase'] = v['phase']
        if v.get('from_pos') and v.get('to_pos'):     # 物理位置(同一シート内)を正本として併記
            w['from_pos'] = v['from_pos']
            w['to_pos'] = v['to_pos']
        wires.append(w)
        summary[tier] += 1
    return {'wires': wires, 'summary': summary}


def to_rows(draft):
    """ドラフト → 表示用の行(確度順)。[(tier, sources, from, to)]。"""
    order = {TIER_HI: 0, TIER_MID: 1, TIER_LOW: 2}
    rows = sorted(draft['wires'], key=lambda w: (order[w['tier']], ','.join(w['sources'])))
    return [(w['tier'], ','.join(w['sources']), w['from'], w['to']) for w in rows]


def to_csv(draft, path):
    """ドラフト → CSV(確度順)。列: 確度,ソース,From,To。cp932(Excel)で保存。"""
    import csv
    with open(path, 'w', encoding='cp932', errors='replace', newline='') as f:
        w = csv.writer(f)
        w.writerow(['確度', 'ソース', 'From', 'To'])
        for tier, src, fr, to in to_rows(draft):
            w.writerow([tier, src, fr, to])
    return path


_TIER_STYLE = {
    TIER_HI:  ('#1b7f3b', '#e6f4ea'),   # 緑: 複数手法一致
    TIER_MID: ('#9a6400', '#fdf3e0'),   # 橙: 単一ルール生成
    TIER_LOW: ('#7a2d2d', '#f6e9e9'),   # 赤茶: 要精査
}


def to_html(draft, path, seiban=''):
    """ドラフト → 確度で色分けした人間レビュー用HTML(確度順)。確認UIの土台(閲覧)。

    ◎誤答ゼロの運用: 確度:高から確認すれば労力最小。各行はソース(生成手法)も表示し、
    人が採否できるよう From/To を並べる。配線本数と確度内訳を先頭に出す。"""
    import html as _h
    s = draft['summary']
    total = sum(s.values())
    head_cells = ''.join(
        f'<span class="chip" style="color:{_TIER_STYLE[t][0]};background:{_TIER_STYLE[t][1]}">'
        f'{t}: {s.get(t, 0)}</span>'
        for t in (TIER_HI, TIER_MID, TIER_LOW))
    body = []
    for tier, src, fr, to in to_rows(draft):
        fg, bg = _TIER_STYLE.get(tier, ('#333', '#fff'))
        body.append(
            f'<tr style="background:{bg}"><td style="color:{fg};font-weight:bold">{_h.escape(tier)}</td>'
            f'<td class="src">{_h.escape(src)}</td>'
            f'<td>{_h.escape(fr)}</td><td class="arr">→</td><td>{_h.escape(to)}</td></tr>')
    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>ハーネスFrom-Toドラフト {_h.escape(seiban)}</title><style>
 body{{font-family:'Noto Sans JP','IPAGothic',sans-serif;margin:18px;color:#111;font-size:13px}}
 h1{{font-size:17px;margin:0 0 4px}} .sub{{color:#555;margin:0 0 12px}}
 .chip{{display:inline-block;padding:3px 10px;margin-right:8px;border-radius:12px;font-weight:bold}}
 table{{border-collapse:collapse;width:100%;margin-top:10px}}
 th,td{{border:1px solid #ddd;padding:4px 8px;text-align:left}}
 th{{background:#1f3b63;color:#fff;position:sticky;top:0}}
 td.src{{color:#555;font-size:11px}} td.arr{{text-align:center;color:#888}}
 .note{{font-size:11px;color:#666;margin-top:10px}}</style></head><body>
<h1>ハーネス From-To ドラフト（半自動・要確認）</h1>
<p class="sub">製番: {_h.escape(seiban)}　／　生成 {total} 本</p>
<div>{head_cells}</div>
<table><thead><tr><th>確度</th><th>ソース</th><th>From</th><th></th><th>To</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>
<p class="note">確度:高=複数手法が一致(相互裏取り)／確度:中=単一ルール生成(主回路・SPD分離器)／
確度:精査=実配線トレース単独・TB台番号/接地。◎誤答ゼロ: 確定は人の確認で担保。
確度:高→中→精査 の順で確認すると労力最小。</p>
</body></html>"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(doc)
    return path


def open_confirmations(draft):
    """ドラフト → まだ人の確認が要る項目。戻り: {'tb':{回路:主回路台hint}, 'pins':[(機器,種別)...]}。
    tb=『TB:回路NNN』で台番号未確定の回路。pins=『機器:a?/b?』で番号未確定の接点。"""
    tb, pins = {}, set()
    for w in draft['wires']:
        to = w.get('to', '')
        # 台番号が未確定/予測のもの('TB:回路NNN' か 'TB-<台>:回路NNN(予測)')が確認対象。
        # 確認済('TB-<台>:回路NNN' で予測でない)は確度:高なので対象外。
        m = re.match(r'TB(?:-(\d+))?:回路([^(]+)', to)
        if m and ('(予測)' in to or to.startswith('TB:回路')):
            tb.setdefault(m.group(2).strip(), m.group(1))   # 予測台番号(あれば)をヒントに
        for f in (w.get('from', ''), w.get('to', '')):
            m2 = re.match(r'(.+):(a\?|b\?|aL\?|bL\?|c\?)$', f)
            if m2:
                pins.add((m2.group(1), m2.group(2)))
    return {'tb': tb, 'pins': sorted(pins)}


_MARK_JA = {'a?': 'a接点(入)', 'b?': 'b接点(切)', 'aL?': 'a接点(警報)',
            'bL?': 'b接点(警報)', 'c?': 'c接点(切替)'}


def worksheet_html(seiban_dir, path, seiban=None):
    """製番 → 確認ワークシートHTML。確認が要る項目だけを記入欄つきで出す(回路→台番号/接点→ピン)。
    末尾に『貼り付け用テンプレート』を付け、記入してチャットで返せば apply_confirmations_text で保存できる。"""
    import html as _h
    seiban = seiban or os.path.basename(seiban_dir.rstrip('/'))
    oc = open_confirmations(build(seiban_dir, seiban))
    tb_rows = ''.join(
        f'<tr><td>回路 {_h.escape(c)}</td><td>TB-<input style="width:4em"></td>'
        f'<td class="hint">{("参考: 主回路台≈"+h) if h else ""}</td></tr>'
        for c, h in sorted(oc['tb'].items()))
    pin_rows = ''.join(
        f'<tr><td>{_h.escape(dev)}</td><td>{_MARK_JA.get(mk, mk)}</td>'
        f'<td>ピン <input style="width:4em"></td></tr>'
        for dev, mk in oc['pins'])
    tpl = ('# 記入して返信してください（分かる範囲で）\n'
           '[TB] ' + ', '.join(f'回路{c}=' for c in sorted(oc['tb'])) + '\n'
           '[PIN] ' + ', '.join(f'{d}:{m}=' for d, m in oc['pins']))
    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>確認ワークシート {_h.escape(seiban)}</title><style>
 body{{font-family:'Noto Sans JP','IPAGothic',sans-serif;margin:18px;color:#111;font-size:13px}}
 h1{{font-size:17px}} h2{{font-size:14px;background:#1f3b63;color:#fff;padding:4px 8px;border-radius:4px}}
 table{{border-collapse:collapse;margin:8px 0}} th,td{{border:1px solid #ddd;padding:4px 10px}}
 td.hint{{color:#777;font-size:11px}} .tpl{{background:#f2f5fa;border:1px solid #ccd;padding:8px;
 white-space:pre-wrap;font-family:monospace;font-size:12px}} .note{{color:#555;font-size:12px}}</style></head><body>
<h1>確認ワークシート — {_h.escape(seiban)}</h1>
<p class="note">分かる範囲で結構です。記入後、下の「貼り付け用テンプレート」を埋めて返信いただければ保存し、
次回から自動で埋まります（学習ループ）。</p>
<h2>① 制御TBの台番号（回路ごとに1つ決めれば、その回路の全配線に反映）</h2>
<table><thead><tr><th>回路</th><th>台番号</th><th></th></tr></thead><tbody>{tb_rows or '<tr><td colspan=3>なし</td></tr>'}</tbody></table>
<h2>② 接点の端子番号（a?/b? の実番号）</h2>
<table><thead><tr><th>機器</th><th>接点</th><th>端子番号</th></tr></thead><tbody>{pin_rows or '<tr><td colspan=3>なし</td></tr>'}</tbody></table>
<h2>貼り付け用テンプレート（これを埋めて返信）</h2>
<div class="tpl">{_h.escape(tpl)}</div>
</body></html>"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(doc)
    return path


def apply_confirmations_text(seiban, text):
    """ワークシートの『貼り付け用テンプレート』記入文字列を解釈して保存する。
    形式: '[TB] 回路210=26, 回路211=27' / '[PIN] 52-101:a?=13'。空欄は無視。戻り: 適用件数。"""
    tb, pins = {}, {}
    for line in str(text or '').splitlines():
        body = line.split(']', 1)[1] if ']' in line else line
        is_tb = '[TB]' in line
        is_pin = '[PIN]' in line
        for item in body.split(','):
            if '=' not in item:
                continue
            k, v = item.split('=', 1)
            k, v = k.strip(), v.strip()
            if not v:
                continue
            if is_tb:
                tb[re.sub(r'^回路', '', k)] = v
            elif is_pin:
                pins[k] = v
    seiban_config.record_decision(seiban, tb_assign=tb or None, pin_confirm=pins or None)
    return {'tb': len(tb), 'pins': len(pins)}


def write_drafts(seiban_dirs, out_dir):
    """複数製番 → 各製番のHTML/CSV＋一覧 index.html を out_dir に出力。戻り: index のパス。
    半自動レビューの実運用: index から各製番を開き、確度順に採否する。"""
    import html as _h
    import os
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for d in seiban_dirs:
        name = os.path.basename(d.rstrip('/'))
        try:
            dr = build(d)
        except Exception:
            continue
        base = name.replace('/', '_').replace('（', '(').replace('）', ')')
        to_html(dr, os.path.join(out_dir, base + '.html'), seiban=name)
        to_csv(dr, os.path.join(out_dir, base + '.csv'))
        rows.append((name, base, dr['summary']))
    tot = {TIER_HI: 0, TIER_MID: 0, TIER_LOW: 0}
    trs = []
    for name, base, s in rows:
        for k in tot:
            tot[k] += s.get(k, 0)
        n = sum(s.values())
        trs.append(
            f'<tr><td><a href="{_h.escape(base)}.html">{_h.escape(name)}</a></td>'
            f'<td class="n">{s.get(TIER_HI, 0)}</td><td class="n">{s.get(TIER_MID, 0)}</td>'
            f'<td class="n">{s.get(TIER_LOW, 0)}</td><td class="n">{n}</td>'
            f'<td><a href="{_h.escape(base)}.csv">CSV</a></td></tr>')
    grand = sum(tot.values())
    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>ハーネスFrom-Toドラフト 一覧</title><style>
 body{{font-family:'Noto Sans JP','IPAGothic',sans-serif;margin:18px;color:#111;font-size:13px}}
 h1{{font-size:18px}} table{{border-collapse:collapse;margin-top:10px}}
 th,td{{border:1px solid #ddd;padding:5px 12px}} th{{background:#1f3b63;color:#fff}}
 td.n{{text-align:right}} a{{color:#1f3b63}}</style></head><body>
<h1>ハーネス From-To ドラフト一覧（半自動・要確認）</h1>
<p>{len(rows)} 製番 / 生成 {grand} 本（確度:中 {tot[TIER_MID]} ・ 精査 {tot[TIER_LOW]} ・ 高 {tot[TIER_HI]}）</p>
<table><thead><tr><th>製番</th><th>確度:高</th><th>確度:中</th><th>確度:精査</th><th>計</th><th></th></tr></thead>
<tbody>{''.join(trs)}</tbody></table>
<p style="font-size:11px;color:#666">各製番を開き、確度:高→中→精査の順に採否してください。確定は人の確認で担保（◎誤答ゼロ）。</p>
</body></html>"""
    ip = os.path.join(out_dir, 'index.html')
    with open(ip, 'w', encoding='utf-8') as f:
        f.write(doc)
    return ip
