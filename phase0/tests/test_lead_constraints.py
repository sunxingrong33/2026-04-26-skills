"""Lead entry and project constraints (I2.8 A and C): anchors, kept fragments, unverified values."""
import pytest

from phase0.ledger.migrate import build
from phase0.sar import lead
from phase0.sar import project_sar as ps
from phase0.tests.test_project_sar import goal

LEDGER = build()
EX2 = next(c for c in LEDGER.compounds if c.id == 'WO2013132376A1:example:2')  # lorlatinib
DIMETHYLAMIDE = 'CN(C)C=O'  # 6f carries it, 6e and the triazole replacement do not
AMINOPYRIDINE = 'Nc1ncccc1OC'  # shared by the whole series


def analyse(**kw):
    return ps.analyse(LEDGER, goal(), None, 12, **kw)


# A: the lead is an anchor, not a source of numbers.

def test_lead_is_standardised_and_matched_against_the_ledger():
    r = lead.read_lead({'smiles': EX2.smiles + '.Cl', 'label': '当前先导'}, LEDGER)
    assert r['smiles'] != r['original_smiles'] and 'largest_fragment' in [s['step'] for s in r['steps']]
    assert r['in_ledger'] and {m['compound_id'] for m in r['ledger_matches']} >= {EX2.id}
    assert r['label'] == '当前先导'


def test_unknown_lead_says_it_does_not_join_the_pairing():
    r = lead.read_lead('CCOc1ccccc1NC(=O)C1CC1', LEDGER)
    assert r['in_ledger'] is False and r['ledger_matches'] == []
    assert '只用作锚点' in r['note'] and '加入台账' in r['note']


def test_lead_never_changes_the_analysis_numbers():
    plain, anchored = analyse(), analyse(lead={'smiles': EX2.smiles})
    for key in ('pair_count', 'transforms', 'sites', 'followups', 'coverage'):
        assert plain[key] == anchored[key]
    assert anchored['lead']['smiles'] and plain['lead'] is None


@pytest.mark.parametrize('bad, message', [
    ('not a smiles', 'SMILES'),
    ({'smiles': EX2.smiles, 'label': 'x' * 61}, '名称'),
    (123, '格式无效'),
])
def test_invalid_leads_are_refused(bad, message):
    with pytest.raises(ValueError, match=message):
        lead.read_lead(bad, LEDGER)


# C: constraints mark pairs that leave the space; they never delete evidence.

def test_broken_scaffold_is_shown_but_never_a_candidate():
    r = analyse(constraints={'keep': [{'pattern': DIMETHYLAMIDE, 'label': 'N,N-二甲酰胺'}]})
    out = [t for t in r['transforms'] if t['category']['key'] == 'out_of_scope']
    assert out, '至少一种替换破坏了该片段'
    g = out[0]
    assert g['category']['lost'] == ['N,N-二甲酰胺'] and g['category']['was'] in ps.CATEGORY
    assert g['pairs'], '分子对仍然显示，不被删除'
    assert all(t['category']['key'] != 'candidate' for t in out)
    # the transformation's own evidence is untouched: only its category changed
    plain = {t['transform']: t['summary'] for t in analyse()['transforms']}
    assert g['summary'] == plain[g['transform']]


def test_a_fragment_the_series_keeps_marks_nothing():
    r = analyse(constraints={'keep': [AMINOPYRIDINE]})
    assert all(t['category']['key'] != 'out_of_scope' for t in r['transforms'])
    assert all(p['constraints']['ok'] for t in r['transforms'] for p in t['pairs'])


def test_pairs_without_the_fragment_are_not_violations():
    r = analyse(constraints={'keep': [DIMETHYLAMIDE]})
    absent = [p for t in r['transforms'] for p in t['pairs'] if p['constraints']['absent']]
    assert absent and all(p['constraints']['ok'] for p in absent)


