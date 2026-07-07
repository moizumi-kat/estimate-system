#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""弱電端子盤(62系)の選定改善。
問題: 見積書の端子盤は「端子盤」の語が無く「10P(T付)」「10P(E1,E2,E3,トクE)」「MDF 10P」等と書かれ、
      既存select_oneが62系に結び付けられない(0%)。
方針: 極数P + 種別(端子付/無・接地E・MDF・保安器函・端子明細)を読み取り、極数以上で最近傍上位の62系を選ぶ。
      MCB/ELB等の遮断器の"3P"を誤って端子盤にしない(遮断器キーワードがあれば対象外)。
"""
import re

# 各系列の (コード接頭, {極数: 下2/3桁}) は DB を参照して解決する。呼出側で byCode を渡す。
# 種別 -> DBの名称パターン
FAMILY = [
    ('接地端子盤', lambda n: re.search(r'(E1|E2|E3|ﾄｸE|トクE|接地)', n)),
    ('保安器函',   lambda n: '保安器' in n),
    ('MDF',        lambda n: re.search(r'MDF|主配線', n)),
    ('安定器収納函', lambda n: '安定器' in n),
    ('端子盤',      lambda n: True),   # 既定=端子盤(端子付/無は下で判定)
]
EXCLUDE = re.compile(r'MCB|ELB|MCCB|ELCB|LBS|VCB|VCS|DS|TR|SC|SR|CT|VT|MGS|MCTT|ﾌﾞ-ｽﾀ|ｾﾊﾟﾚ|ｺﾝｾﾝﾄ|ﾋｰﾀ|換気|FL|PL|RY|BZ|COS|PBS')


def _poles(name):
    m = re.search(r'(\d+)\s*[PＰ]\b', name) or re.search(r'(\d+)\s*[PＰ]', name)
    return int(m.group(1)) if m else None


def select_terminal(name, byCode):
    """端子盤系なら (code, conf, note) を返す。非該当は None。byCode={code:row}。"""
    n = re.sub(r'[（）]', lambda m: '(' if m.group() == '（' else ')', str(name))
    # 遮断器等のP(極数)を端子盤と誤認しない
    if EXCLUDE.search(n) and '端子' not in n and 'MDF' not in n and '保安器' not in n:
        return None
    p = _poles(n)
    if p is None:
        return None
    # 端子盤らしさ: 端子の"積極的指標"がある時のみ(極数Pだけでは判定しない=コンセント/遮断器の誤認防止)
    # 図面表記(露出盤 電話20P / 放送30P 等)にも対応。
    if not re.search(r'端子|MDF|主配線|保安器|安定器|E1|E2|E3|ﾄｸE|トクE|接地|T付|(^|\s)TB(\s|$)'
                     r'|電話|放送|情報|通信|LAN|露出盤', n):
        return None
    # 種別判定
    fam = next(f for f, test in FAMILY if test(n))
    tanshi_tsuki = bool(re.search(r'T付|端子付', n)) or not re.search(r'端子無', n)
    # DBから該当系列の(極数,コード)一覧を作り、極数以上で最近傍上位
    def pool(pred):
        out = []
        for c, row in byCode.items():
            nm = row['name']
            pp = _poles(nm)
            if pp and pred(nm):
                out.append((pp, c))
        return sorted(out)
    if fam == '接地端子盤':
        cand = pool(lambda nm: '接地端子盤' in nm)
    elif fam == '保安器函':
        cand = pool(lambda nm: '保安器函' in nm)
    elif fam == 'MDF':
        cand = pool(lambda nm: 'MDF' in nm or '主配線' in nm)
    elif fam == '安定器収納函':
        cand = pool(lambda nm: '安定器' in nm)
    else:  # 端子盤
        if tanshi_tsuki:
            cand = pool(lambda nm: '端子盤' in nm and '端子付' in nm)
        else:
            cand = pool(lambda nm: '端子盤' in nm and '端子無' in nm)
    pick = next((c for pp, c in cand if pp >= p), (cand[-1][1] if cand else None))
    if not pick:
        return None
    got_p = _poles(byCode[pick]['name'])
    conf = '◎' if got_p == p else '○'
    note = '' if got_p == p else '極数切上 %dP→%dP' % (p, got_p)
    return pick, conf, note


if __name__ == '__main__':
    import json, os
    byCode = {d['code']: d for d in json.load(open(os.path.join(os.path.dirname(__file__), '..', '..', 'db.json'), encoding='utf-8'))}
    tests = ['10P (T付)', '10P(E1,E2,E3,トクE)', '8P（E1,E2,E3,トクE）', 'MDF 10P',
             '端子盤 30P', '15P(端子無)', '保安器函 20P', 'B)MCB 3P225AF', 'LBS 3P200A', 'コンセント 2P15A']
    for t in tests:
        r = select_terminal(t, byCode)
        if r:
            print('  %-24s -> %s %s %s (%s)' % (t, r[0], r[1], byCode[r[0]]['name'][:18], r[2]))
        else:
            print('  %-24s -> (端子盤対象外)' % t)
