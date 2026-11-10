# -*- coding: utf-8 -*-
"""2人目(端子番号割付)の現場確認フィードバック: 修正(差異)の保存と学習。

方針(茂泉様):
  ・現場で端子番号を確認・修正し、確認UIから『端子番号CSV(修正反映)』を出力する。
  ・CSVを取り込み、自動値と確定値の差異(＝人が直した所)を製番ごとに保存する(台帳/監査)。
  ・学習は慎重側(◎誤答ゼロ)。複数製番で同一の修正が一致した時だけ自動付番へ反映し、
    1件でも矛盾する確定があれば採用しない。既存知識は退行させない(回帰ゼロ)。

データの流れ(サーバー不要・静的HTML＋CSV方式):
  確認UI(stage_interactive)の「保存」→ ①最終端子番号リスト(次工程=シーケンス図記入用)と
  ②学習用CSV(当初値＝自動端子番号＋確定端子番号＋変更フラグ＋座標)をダウンロード →
  管理者が後日 ②を ingest_csv(保存) → learn(採用) → 自動付番に反映。
  (②は全端子を含むので順位が安定し、自動値と確定値から差分も導ける。)

保存先は HARNESS_STATE(あれば)配下に外部化し、再デプロイで巻き戻らないようにする。
採用済みの修正は terminal_rules.corrections_path() に書き出し、load_model が読み込む。

CLI:
  python -m fromto_qc.terminal_feedback ingest <csv> [<csv> ...]   # 取込・差異保存
  python -m fromto_qc.terminal_feedback learn [--min-n N]          # 一致修正を採用
  python -m fromto_qc.terminal_feedback status                     # 蓄積状況
"""
import os
import csv as _csv
import json
import glob
import time
import collections

from . import terminal_rules as TR

_HERE = os.path.dirname(os.path.abspath(__file__))
_DIRNAME = 'terminal_feedback'


def corpus_dir():
    """現場確認の確定コーパス(製番ごと)の置き場。HARNESS_STATE優先、無ければパッケージ内。"""
    st = os.environ.get('HARNESS_STATE')
    base = st if st else _HERE
    d = os.path.join(base, _DIRNAME)
    os.makedirs(d, exist_ok=True)
    return d


def _safe(s):
    return ''.join(c for c in str(s) if c.isalnum() or c in '-_.') or 'seiban'


