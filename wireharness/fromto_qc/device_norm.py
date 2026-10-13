# -*- coding: utf-8 -*-
"""機器記号の正規化(④作業者表記 ↔ 設計図面DEVICE/DEVICE1 の突合用)。

作業者のFrom-To(④)は遮断器を『○/◎マーク＋枠番号』で記す(例 ELCB-○LG05, MCCB-◎LG55,
MCCB-○A)。図面のDEVICE1は枠番号のみ(LG05, LG55)。マークを除くと一致する。
10製番で遮断器番号の図面一致 56%→91% を確認。

strip_marks:   ○◎●等の囲み記号を除去。
normalize_no:  機器番号の正規化(マーク除去＋前後空白)。
normalize_sym: 機器記号(DEVICE-DEVICE1)の正規化。突合の共通キー。
"""
import re

# 囲み記号(白丸/二重丸/黒丸/大丸 等)。④の遮断器ラベルの接頭に付く。
_MARKS = re.compile(r'[○◎●◯⦿◉○◎●◯◉⦿]')


def strip_marks(s):
    """文字列から囲み記号(○◎●等)を除去する。"""
    return _MARKS.sub('', str(s or '')).strip()


def normalize_no(no):
    """機器番号を正規化(マーク除去)。例 '○LG05'→'LG05', '◎LG55'→'LG55'。"""
    return strip_marks(no)


def normalize_sym(device, no=''):
    """機器記号の正規化キー。device と no(番号)からマークを除いて 'DEVICE-NO' を作る。
    no が空なら device のみ(device 側のマークも除去)。大文字化はしない(日本語機器名を保つ)。"""
    dev = strip_marks(device)
    n = strip_marks(no)
    if '-' in dev and not n:
        d, nn = dev.rsplit('-', 1)
        return f'{d}-{strip_marks(nn)}'
    return f'{dev}-{n}' if n else dev


def canon(sym):
    """突合用の正準キー。マーク除去＋ハイフン除去＋大文字化。
    'FX-1'と'FX1'、'ELCB-○LG05'と'ELCBLG05' を同一視する(表記ゆれ吸収)。
    機器名の区切り(ハイフン)だけ違う表記を揃えるためのもので、突合の両辺に等しく適用する。"""
    return strip_marks(sym).replace('-', '').replace(' ', '').upper()
