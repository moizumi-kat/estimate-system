# -*- coding: utf-8 -*-
"""
電気図面チェックエンジン(dxf_check.py)の回帰テスト。

方針(CLAUDE.md 1-2 副作用チェック / 回帰ゼロ):
  ルール改修時に「実ミスを検出し続けるか」「他ケースで誤検出しないか」を
  毎回確認するための自己完結テスト。顧客の実図面は同梱せず、実ミスと同じ
  ブロック属性構造を持つ合成DXFをその場で生成して検証する。

実行:
  python3 test_dxf_check.py      # 全ケースPASSで exit 0

各テストは「実図面の修正前後」で確認済みのミスに対応:
  test_main_lt_branch   … 4-26020-17(主幹75AT < 分岐205 100AT)
  test_phase_terminal   … 6-21072-1(1φ3Wなのに負荷TB端子がV表記)
  test_load_terminal    … 5-12110-38(同系統で1回路だけ負荷端末なし)
  test_seq_layout       … 4-21240-33(シーケンスのリレー/タイマーが配置図に無い)
"""
import sys, tempfile, os
import ezdxf
import dxf_check as dc


# ---- 合成DXF生成ヘルパ ----
def _make_dxf(dwgno, title2, seiban, comps):
    """comps: [{block,parts,device,dno,type,spec,code,terminal,phase_src?,x,y, **extra}]
    最小限のDXFを生成し一時ファイルパスを返す。タイトル枠と各機器をINSERT+ATTRIBで作る。"""
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()

    def _add(block_name, x, y, attribs):
        # ATTDEFを持つブロックを定義してからブロック参照+属性を付与
        if block_name not in doc.blocks:
            blk = doc.blocks.new(name=block_name)
            for i, tag in enumerate(attribs):
                blk.add_attdef(tag, dxfattribs={'insert': (0, i)})
        ref = msp.add_blockref(block_name, (x, y))
        ref.add_auto_attribs(attribs)
        return ref

    # タイトル枠
    _add('FA3_FRAME', 0, 0, {'SEIBAN': seiban, 'DWGNAME2': dwgno, 'TITLE2': title2})
    # 機器
    for c in comps:
        atts = {'PARTS': c['parts']}
        for k_src, k_dst in [('device', 'DEVICE'), ('dno', 'DEVICE1'), ('type', 'TYPE'),
                             ('spec', 'SPEC1'), ('code', 'CODE'), ('terminal', 'TERMINAL')]:
            if c.get(k_src):
                atts[k_dst] = str(c[k_src])
        for k, v in c.get('extra', {}).items():
            atts[k] = v
        _add(c.get('block', 'BLK_' + c['parts']), c.get('x', 0), c.get('y', 0), atts)

    path = tempfile.NamedTemporaryFile(suffix='.dxf', delete=False).name
    doc.saveas(path)
    return path


def _sysinfo(dno, power2, power1='AC'):
    return {'parts': '系統情報', 'device': '系統情報', 'dno': dno,
            'extra': {'2.電源種別-1': power1, '3.電源種別-2': power2}, 'x': 100, 'y': 2600}


def _run(comps, dwgno='001-H001', title2='結線図(1)', seiban='TEST-1'):
    p = _make_dxf(dwgno, title2, seiban, comps)
    try:
        d = dc.parse_dxf(p, fallback_name=dwgno)
        return dc.run_checks([d]), d
    finally:
        os.unlink(p)


def _run_multi(sheets):
    """sheets: [(dwgno,title2,seiban,comps), ...] を同時に投入。"""
    paths = [_make_dxf(dwgno, t2, sb, comps) for dwgno, t2, sb, comps in sheets]
    try:
        draws = [dc.parse_dxf(p, fallback_name=os.path.basename(p)) for p in paths]
        return dc.run_checks(draws), draws
    finally:
        for p in paths:
            os.unlink(p)


# ---- テスト本体 ----
def _cnt(findings, sev=None, cat=None, sub=None):
    n = 0
    for f in findings:
        if sev and f['sev'] != sev: continue
        if cat and f['cat'] != cat: continue
        if sub and sub not in f['msg']: continue
        n += 1
    return n


def test_main_lt_branch():
    """主幹75AT < 分岐100AT → 重大1件。分岐を75ATに直すと0件。"""
    base = [_sysinfo('2', '3φ3W 200V'),
            {'parts': 'MCCB', 'device': 'MCCB', 'dno': '2', 'block': 'LB_MCB',
             'type': 'E100-NF', 'spec': '3P100/75AT', 'x': 500, 'y': 2100}]
    bad = base + [{'parts': 'ELCB', 'device': 'ELCB', 'dno': '205', 'block': 'LB_ELB',
                   'type': 'ZE100-NF', 'spec': '3P100/100AT', 'x': 1500, 'y': 2100}]
    good = base + [{'parts': 'ELCB', 'device': 'ELCB', 'dno': '205', 'block': 'LB_ELB',
                    'type': 'ZE100-NF', 'spec': '3P100/75AT', 'x': 1500, 'y': 2100}]
    fb, _ = _run(bad, dwgno='017-E002', title2='スケルトン(2)')
    fg, _ = _run(good, dwgno='017-E002', title2='スケルトン(2)')
    assert _cnt(fb, sev='重大', cat='電気的整合性', sub='上回っています') == 1, fb
    assert _cnt(fg, sev='重大', cat='電気的整合性') == 0, fg
    # AFのみ(整定不明)の分岐は誤検出しない
    afonly = base + [{'parts': 'MCCB', 'device': 'MCCB', 'dno': '206', 'block': 'LB_MCB',
                      'type': 'E100-NF', 'spec': '3P100AF', 'x': 1800, 'y': 2100}]
    fa, _ = _run(afonly, dwgno='017-E002', title2='スケルトン(2)')
    assert _cnt(fa, sev='重大', cat='電気的整合性') == 0, fa