def _read_rows(csv_path):
    """確認UIの『学習用』CSVを読む(utf-8-sig/cp932/utf-8)。列:
    製番,シート,機器,接続号線,自動端子番号,確定端子番号,変更,X,Y。
    (旧形式の 除外 列があっても後方互換で読む。)戻り: [dict,...]。"""
    for enc in ('cp932', 'utf-8-sig', 'utf-8'):
        try:
            with open(csv_path, encoding=enc, newline='') as f:
                return list(_csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    return []


def _num(v):
    try:
        return float(str(v).strip())
    except Exception:
        return 0.0


def _records_from_rows(rows):
    """CSV行(全端子)を (製番,シート,機器) でまとめ、(y降順,x昇順)で順位付けした確定記録にする。
    順位付けは確認UI/自動付番側の最終反映パスと一致(全端子で順位→drawnは上書き対象外)。
    戻り: [{seiban,sheet,dev,devtype,rank,gousen,auto,final,excluded,x,y}, ...]。"""
    groups = collections.defaultdict(list)
    for r in rows:
        seiban = (r.get('製番') or '').strip()
        sheet = (r.get('シート') or '').strip()
        dev = (r.get('機器') or '').strip()
        groups[(seiban, sheet, dev)].append(r)
    out = []
    for (seiban, sheet, dev), rs in groups.items():
        rs.sort(key=lambda r: (-_num(r.get('Y')), _num(r.get('X'))))
        for rank, r in enumerate(rs):
            out.append({
                'seiban': seiban, 'sheet': sheet, 'dev': dev,
                'devtype': TR.devtype(dev), 'rank': rank,
                'gousen': (r.get('接続号線') or '').strip(),
                'auto': (r.get('自動端子番号') or '').strip(),
                'final': (r.get('確定端子番号') or '').strip(),
                'excluded': str(r.get('除外') or '').strip() in ('1', 'true', 'True', '○'),
                'x': _num(r.get('X')), 'y': _num(r.get('Y')),
            })
    return out


def _ingest_records(recs, source='web'):
    """確定記録(_records_from_rows の出力)を製番ごとに保存する(最新で上書き)。戻り: サマリ。"""
    by_seiban = collections.defaultdict(list)
    for rec in recs:
        by_seiban[rec['seiban']].append(rec)
    saved, diffs = [], 0
    for seiban, rlist in by_seiban.items():
        diffs += sum(1 for r in rlist if r['final'] != r['auto'])
        payload = {'seiban': seiban, 'updated': time.strftime('%Y-%m-%dT%H:%M:%S'),
                   'source': source, 'records': rlist}
        p = os.path.join(corpus_dir(), _safe(seiban) + '.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        saved.append(seiban)
    return {'製番': saved, '端子数': len(recs), '差異(修正)': diffs}


def ingest_csv(csv_path):
    """確認UIの『学習用』CSVを取り込み、製番ごとの確定記録を保存する。戻り: 取込サマリ。"""
    return _ingest_records(_records_from_rows(_read_rows(csv_path)),
                           source=os.path.basename(csv_path))


def ingest_rows(matrix, source='web'):
    """確認UIがPOSTした学習用の行列(先頭行=ヘッダ, 以降=全端子行)を取り込む。
    列は学習用CSVと同じ(製番,シート,機器,接続号線,自動端子番号,確定端子番号,変更,X,Y)。"""
    head = matrix[0] if matrix else []
    dicts = [dict(zip(head, r)) for r in matrix[1:]]
    return _ingest_records(_records_from_rows(dicts), source=source)


def all_records():
    """保存済みの全製番の確定記録を返す: [(seiban, [rec,...]), ...]。"""
    out = []
    for p in sorted(glob.glob(os.path.join(corpus_dir(), '*.json'))):
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
            out.append((d.get('seiban', ''), d.get('records', [])))
        except Exception:
            continue
    return out


def learn(min_n=2, save=True):
    """蓄積した確定記録から、複数製番で一致した修正だけを採用して自動付番に反映する。

    採用規則(◎誤答ゼロ・回帰ゼロ):
      ・キー=(機器タイプ, 順位)。除外行は投票しない。
      ・そのキーで『自動値≠確定値(＝実際に人が直した)』修正を行った製番が min_n 以上、
        かつ全て同一の確定値 v で、他に v と異なる確定値(修正/確認を問わず)が
        1件も無い(矛盾ゼロ)場合にのみ、(タイプ|順位)→v を採用する。
      ・空文字 v('') は『番号なし(確定)』の修正として採用できる(例: 誤って付いた番号を消す)。
    戻り: サマリ。採用結果は terminal_rules.corrections_path() に書き出す。"""
    corrected = collections.defaultdict(lambda: collections.defaultdict(set))  # key -> v -> {製番}
    present = collections.defaultdict(lambda: collections.defaultdict(set))     # key -> v -> {製番}(全確定)
    for seiban, recs in all_records():
        for r in recs:
            if r.get('excluded'):
                continue
            key = f"{r['devtype']}|{r['rank']}"
            v = r['final']
            present[key][v].add(seiban)
            if v != r['auto']:
                corrected[key][v].add(seiban)
    term = {}
    conflicts = []
    for key, vmap in corrected.items():
        # 他に異なる確定値が存在する(矛盾)なら採用しない
        all_vals = set(present[key].keys())
        # そのキーで min_n 以上の製番が同一値 v へ修正し、かつ確定値が v のみ(矛盾ゼロ)
        cands = [v for v, sbs in vmap.items() if len(sbs) >= min_n]
        for v in cands:
            if all_vals == {v}:
                term[key] = v
            else:
                conflicts.append({'key': key, 'to': v, '他の確定値': sorted(all_vals - {v})[:6]})
    out = {'term': term,
           'stats': {'採用': len(term), '矛盾により非採用': len(conflicts),
                     '製番数': len(all_records()), 'min_n': min_n},
           'conflicts': conflicts,
           'updated': time.strftime('%Y-%m-%dT%H:%M:%S')}
    if save:
        p = TR.corrections_path()
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def status():
    """蓄積状況の要約を返す。"""
    recs = all_records()
    total = sum(len(r) for _, r in recs)
    diffs = sum(1 for _, rs in recs for r in rs
                if (not r.get('excluded')) and r.get('final') != r.get('auto'))
    cur = TR.load_corrections()
    return {'製番数': len(recs), '端子数': total, '差異(修正)': diffs,
            '採用済みの修正': len(cur.get('term', {}))}


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog='terminal_feedback')
    sub = ap.add_subparsers(dest='cmd')
    pi = sub.add_parser('ingest'); pi.add_argument('csv', nargs='+')
    pl = sub.add_parser('learn'); pl.add_argument('--min-n', type=int, default=2)
    sub.add_parser('status')
    a = ap.parse_args(argv)
    if a.cmd == 'ingest':
        for c in a.csv:
            print(c, '->', ingest_csv(c))
    elif a.cmd == 'learn':
        r = learn(min_n=a.min_n)
        print('学習(採用):', json.dumps(r['stats'], ensure_ascii=False))
        if r['conflicts']:
            print('矛盾により非採用:', json.dumps(r['conflicts'], ensure_ascii=False))
    elif a.cmd == 'status':
        print(json.dumps(status(), ensure_ascii=False))
    else:
        ap.print_help()


if __name__ == '__main__':
    import sys
    _main(sys.argv[1:])
