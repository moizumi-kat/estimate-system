# -*- coding: utf-8 -*-
"""connector_crossover のロジック固定テスト(DXF非依存・合成データ)。

実機の手本/図面(社内資産)は同梱しないため、ここでは分類(classify)・渡り検出
(detect_crossings)・生成(generate)の決定的ロジックを合成 wire で検証する。
device→面 の図面抽出(build_device_face)は DXF 依存のため別途実機で検証済み。
"""
from . import connector_crossover as CC


def _wire(g, fd, fn, ft, td, tn, tt):
    return {'gousen': g,
            'from': {'device': fd, 'no': fn, 'terminal': ft},
            'to': {'device': td, 'no': tn, 'terminal': tt}}


def test_classify_power_by_mccb_primary_pole():
    # MCCB の一次極(奇数 1/3/5)を使う渡り = 太物(power)。手本: 5-21071 の 1G系。
    wires = [_wire('1G2', 'MCCB', '1G2', '1', 'MCCB', '2G2', '3')]
    assert CC.classify('1G2', wires) == 'power'


def test_classify_power_by_phase_terminal():
    # 相端子(R/S/T/U/V/W/N・数字前置可: U,1R,201U)に繋がる渡り = 太物。手本: 渡り用TB の相端子。
    assert CC.classify('2U', [_wire('2U', 'MCCB', 'X', 'U', 'TB', '1', '')]) == 'power'
    assert CC.classify('201U', [_wire('201U', 'TB', 'C', '201U', 'LOAD', '1', '')]) == 'power'


def test_classify_control_by_mccb_secondary_pole():
    # MCCB の二次極(2/4/6)のみを使う計器/CT タップ = 制御。手本: 117 の RC2/SC2。
    wires = [_wire('RC2', 'MCCB', '1A', '2', 'CT', 'k', ''),
             _wire('RC2', 'MCCB', '1A', '4', '51', '201', '97')]
    assert CC.classify('RC2', wires) == 'control'


def test_classify_control_by_mccb_alarm_terminal():
    # MCCB の警報/補助接点(ALc/ALa)のみ = 制御(control)
    wires = [_wire('KA', 'MCCB', '1A', 'ALc', 'TB', '2', ''),
             _wire('KA', 'MCCB', '1G', 'ALc', '30X', '1', '6')]
    assert CC.classify('KA', wires) == 'control'


def test_classify_control_without_mccb():
    # MCCB を含まない制御号線
    wires = [_wire('104', 'OL', '12', '', 'DTAL', '1', '2')]
    assert CC.classify('104', wires) == 'control'


def test_classify_control_relay_numeric_contacts():
    # リレー/VCB の接点番号(1-6)は MCCB の一次極ではない = 制御。手本: 117 の 201。
    wires = [_wire('201', '52', 'X', '1', '52', 'X', '3'),
             _wire('201', '52', 'X', '5', 'CT', 'K', '')]
    assert CC.classify('201', wires) == 'control'


def test_detect_crossings_spans_two_faces():
    # 号線が2面に跨れば検出、1面のみなら非検出
    dev_face = {('OL', '12'): '1', ('DTAL', '1'): '2', ('OL', '22'): '1'}
    wires = [_wire('104', 'OL', '12', '', 'DTAL', '1', '2'),   # 面1→面2 跨ぎ
             _wire('900', 'OL', '12', '', 'OL', '22', '')]      # 面1のみ
    cross = CC.detect_crossings(wires, dev_face)
    assert '104' in cross and cross['104'] == {'1', '2'}
    assert '900' not in cross


def test_detect_tb_goes_to_circuit_panel():
    # 汎用端子台(device='TB')は端子番号が物理ブロック名(TB22)に偶然一致しても
    # そこへ当てず、号線の回路基底が属する盤へ寄せる(回路103のTBは盤1=52-103の盤)。
    # bases 指定で: 103D(TB→回路103の盤1 ＋ 52-103=盤1) は同一盤 → 渡りにならない。
    dev_face = {('52', '103'): '1', ('TB', '22'): '2'}   # TB22 は盤2(物理ブロック)
    wires = [_wire('103D', 'TB', '22', '', '52', '103', '')]
    # bases 無し(従来)は TB を物理ブロックに当てない=盤不明 → 52-103 のみ=非跨ぎ
    assert CC.detect_crossings(wires, dev_face) == {}
    # bases 有り: TB は回路103の盤(=1)、52-103 も盤1 → 同一盤=非跨ぎ(偽渡り解消)
    assert CC.detect_crossings(wires, dev_face, {'103'}) == {}


