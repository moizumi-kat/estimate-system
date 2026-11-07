# -*- coding: utf-8 -*-
"""workflow.run_workflow(工程フロー統合)の固定テスト。各工程関数をモック。"""
import os
import tempfile
from . import workflow as WF


def test_run_workflow_order_and_hub():
    calls = []
    orig = {}
    import fromto_qc.locator_apply as LAP
    import fromto_qc.locator_assign as LA
    import fromto_qc.harness_produce as HP
    import fromto_qc.stage_views as SV
    import fromto_qc.stage_interactive as SI
    orig = dict(wd=LAP.write_dxf, bd=LA.basis_from_dct, ad=HP.analyze_defects,
                lc=SI.locator_confirm, s2=SV.stage2_terminals, s1=SI.stage1_wires,
                ex=SI.export_stage1_harness, s3=SV.stage3_fromto)
    LAP.write_dxf = lambda layout, out, basis_of=None, dct_paths=None: (calls.append('loc_dxf'), 5)[1]
    LA.basis_from_dct = lambda dct: {}
    SI.locator_confirm = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('ロケーター確認UIは廃止(手本準拠なら確認不要)'))
    HP.analyze_defects = lambda seq, skel, sb, layout_path=None: (calls.append('defect'),
                                                                  {'defects': [], 'summary': {'不備件数': 0}})[1]
    SV.stage2_terminals = lambda seq, skel, sb, out: (calls.append('term'), open(out, 'w').write('x'), {'tb': 41})[2]
    SI.stage1_wires = lambda skel, sb, out: (calls.append('s1'), open(out, 'w').write('x'), 108)[2]
    SI.export_stage1_harness = lambda skel, sb, out: (calls.append('s1csv'), open(out, 'w').write('x'), 108)[2]
    SV.stage3_fromto = lambda seq, skel, sb, out: (calls.append('s3'), open(out, 'w').write('x'),
                                                   {'gousen': 189, 'wires': 797})[2]
    try:
        d = tempfile.mkdtemp()
        res = WF.run_workflow('T', ['F.DXF'], ['E.DXF'], ['D.DXF'], dct_paths=['x.dxf'], out_dir=d)
        # 工程の順序: 2人目(ロケーター付与→不備→端子) が 1人目/3人目 より前。
        # ロケーターは自動書き戻し(loc_dxf)のみ。確認UI(loc_html)は廃止。
        assert 'loc_html' not in calls
        assert calls.index('loc_dxf') < calls.index('defect') < calls.index('term')
        assert calls.index('term') < calls.index('s1') and calls.index('term') < calls.index('s3')
        assert res['2人目']['terminals'] == 41 and res['1人目']['wires'] == 108
        assert res['3人目']['gousen'] == 189
        hub = open(os.path.join(d, 'T_00_ハブ.html'), encoding='utf-8').read()
        assert '2人目（入口）' in hub and '並行' in hub and '1人目' in hub and '3人目' in hub
    finally:
        LAP.write_dxf, LA.basis_from_dct, HP.analyze_defects = orig['wd'], orig['bd'], orig['ad']
        SI.locator_confirm, SV.stage2_terminals = orig['lc'], orig['s2']
        SI.stage1_wires, SI.export_stage1_harness, SV.stage3_fromto = orig['s1'], orig['ex'], orig['s3']


if __name__ == '__main__':
    test_run_workflow_order_and_hub()
    print('workflow tests: OK')