def test_out_of_scope_pairs_drop_out_of_the_followup_ranking():
    plain, limited = analyse(), analyse(constraints={'keep': [DIMETHYLAMIDE]})
    keys = lambda r: {(i['compound_id'], i['assay_id']) for i in r['followups']['items']}  # noqa: E731
    assert keys(limited) < keys(plain)
    assert '已排除破坏必须保留片段的分子对' in limited['followups']['rule']


def test_synthesis_notes_are_labels_not_judgements():
    r = analyse(constraints={'keep': [DIMETHYLAMIDE], 'synthesis_notes': ['不接受新增手性中心']})
    items = next(t['synthesis_items'] for t in r['transforms'] if t['category']['key'] in ('candidate', 'tradeoff'))
    assert items[0] == '项目限制（用户填写）：不接受新增手性中心'
    assert items[-1] == '以上仅为待评估项，不是合成可行性判断'


@pytest.mark.parametrize('bad, message', [
    ({'keep': ['CC']}, '至少需要'),
    ({'keep': ['not a fragment']}, '无法解析'),
    ({'keep': ['C1CC1'] * 6}, '最多'),
    ({'synthesis_notes': ['x' * 201]}, '不超过'),
])
def test_invalid_constraints_are_refused(bad, message):
    with pytest.raises(ValueError, match=message):
        lead.read_constraints(bad)


# C: the chemist's own numbers stay text, in their own column.

def test_reference_values_are_text_and_never_computed_with():
    r = analyse(reference=[{'property_id': 'cell_potency', 'value': '约 120 nM（内部批次）', 'note': '单次'},
                           {'property_id': 'efflux', 'value': ''}])  # blank rows are dropped
    assert len(r['reference']) == 1
    row = r['reference'][0]
    assert row == {'property_id': 'cell_potency', 'property': '细胞活性', 'value': '约 120 nM（内部批次）',
                   'note': '单次', 'source': 'user_unverified', 'source_label': '用户提供，未核实'}
    assert isinstance(row['value'], str)
    plain = analyse()
    assert r['transforms'] == plain['transforms'] and r['followups'] == plain['followups']
    assert '用户填写、未经核实' in r['notice']


def test_reference_must_name_a_property_in_the_goal():
    with pytest.raises(ValueError, match='不存在的性质'):
        analyse(reference=[{'property_id': 'made_up', 'value': '1'}])


def test_report_states_the_anchor_the_constraints_and_the_unverified_values():
    r = analyse(lead={'smiles': EX2.smiles}, constraints={'keep': [{'pattern': DIMETHYLAMIDE, 'label': '二甲酰胺'}],
                                                          'synthesis_notes': ['避免手性拆分']},
                reference=[{'property_id': 'efflux', 'value': '7.6（内部）'}])
    md = ps.report(r)
    assert '先导结构（锚点，不产生数值）' in md and '台账中对应' in md
    assert '必须保留的片段：二甲酰胺' in md and '不作为候选' in md
    assert '合成限制（用户填写，仅作标签，不参与判断）：避免手性拆分' in md
    assert '### 先导化合物当前测量值（用户提供，未核实）' in md and '7.6（内部）' in md
    assert '## 7. 超出约束范围' in md and '## 8. 补测建议' in md and '## 9. 规则' in md


def test_tool_accepts_lead_and_constraints_and_replays(tmp_path):
    from phase0.tools import chem_tools  # noqa: F401
    from phase0.tools.core import Run, replay
    run = Run(runs_dir=tmp_path / 'runs', cache_dir=tmp_path / 'cache')
    r = run.call('project_sar_analyse', {'goal': goal(), 'lead': {'smiles': EX2.smiles},
                                         'constraints': {'keep': [DIMETHYLAMIDE]},
                                         'reference': [{'property_id': 'efflux', 'value': '7.6（内部）'}]})
    assert r['data']['lead']['in_ledger'] and r['data']['reference'][0]['source'] == 'user_unverified'
    assert any(t['category']['key'] == 'out_of_scope' for t in r['data']['transforms'])
    assert replay(run.dir)['faithful']