def test_detect_tb_real_cross_via_devices():
    # 本物の盤間渡りは TB 以外の実機器端点で検出される(TBを寄せても recall を損なわない)。
    dev_face = {('52', '301'): '3', ('51', '301'): '5'}
    wires = [_wire('C9', '52', '301', '', '51', '301', '')]   # 面3↔面5 = 実機器で跨ぎ
    assert CC.detect_crossings(wires, dev_face, {'301'}) == {'C9': {'3', '5'}}


def test_detect_collecting_tb_bus_to_master_panel():
    # 集約端子台バス: 基底(237)の全メンバがTB端点を持ち、非TB機器が2盤に跨る(盤5の52-50x＋
    # 盤6の52-601)→ 中継端子台はマスター(最上位=盤6)。各メンバが機器盤↔盤6で渡りになる。
    dev_face = {('52', '503'): '5', ('52', '504'): '5', ('52', '601'): '6'}
    wires = [_wire('237A', 'TB', '', '', '52', '601', ''),   # 盤6の52-601にも繋がる(=盤6到達)
             _wire('237B', 'TB', '', '', '52', '503', ''),   # 盤5の52-503(単独では非跨ぎ)
             _wire('237C', 'TB', '', '', '52', '504', '')]    # 盤5の52-504(同上)
    cross = CC.detect_crossings(wires, dev_face)
    # 237B/237C は 盤5 と 集約TB(盤6) で渡りになる(取りこぼし解消)
    assert cross.get('237B') == {'5', '6'} and cross.get('237C') == {'5', '6'}


def test_detect_non_bus_group_stays_local():
    # 集約バスでない(非TBメンバを含む/単一盤)グループは従来どおり面内完結=非渡り。
    dev_face = {('52', '103'): '1', ('TB', '22'): '2'}
    wires = [_wire('103', '52', '103', '', 'CT', '103', ''),   # TB端点なし → 集約バス不成立
             _wire('103D', 'TB', '22', '', '52', '103', '')]
    assert CC.detect_crossings(wires, dev_face, {'103'}) == {}


def test_detect_concat_name_fallback():
    # app の (DTAL,1) が DCT の DEVICE 'DTAL1' と連結名で一致
    dev_face = {('DTAL1', '2-I'): '2', ('OL', '12'): '1'}
    wires = [_wire('104', 'OL', '12', '', 'DTAL', '1', '2')]
    cross = CC.detect_crossings(wires, dev_face)
    assert cross.get('104') == {'1', '2'}


def test_normalize_gousen_numeric_branch_collapses():
    # 回路番号＋数字枝番(1〜2桁) → 回路番号。手本: 20110→201。
    bases = {'201', '202', '101'}
    assert CC.normalize_gousen('20110', bases) == '201'
    assert CC.normalize_gousen('20131', bases) == '201'
    assert CC.normalize_gousen('2029', bases) == '202'        # 1桁枝番
    assert CC.normalize_gousen('201', bases) == '201'          # 枝番なしはそのまま


def test_normalize_gousen_letter_suffix_kept():
    # 末尾が英字/端子名(201R/201C1)は畳まず別号線のまま。手本準拠。
    bases = {'201'}
    assert CC.normalize_gousen('201R', bases) == '201R'
    assert CC.normalize_gousen('201C1', bases) == '201C1'
    assert CC.normalize_gousen('201S', bases) == '201S'


def test_normalize_gousen_non_circuit_kept():
    # 回路集合に無い数字基底(5-21071の104=回路は1G/2G系)は畳まない=104→1等の誤変換なし。
    bases = {'1G', '2G2', '1A'}      # 単桁主幹は len>=2 で既に除外済みの想定
    assert CC.normalize_gousen('104', bases) == '104'
    assert CC.normalize_gousen('104', set()) == '104'             # 空集合=無変換(回帰ゼロ)


