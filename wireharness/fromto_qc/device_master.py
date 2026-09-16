# -*- coding: utf-8 -*-
"""機器マスタ（製造側・データ駆動）。

目的（茂泉様方針）:
  設計に現行以上を求めず、製造側で一意化する。その土台となる「機器の共通知識」を
  コードのハードコードから、現場/学習で育てられる表データ（JSON）へ移す。

保持する知識:
  alias      … 記号ゆらぎの正規化（人手名↔図面名）。基底記号ベース。現場確認/学習で育つ。
  main       … 主回路の遮断器基底（LUG/相バス側）
  door       … 扉付けになりやすい操作器・表示灯の基底
  lug_terms  … 端子→LUG（主回路入力端子）
  wago_terms … 端子→WAGO（警報補助接点）
  capacity   … 1端子に付く丸/Y端子の上限（超過で中継/渡り）
  wire       … 既定電線種別（制御/主回路）
  colors     … 相→色（赤/白/青 等）※任意

方針:
  - 既定値は「実データで検証済みの現行ルール」を種として持つ（＝単一の真実源）。
  - alias は空で開始し、現場が確定した対応・学習(auto_alias→現場承認)だけを蓄積する。
    （自動候補を無確認で入れて実機器を潰さないため。確定のみ promote_alias で登録）
  保存: kenzu_data/device_master.json（無ければ既定シードで動作）。
"""
import os
import re
import json

try:
    import kenzu_store
    _DIR = kenzu_store.DATA_DIR
except Exception:
    _DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'kenzu_data')
MASTER_PATH = os.path.join(_DIR, 'device_master.json')

# 検証済み現行ルールを既定シードとして保持（単一の真実源）
_SEED = {
    'alias': {},                                   # 記号ゆらぎ（基底 or 完全記号）→正規記号。現場確認で育てる
    'alias_full': {},                              # 完全記号の1:1確定対応（製番固有）。任意
    'main': ['MCCB', 'ELCB', 'ELB', 'LBS', 'ACB', 'CP'],
    'door': ['WL', 'RL', 'GL', 'YL', 'OL', 'BZ', 'BS', 'PB', 'PBS', 'AM', 'VM', 'COS', 'SL'],
    'lug_terms': ['1', '3', '5'],
    'wago_terms': ['ALa', 'ALc', 'EALa', 'EALc'],
    'capacity': 2,
    'wire': {'ctrl': 'KIV', 'main': 'HIV'},
    'colors': {'R': '赤', 'S': '白', 'T': '青', 'E': '緑'},
}


def _upper(seq):
    return {str(s).upper() for s in seq}


def load():
    """マスタを読み込む。ファイルが無ければ既定シードを返す（破壊しない）。"""
    data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
            for k, v in _SEED.items()}
    if os.path.exists(MASTER_PATH):
        try:
            with open(MASTER_PATH, encoding='utf-8') as fp:
                disk = json.load(fp)
            for k, v in disk.items():
                data[k] = v
        except Exception:
            pass
    return data


def save(data):
    os.makedirs(_DIR, exist_ok=True)
    with open(MASTER_PATH, 'w', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1)


class Master:
    """読み込んだマスタへのアクセサ。rules_engine 等はこれ経由で知識を参照できる。"""

    def __init__(self, data=None):
        self.d = data if data is not None else load()
        self._main = _upper(self.d.get('main', []))
        self._door = _upper(self.d.get('door', []))
        self._lug = {str(t).upper() for t in self.d.get('lug_terms', [])}
        self._wago = {str(t).upper() for t in self.d.get('wago_terms', [])}
        self._alias = {str(k).upper(): str(v).upper() for k, v in self.d.get('alias', {}).items()}
        self._alias_full = {str(k).upper(): str(v).upper()
                            for k, v in self.d.get('alias_full', {}).items()}

    # ---- 記号の分解・正規化 ----
    @staticmethod
    def split(sym):
        """記号を (基底, 番号) に分解。例 '86X102'→('86X','102'), 'MCCB105'→('MCCB','105'),
        'TBK1'→('TBK','1'), '52102'→('','52102')。末尾の連続数字を番号とみなす。"""
        s = str(sym).strip()
        m = re.match(r'^(.*?)(\d+)$', s)
        if m:
            return m.group(1).rstrip('-'), m.group(2)
        return s, ''

    def canon(self, sym):
        """記号ゆらぎを正規化して返す。確定 alias（完全記号→基底）のみ適用。"""
        if sym is None:
            return sym
        s = str(sym).strip()
        up = s.upper()
        if up in self._alias_full:          # 完全記号の確定対応（製番固有）
            return self._alias_full[up]
        base, num = self.split(s)
        b = base.upper()
        if b in self._alias:                # 基底記号の確定対応（汎用）
            return self._alias[b] + num
        return s

    def apply(self, devs):
        """機器集合を一括正規化。"""
        return {self.canon(d) for d in devs}

    # ---- 知識アクセサ（rules_engine から利用可能）----
    def is_main(self, dev):
        return self.split(dev)[0].upper() in self._main

    def is_door(self, dev):
        return self.split(dev)[0].upper() in self._door

    def is_lug(self, term):
        return str(term).upper() in self._lug

    def is_wago(self, term):
        return str(term).upper() in self._wago

    def capacity(self):
        return int(self.d.get('capacity', 2))

    def wire(self, kind):
        return self.d.get('wire', {}).get(kind, 'KIV')

    def color(self, phase):
        return self.d.get('colors', {}).get(str(phase).upper(), '')


def promote_alias(src, dst, full=False, user='', note=''):
    """確定した記号ゆらぎをマスタへ登録（現場承認・学習からのみ呼ぶ）。
      src → dst に正規化。full=True なら完全記号対応、False なら基底記号対応。
    自動候補を無確認で入れないこと（実機器を潰さないため）。"""
    data = load()
    key = 'alias_full' if full else 'alias'
    tbl = data.setdefault(key, {})
    tbl[str(src)] = str(dst)
    save(data)
    try:
        import kenzu_store
        kenzu_store.add_feedback(None, 'fixed', rule='master:alias',
                                 note=f"{src}->{dst}{' (full)' if full else ''} {note}", user=user)
    except Exception:
        pass
    return {'src': src, 'dst': dst, 'full': full}
