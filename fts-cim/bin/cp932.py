# -*- coding: utf-8 -*-
"""Windows（cp932）で出せない字を見つける・直す。

村上様・情報システム部門にお渡しする文書は、メモ帳とコマンドプロンプトで
開かれる。cp932 に無い字が1つでも混じると、そこで止まる。
**出す前に必ずここを通す。**

よく混じるのは、見た目がほとんど同じで符号だけが違う字。
  〜 U+301C 波ダッシュ      → ～ U+FF5E 全角チルダ
  — U+2014 emダッシュ       → ― U+2015 水平線
  ¥ U+00A5 半角円記号       → ￥ 全角
  − U+2212 マイナス記号     → － 全角
"""
NAOSU = {
    '〜': '～',   # 〜 → ～
    '—': '―',   # — → ―
    '¥': '￥',   # ¥ → ￥
    '−': '－',   # − → －
    '–': '―',   # – → ―
    ' ': '　',   # ノーブレークスペース → 全角空白
}


def naosu(s):
    for a, b in NAOSU.items():
        s = s.replace(a, b)
    return s


def warui(s):
    """cp932 で出せない字を返す（空なら問題なし）"""
    deru = set(s.encode('cp932', 'ignore').decode('cp932'))
    return sorted({c for c in s if not c.isspace()} - deru)


def kaku(path, s, newline='\r\n'):
    """cp932 で書き出す。出せない字が残っていたら書かずに止める。"""
    s = naosu(s)
    ng = warui(s)
    if ng:
        raise ValueError('cp932 で出せない字が残っています: %r' % ng)
    with open(path, 'w', encoding='cp932', newline=newline) as f:
        f.write(s)
    return len(s)