def test_normalize_gousen_control_two_digit_branch_collapses():
    # 制御: 英字基底＋末尾2桁数字=枝番 → 基底。手本: 1BZ07→1BZ, 2BZ04→2BZ。回路集合不要。
    assert CC.normalize_gousen('2BZ04', set()) == '2BZ'
    assert CC.normalize_gousen('1BZ07', set()) == '1BZ'
    assert CC.normalize_gousen('4BZ05', {'201'}) == '4BZ'
    assert CC.normalize_gousen('2A31', set()) == '2A'


def test_normalize_gousen_control_one_digit_index_kept():
    # 制御: 英字基底＋末尾1桁=信号index(別信号) → そのまま。手本: RC1-4/LT1-4/AL1-2 を区別。
    for g in ['RC2', 'LT2', 'SC2', 'AL1', 'AL2', 'P1']:
        assert CC.normalize_gousen(g, set()) == g
    # 末尾が英字/端子(201R/201C1)も非畳み
    assert CC.normalize_gousen('201R', {'201'}) == '201R'
    assert CC.normalize_gousen('201C1', {'201'}) == '201C1'


def test_normalize_gousen_longest_base_wins():
    # 最長一致: 20110 は 201(3桁)で畳む(2(1桁)ではない)。※単桁は len>=2 で除外前提
    assert CC.normalize_gousen('20110', {'201', '20'}) == '201'


def test_normalize_gousen_circuit_itself_not_collapsed():
    # 線番そのものが回路番号(別MCCB)なら畳まない。5-21071: 1G/1G1/1G2 は各々別回路。
    bases = {'1G', '1G1', '1G2', '2G2'}
    assert CC.normalize_gousen('1G1', bases) == '1G1'
    assert CC.normalize_gousen('1G2', bases) == '1G2'
    assert CC.normalize_gousen('1G', bases) == '1G'


def test_generate_empty_without_faces():
    # device→面 が作れない(dct_paths=None)なら空=回帰ゼロ
    routed = {'wires': [_wire('104', 'OL', '12', '', 'DTAL', '1', '2')]}
    out = CC.generate(routed, None)
    assert out == {'control': [], 'power': [], 'faces': 0,
                   'conf': {}, 'conflicts': [], 'rows': []}


def test_source_type_sequence_is_control():
    # シーケンス図の線番に出る号線 = 制御(由来図面の裏付け)
    ev = {'seq_gousen': {'KA'}, 'power_devs': set()}
    assert CC.source_type('KA', [_wire('KA', 'X', '1', '13', 'Y', '2', '14')], ev) == 'control'


def test_source_type_skeleton_power_device_is_power():
    # スケルトンの電源機器に繋がり線番に無い号線 = 太物(由来図面の裏付け)
    ev = {'seq_gousen': set(), 'power_devs': {('MCCB', '1G2')}}
    assert CC.source_type('1G2', [_wire('1G2', 'MCCB', '1G2', '1', 'BUS', '', '')], ev) == 'power'


def test_source_type_none_when_ambiguous():
    # 由来情報が無い/両立しない号線は None(由来からは断定しない)
    assert CC.source_type('X', [_wire('X', 'A', '1', '1', 'B', '2', '2')], None) is None
    ev = {'seq_gousen': {'Y'}, 'power_devs': {('A', '1')}}   # 線番かつ電源機器=両立
    assert CC.source_type('Y', [_wire('Y', 'A', '1', '1', 'B', '2', '2')], ev) is None


def test_generate_conf_agreement_double_checked():
    # 端子信号と由来図面が一致すれば確度◎、由来情報が無ければ○
    routed = {'wires': [_wire('1G2', 'MCCB', '1G2', '1', 'MCCB', '2G2', '3'),
                        _wire('KA', 'OL', '12', '13', 'DTAL', '1', '2')]}
    # dev_face を直接与えるため build をモンキーパッチせず、generate 内の検出に乗る形で検証
    dev_face = {('MCCB', '1G2'): '1', ('MCCB', '2G2'): '2',
                ('OL', '12'): '1', ('DTAL', '1'): '2'}
    cross = CC.detect_crossings(routed['wires'], dev_face)
    assert cross.get('1G2') == {'1', '2'} and cross.get('KA') == {'1', '2'}
    ev = {'seq_gousen': {'KA'}, 'power_devs': {('MCCB', '1G2'), ('MCCB', '2G2')}}
    assert CC.source_type('1G2', routed['wires'], ev) == 'power'      # 由来=太物
    assert CC.classify('1G2', routed['wires']) == 'power'             # 端子=太物 → ◎
    assert CC.source_type('KA', routed['wires'], ev) == 'control'     # 由来=制御
    assert CC.classify('KA', routed['wires']) == 'control'            # 端子=制御 → ◎