def test_phase_terminal():
    """1φ3W系統の負荷TB端子がV表記(N欠落) → 警告1件。U,N,Wなら0件。"""
    def case(term):
        return [_sysinfo('3', '1φ3W 200-100V'),
                {'parts': 'TB', 'device': 'TB', 'dno': '3', 'type': 'BN200NW',
                 'terminal': 'R,N,T', 'x': 500, 'y': 2400},
                {'parts': 'TB', 'device': 'TB', 'dno': '301', 'type': 'BN150W',
                 'terminal': term, 'x': 500, 'y': 1050}]
    fbad, _ = _run(case('U,V,W,E'), dwgno='001-E001', title2='スケルトン')
    fgood, _ = _run(case('U,N,W,E'), dwgno='001-E001', title2='スケルトン')
    assert _cnt(fbad, sev='警告', cat='電気的整合性', sub='中性線N') == 1, fbad
    assert _cnt(fgood, cat='電気的整合性') == 0, fgood
    # 3φ3W系統でV表記は正常 → 誤検出しない
    three = [_sysinfo('1', '3φ3W 200V'),
             {'parts': 'TB', 'device': 'TB', 'dno': '101', 'type': 'BN300NW',
              'terminal': 'U,V,W,E', 'x': 500, 'y': 1050}]
    f3, _ = _run(three, dwgno='001-E001', title2='スケルトン')
    assert _cnt(f3, cat='電気的整合性') == 0, f3


def test_load_terminal():
    """結線図で回路501/502に圧着端子があり503に無い → 警告1件。503にも端末があれば0件。"""
    def case(with503):
        rows = [_sysinfo('5', '1φ3W 200-100V'),
                {'parts': 'TB', 'device': 'TB', 'dno': '5', 'type': 'TBF-253',
                 'terminal': 'R,N,T', 'x': 500, 'y': 2450}]
        for i, dno in enumerate(['501', '502', '503']):
            y = 1875 - i * 125
            rows.append({'parts': 'MCCB', 'device': 'MCCB', 'dno': dno, 'block': 'LY3_MCB',
                         'type': 'E100-NF', 'spec': '3P100/100AT', 'x': 1925, 'y': y})
            if dno != '503' or with503:
                rows.append({'parts': '圧着端子', 'device': '圧着端子', 'type': '60-S8',
                             'x': 3300, 'y': y - 25})
        return rows
    fbad, _ = _run(case(False), dwgno='038-H006', title2='結線図（5）')
    fgood, _ = _run(case(True), dwgno='038-H006', title2='結線図（5）')
    assert _cnt(fbad, sev='警告', cat='記載漏れ・欠落', sub='負荷側端末') == 1, fbad
    assert _cnt(fgood, cat='記載漏れ・欠落', sub='負荷側端末') == 0, fgood


def test_seq_layout():
    """シーケンス(F)のリレー52X-101・タイマー2-101が内部配置図(D)に無い → 重大2件。あれば0件。"""
    seq = ('033-F001', 'シーケンス', 'GRP-1', [
        {'parts': 'AXR', 'device': '51X', 'dno': '101', 'type': 'RU4S-A200', 'x': 100, 'y': 2000},
        {'parts': 'AXR', 'device': '52X', 'dno': '101', 'type': 'RU4S-A200', 'x': 200, 'y': 2000},
        {'parts': 'TM', 'device': '2', 'dno': '101', 'type': 'MS4SA-AP', 'x': 300, 'y': 2000},
    ])
    lay_bad = ('033-D001', '完全自立形 1基', 'GRP-1', [
        {'parts': 'AXR', 'device': '51X', 'dno': '101', 'type': 'RU4S', 'x': 100, 'y': 1000},
    ])
    lay_good = ('033-D001', '完全自立形 1基', 'GRP-1', [
        {'parts': 'AXR', 'device': '51X', 'dno': '101', 'type': 'RU4S', 'x': 100, 'y': 1000},
        {'parts': 'AXR', 'device': '52X', 'dno': '101', 'type': 'RU4S', 'x': 200, 'y': 1000},
        {'parts': 'TM', 'device': '2', 'dno': '101', 'type': 'MS4S', 'x': 300, 'y': 1000},
    ])
    fbad, _ = _run_multi([seq, lay_bad])
    fgood, _ = _run_multi([seq, lay_good])
    assert _cnt(fbad, sev='重大', cat='記載漏れ・欠落', sub='内部配置図に記載されていません') == 2, fbad
    assert _cnt(fgood, cat='記載漏れ・欠落', sub='内部配置図に記載されていません') == 0, fgood


def main():
    tests = [test_main_lt_branch, test_phase_terminal, test_load_terminal, test_seq_layout]
    ng = 0
    for t in tests:
        try:
            t()
            print(f'  PASS  {t.__name__}')
        except AssertionError as e:
            ng += 1
            print(f'  FAIL  {t.__name__}: {e}')
        except Exception as e:
            ng += 1
            print(f'  ERROR {t.__name__}: {type(e).__name__}: {e}')
    print(f'\n{len(tests)-ng}/{len(tests)} passed')
    sys.exit(1 if ng else 0)


if __name__ == '__main__':
    main()
