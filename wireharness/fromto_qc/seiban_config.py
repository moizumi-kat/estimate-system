# -*- coding: utf-8 -*-
"""製番ごとの入力(人が指定する値)を保持する。

太物判定は、DENSEN(内部電線サイズ)やフレームで一意に決まらない回路が残る。
これらは『負荷率(稼働時間・顧客仕様)』で決まり、製番ごとに人が指定する入力である
(茂泉様確認済: 負荷率は製番ごとの入力とする)。本モジュールはその入力を製番単位で
保存/取得する。保存先は harness_learn と同じ _state_path('seiban_config.json')
(HARNESS_STATE 指定で永続領域へ外部化、未指定ならパッケージ内)。

load_factor は FS電線選定基準の負荷率: '1.0'(=①24時間操業)/'0.6'(=②)/'0.35'(=③)。
指定が無い製番は None(=保守的に両極一致のみ確定、割れる回路は要確認)。
"""
import json
import os
from .harness_learn import _state_path

CONFIG_PATH = _state_path('seiban_config.json')
VALID_FACTORS = ('1.0', '0.6', '0.35')


def _load():
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2, sort_keys=True)


def get(seiban):
    """製番の設定 dict を返す(無ければ空 dict)。"""
    return _load().get(str(seiban), {})


def load_factor(seiban):
    """製番の負荷率('1.0'/'0.6'/'0.35')。未設定なら None。"""
    return get(seiban).get('load_factor')


def set_load_factor(seiban, factor):
    """製番の負荷率を設定・保存する。factor は '1.0'/'0.6'/'0.35'。
    無効値は ValueError。None でクリア。"""
    if factor is not None and str(factor) not in VALID_FACTORS:
        raise ValueError(f'load_factor は {VALID_FACTORS} のいずれか(得たのは {factor!r})')
    cfg = _load()
    rec = cfg.setdefault(str(seiban), {})
    if factor is None:
        rec.pop('load_factor', None)
    else:
        rec['load_factor'] = str(factor)
    if not rec:
        cfg.pop(str(seiban), None)
    _save(cfg)
    return rec


def futo_override(seiban):
    """製番の『人が確定した太物/内』の回路別辞書 {回路番号(str): True(太物)/False(内)}。
    確認UIでの確定操作を蓄積したもの(最優先の確定根拠)。無ければ空 dict。"""
    ov = get(seiban).get('futo_override', {})
    return {str(k): bool(v) for k, v in ov.items()}


def set_futo_override(seiban, circuit, futo):
    """製番の回路について『太物(True)/内(False)』を人の確定として記録。None でクリア。"""
    cfg = _load()
    rec = cfg.setdefault(str(seiban), {})
    ov = rec.setdefault('futo_override', {})
    if futo is None:
        ov.pop(str(circuit), None)
    else:
        ov[str(circuit)] = bool(futo)
    if not ov:
        rec.pop('futo_override', None)
    if not rec:
        cfg.pop(str(seiban), None)
    _save(cfg)
    return rec


def tb_assign(seiban):
    """確認済の制御TB台割当 {回路(str): 台番号(str)}。
    発見ルール(制御TB=その回路の主回路TB台)で回路グルーピングした上で、人が台番号を確定した値。
    一度確認すればその回路の制御配線すべてに自動反映される(学習ループ)。無ければ空 dict。"""
    m = get(seiban).get('tb_assign', {})
    return {str(k): str(v) for k, v in m.items()}


def set_tb_assign(seiban, circuit, block):
    """製番の回路について、確認済の制御TB台番号を記録する。None でクリア。"""
    cfg = _load()
    rec = cfg.setdefault(str(seiban), {})
    m = rec.setdefault('tb_assign', {})
    if block is None:
        m.pop(str(circuit), None)
    else:
        m[str(circuit)] = str(block)
    if not m:
        rec.pop('tb_assign', None)
    if not rec:
        cfg.pop(str(seiban), None)
    _save(cfg)
    return rec


def pin_confirm(seiban):
    """確認済の端子番号(ピン) {キー(str): ピン(str)}。
    キーは '機器:接点識別'(例 '52-101:a1')など確認UI側で一意に決める。無ければ空 dict。"""
    m = get(seiban).get('pin_confirm', {})
    return {str(k): str(v) for k, v in m.items()}


def set_pin_confirm(seiban, key, pin):
    """製番の接点について確認済ピンを記録する。None でクリア。"""
    cfg = _load()
    rec = cfg.setdefault(str(seiban), {})
    m = rec.setdefault('pin_confirm', {})
    if pin is None:
        m.pop(str(key), None)
    else:
        m[str(key)] = str(pin)
    if not m:
        rec.pop('pin_confirm', None)
    if not rec:
        cfg.pop(str(seiban), None)
    _save(cfg)
    return rec


def get_style(seiban):
    """製番の流儀(作業者/スタイル)タグ。未設定なら None。
    各役割(1人目/2人目/3人目)に複数の作業者がいて、やり方が異なる場合に、学習を流儀ごとに
    分けるためのキー。同じ流儀の製番だけから横展開学習する(cross_learn)。"""
    return get(seiban).get('style')


def set_style(seiban, style):
    """製番の流儀タグを設定・保存する。None でクリア。"""
    cfg = _load()
    rec = cfg.setdefault(str(seiban), {})
    if style is None:
        rec.pop('style', None)
    else:
        rec['style'] = str(style)
    if not rec:
        cfg.pop(str(seiban), None)
    _save(cfg)
    return rec


def added_wires(seiban):
    """人が補完した配線のリスト [{'from':{機器,番号,端子},'to':{...}}]。
    ツールが生成できない製造側の結線(制御TB渡り・device→TB 等)を人が足したもの。
    FromToData 生成時に 確度:高(人確認)として出力に含める。無ければ空リスト。"""
    return [dict(w) for w in get(seiban).get('added_wires', [])]


def set_added_wires(seiban, wires):
    """製番の補完配線リストを丸ごと設定・保存する。空/None でクリア。"""
    cfg = _load()
    rec = cfg.setdefault(str(seiban), {})
    if wires:
        rec['added_wires'] = [dict(w) for w in wires]
    else:
        rec.pop('added_wires', None)
    if not rec:
        cfg.pop(str(seiban), None)
    _save(cfg)
    return rec


def add_wire(seiban, frm, to):
    """製番に補完配線を1本追加する。frm/to は {機器,番号,端子} dict。"""
    ws = added_wires(seiban)
    ws.append({'from': dict(frm), 'to': dict(to)})
    return set_added_wires(seiban, ws)


def record_decision(seiban, load_factor=None, futo_override=None,
                    tb_assign=None, pin_confirm=None, style=None, added_wires=None):
    """確認UIの確定内容(負荷率＋太物/内＋TB台割当＋ピン)をまとめて記録する。
    futo_override/tb_assign/pin_confirm はいずれも辞書。None の値はクリア。戻り: 保存後の製番レコード。"""
    if load_factor is not None:
        set_load_factor(seiban, load_factor or None)
    for circ, futo in (futo_override or {}).items():
        set_futo_override(seiban, circ, futo)
    for circ, blk in (tb_assign or {}).items():
        set_tb_assign(seiban, circ, blk)
    for key, pin in (pin_confirm or {}).items():
        set_pin_confirm(seiban, key, pin)
    if style is not None:
        set_style(seiban, style or None)
    if added_wires is not None:
        set_added_wires(seiban, added_wires or None)
    return get(seiban)


def all_seiban():
    """設定のある製番 → 設定 dict の一覧。"""
    return dict(_load())