def test_color_gousen_ignored():
    # 色トークン(白/赤 等)は号線でないので渡り判定に使わない
    dev_face = {('A', '1'): '1', ('B', '2'): '2'}
    wires = [_wire('白', 'A', '1', '', 'B', '2', '')]
    assert CC.detect_crossings(wires, dev_face) == {}


def test_rows_control_rf_lf_cores():
    # 制御渡りは R-F(P)[送り面]/L-F(R)[受け面] の白-N芯(面ペアごと連番)
    rows = CC._rows(control=[('104', '1', '2'), ('106', '1', '2')], power=[],
                    conf={'104': '◎', '106': '○'})
    assert {'面': '1', '号線': '104', '機器': 'R-F(P)', '端子': '白-1', '型': '制御', '確度': '◎'} in rows
    assert {'面': '2', '号線': '104', '機器': 'L-F(R)', '端子': '白-1', '型': '制御', '確度': '◎'} in rows
    assert {'面': '1', '号線': '106', '機器': 'R-F(P)', '端子': '白-2', '型': '制御', '確度': '○'} in rows


def test_rows_power_direct():
    # 太物渡りは両面に『直接渡り(太物)』(現場ルール: 2.0sq超は直接1本渡り・たまにTB)。
    rows = CC._rows(control=[], power=[('2G2', '1', '2')], sizes={'2G2': '60sq'})
    kinds = {(r['面'], r['機器']) for r in rows}
    assert ('1', '直接渡り(太物)') in kinds and ('2', '直接渡り(太物)') in kinds
    assert all(r['型'] == '太物' for r in rows)
    assert all(r['端子'] == '60sq' for r in rows)       # サイズ併記


def test_rows_core_number_per_signal_global():
    # 芯番号は信号ごとに一貫(全境界で同じ)。手本: KA=白-1/KB=白-2 を各境界で使用。
    rows = CC._rows(control=[('a', '1', '2'), ('b', '2', '3')], power=[])
    a1 = [r for r in rows if r['号線'] == 'a' and r['機器'] == 'R-F(P)'][0]
    b1 = [r for r in rows if r['号線'] == 'b' and r['機器'] == 'R-F(P)'][0]
    assert a1['端子'] == '白-1' and b1['端子'] == '白-2'   # 信号ごとに通し番号


def test_rows_multiface_connector_per_boundary():
    # 3面跨ぎ(面1-2-3)は各境界にR-F/L-F、中間面2は L-F(R)＋R-F(P) の両方。手本KA準拠。
    rows = CC._rows(control=[('KA', '1', '3')], power=[], face_order=['1', '2', '3'])
    got = {(r['面'], r['機器'], r['端子']) for r in rows if r['号線'] == 'KA'}
    assert ('1', 'R-F(P)', '白-1') in got          # 面1=送り
    assert ('2', 'L-F(R)', '白-1') in got          # 面2=左から受け
    assert ('2', 'R-F(P)', '白-1') in got          # 面2=右へ再送(中継)
    assert ('3', 'L-F(R)', '白-1') in got          # 面3=受け
    # 面2は L-F(R) と R-F(P) の両方を持つ
    f2 = {r['機器'] for r in rows if r['面'] == '2'}
    assert f2 == {'L-F(R)', 'R-F(P)'}


def test_rows_passthrough_face_relayed():
    # 面{1,3}しか触れない信号も、間の面2を物理境界として中継(通過面)。
    rows = CC._rows(control=[('X', '1', '3')], power=[], face_order=['1', '2', '3'])
    faces = sorted({r['面'] for r in rows})
    assert faces == ['1', '2', '3']               # 通過面2にも中継コネクタ
