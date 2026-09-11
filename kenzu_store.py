# -*- coding: utf-8 -*-
"""図面検図システム専用のデータストア（現場用アプリ kenzu_app 専用）。

積算コード選定システムのデータ(db.json 等)とは完全に別の置き場に、
検図の実行結果と設計からのフィードバックを蓄積する。これが
「追加で起きる不具合をカウント→ルール/システムをバージョンアップ」の土台。

- 保存先: 環境変数 KENZU_DATA_DIR（未設定なら <repo>/kenzu_data）。
  本番は /var/lib/kenzu-system 等、リポジトリ外の永続領域を推奨。
- 依存なし(標準ライブラリのみ)・ファイルベース(JSON/JSONL)。
  runs/<run_id>.json … 各検図実行の入出力
  feedback.jsonl     … 設計の判定(是正/誤検知/見逃し)を1行1件で追記
"""
import os
import io
import json
import time
import uuid
import threading
import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('KENZU_DATA_DIR') or os.path.join(_HERE, 'kenzu_data')
RUNS_DIR = os.path.join(DATA_DIR, 'runs')
FEEDBACK_PATH = os.path.join(DATA_DIR, 'feedback.jsonl')

_lock = threading.Lock()

# 設計の判定種別
DISPOSITIONS = ('fixed', 'false_positive', 'missed')
DISPOSITION_JA = {'fixed': '是正(正しい指摘)', 'false_positive': '誤検知', 'missed': '見逃し'}


def _ensure():
    os.makedirs(RUNS_DIR, exist_ok=True)


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def save_run(meta, findings):
    """1回の検図実行を保存し、各指摘に安定ID(fid)を振って返す。
    戻り: (run_id, findings_with_fid)"""
    _ensure()
    run_id = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    out = []
    for i, f in enumerate(findings):
        g = dict(f)
        g['fid'] = str(i)
        out.append(g)
    rec = {'run_id': run_id, 'ts': _now(), 'meta': meta, 'findings': out}
    path = os.path.join(RUNS_DIR, run_id + '.json')
    with _lock:
        with open(path, 'w', encoding='utf-8') as fp:
            json.dump(rec, fp, ensure_ascii=False, indent=1)
    return run_id, out


def get_run(run_id):
    path = os.path.join(RUNS_DIR, os.path.basename(run_id) + '.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as fp:
        return json.load(fp)


def add_feedback(run_id, disposition, rule=None, fid=None, note='', user=''):
    """設計の判定を1件追記する。
      fixed/false_positive … 既存の指摘(run_id+fid)に対する判定
      missed               … ツールが出せなかった不具合(fid不要、rule/noteで記述)
    """
    if disposition not in DISPOSITIONS:
        raise ValueError(f'disposition は {DISPOSITIONS} のいずれか')
    # fid が指定されていれば、その指摘の rule を補完
    if run_id and fid is not None and not rule:
        r = get_run(run_id)
        if r:
            for f in r.get('findings', []):
                if f.get('fid') == str(fid):
                    rule = f.get('rule')
                    break
    rec = {'ts': _now(), 'run_id': run_id, 'fid': (str(fid) if fid is not None else None),
           'rule': rule, 'disposition': disposition, 'note': note, 'user': user}
    _ensure()
    with _lock:
        with open(FEEDBACK_PATH, 'a', encoding='utf-8') as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec


def _iter_feedback():
    if not os.path.exists(FEEDBACK_PATH):
        return
    with open(FEEDBACK_PATH, encoding='utf-8') as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def stats():
    """バージョンアップ判断用の集計。
    ルール別に 是正/誤検知/見逃し の件数と、実行回数・総指摘数を返す。"""
    by_rule = {}
    by_disp = {d: 0 for d in DISPOSITIONS}
    total_fb = 0
    for r in _iter_feedback():
        d = r.get('disposition')
        rule = r.get('rule') or '(不明)'
        by_rule.setdefault(rule, {x: 0 for x in DISPOSITIONS})
        if d in by_disp:
            by_disp[d] += 1
            by_rule[rule][d] += 1
            total_fb += 1
    # 実行回数・総指摘数
    n_runs = 0
    n_findings = 0
    if os.path.isdir(RUNS_DIR):
        for fn in os.listdir(RUNS_DIR):
            if fn.endswith('.json'):
                n_runs += 1
                try:
                    with open(os.path.join(RUNS_DIR, fn), encoding='utf-8') as fp:
                        n_findings += len(json.load(fp).get('findings', []))
                except Exception:
                    pass
    # 適合率(是正/(是正+誤検知)) … 誤検知が多いルールはチューニング候補
    for rule, c in by_rule.items():
        judged = c['fixed'] + c['false_positive']
        c['precision'] = round(c['fixed'] / judged, 3) if judged else None
    return {
        'data_dir': DATA_DIR,
        'runs': n_runs,
        'findings_total': n_findings,
        'feedback_total': total_fb,
        'by_disposition': by_disp,
        'by_rule': by_rule,
        'missed_total': by_disp['missed'],
    }
