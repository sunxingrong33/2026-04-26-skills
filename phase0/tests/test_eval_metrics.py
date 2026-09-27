"""Extraction metrics must catch the failures a chemist cannot afford to miss."""
import copy
import json
import pytest
from phase0.eval import gold as gold_module
from phase0.eval.extraction_metrics import evaluate, main
from phase0.eval.gold import GOLD, load, validate

EARLY = load(GOLD / 'WO2011138751A2.json')
LATE = load(GOLD / 'WO2013132376A1.json')


def obs(doc, example, assay):
    return next(o for o in doc['observations'] if o['example'] == example and o['assay_id'] == assay)


def cpd(doc, example):
    return next(c for c in doc['compounds'] if c['example'] == example)


def tiny(**compound):
    base = {'example': '1', 'role': 'example', 'smiles': 'CCO'}
    base.update(compound)
    return {'schema_version': 1, 'publication': 'WO0000000A1', 'compounds': [base], 'observations': []}


def test_committed_gold_matches_evidence_packages():
    assert gold_module.main(['--check']) == 0


def test_gold_scored_against_itself_is_perfect():
    for doc in (EARLY, LATE):
        report = evaluate(doc, copy.deepcopy(doc))
        assert report['errors'] == report['missed'] == report['comparability'] == []
        assert all(r['value'] == 1 for r in report['rates'].values() if r['total'])


def test_dropped_qualifier_is_an_unflagged_error_and_enables_a_false_ratio():
    pred = copy.deepcopy(LATE)
    for ex in ('2', '4'):
        o = obs(pred, ex, 'ALK_WT_Ki')
        o['relation'], o['raw'] = '=', '0.200 nM'
    report = evaluate(LATE, pred)
    assert [e['kind'] for e in report['errors']] == ['dropped_qualifier', 'dropped_qualifier']
    assert report['counts']['unflagged_errors'] == 2
    assert report['counts']['false_comparable'] == 1
    assert report['comparability'][0]['pair'] == ['2', '4']
    assert report['rates']['measurement_exact']['hit'] == 10


def test_low_confidence_error_is_counted_but_not_as_unflagged():
    pred = copy.deepcopy(EARLY)
    o = obs(pred, '1', 'ALK_WT_Ki')
    o['value'], o['confidence'] = 29.0, 'low'
    report = evaluate(EARLY, pred)
    assert report['counts']['errors'] == 1
    assert report['counts']['unflagged_errors'] == 0
    flagged_by_list = copy.deepcopy(EARLY)
    o = obs(flagged_by_list, '1', 'ALK_WT_Ki')
    o['value'], o['flags'] = 29.0, ['mass_mismatch']
    assert evaluate(EARLY, flagged_by_list)['counts']['unflagged_errors'] == 0


def test_number_for_untested_cell_is_fabrication_and_false_comparability():
    pred = copy.deepcopy(EARLY)
    o = obs(pred, '6', 'cell_WT_IC50')
    o.update(status='measured', relation='=', value=40.0, unit='nM', note=None)
    report = evaluate(EARLY, pred)
    assert report['counts']['fabricated_values'] == 1
    assert report['errors'][0]['kind'] == 'fabricated_value'
    assert {tuple(c['pair']) for c in report['comparability']} == {('1', '6'), ('6', '7')}
    assert report['counts']['false_comparable'] == 2


def test_wrong_missing_status_is_not_fabrication():
    pred = copy.deepcopy(EARLY)
    obs(pred, '6', 'cell_WT_IC50')['status'] = 'blank'
    report = evaluate(EARLY, pred)
    assert report['counts']['fabricated_values'] == 0
    assert [e['kind'] for e in report['errors']] == ['missing_semantics']
    assert report['rates']['missing_semantics']['hit'] == 1


def test_equivalent_units_are_correct():
    pred = copy.deepcopy(EARLY)
    o = obs(pred, '1', 'ALK_WT_Ki')
    o['value'], o['unit'] = 0.0029, 'µM'
    assert evaluate(EARLY, pred)['counts']['errors'] == 0


def test_lost_stereochemistry_keeps_connectivity_but_fails_exact_structure():
    pred = copy.deepcopy(LATE)
    c = cpd(pred, '2')
    assert '@' in c['smiles']
    c['smiles'] = c['smiles'].replace('[C@H]', 'C')
    report = evaluate(LATE, pred)
    assert report['rates']['structure_exact']['hit'] == 2
    assert report['rates']['structure_connectivity']['hit'] == 3
    assert report['errors'][0]['kind'] == 'structure'


def test_unparseable_smiles_is_a_structure_error_not_a_crash():
    report = evaluate(tiny(), tiny(smiles='C1CC'))
    assert report['rates']['structure_exact']['hit'] == 0
    assert report['rates']['structure_connectivity']['hit'] == 0


def test_intermediate_labelled_as_example_is_a_role_error():
    report = evaluate(tiny(), tiny(role='intermediate'))
    assert [e['kind'] for e in report['errors']] == ['role']


def test_empty_prediction_is_missed_not_wrong():
    empty = {'schema_version': 1, 'publication': EARLY['publication'], 'compounds': [], 'observations': []}
    report = evaluate(EARLY, empty)
    assert report['errors'] == []
    assert report['rates']['compound_recall']['value'] == 0
    assert report['rates']['compound_precision']['total'] == 0
    assert report['counts']['missed'] == 3 + 12
    assert report['counts']['missed_comparable'] > 0


def test_spurious_records_are_counted():
    pred = copy.deepcopy(EARLY)
    pred['compounds'].append({'example': '99', 'role': 'example', 'smiles': 'CCN'})
    pred['observations'].append({'example': '99', 'assay_id': 'ALK_WT_Ki', 'status': 'measured',
                                 'relation': '=', 'value': 1.0, 'unit': 'nM'})
    report = evaluate(EARLY, pred)
    assert {e['kind'] for e in report['errors']} == {'spurious_compound', 'spurious_observation'}
    assert report['counts']['fabricated_values'] == 1
    assert report['rates']['compound_precision']['value'] == pytest.approx(3 / 4)


def test_graded_values_compare_by_grade_and_never_enter_ratios():
    g = tiny()
    g['observations'] = [{'example': '1', 'assay_id': 'AMY3R', 'status': 'measured', 'relation': 'grade', 'grade': 'A'}]
    g['compounds'].append({'example': '2', 'role': 'example', 'smiles': 'CCN'})
    g['observations'].append({'example': '2', 'assay_id': 'AMY3R', 'status': 'measured', 'relation': 'grade', 'grade': 'A'})
    assert evaluate(g, copy.deepcopy(g))['comparability'] == []
    wrong = copy.deepcopy(g)
    wrong['observations'][0]['grade'] = 'B'
    assert [e['kind'] for e in evaluate(g, wrong)['errors']] == ['value']


@pytest.mark.parametrize('mutate, message', [
    (lambda d: d['observations'].append(dict(d['observations'][0])), '观测重复'),
    (lambda d: d['observations'][0].update(unit=None), '缺少单位'),
    (lambda d: d['observations'][0].update(value=float('nan')), '有限数值'),
    (lambda d: obs(d, '6', 'cell_WT_IC50').update(value=0), '不能带数值'),
    (lambda d: d['observations'][0].update(relation='grade', grade='A'), '不能同时给出连续数值'),
    (lambda d: d['compounds'][0].update(confidence='sure'), 'confidence 无效'),
    (lambda d: d['compounds'][0].update(role='product'), 'role 无效'),
])
def test_malformed_documents_are_rejected(mutate, message):
    doc = copy.deepcopy(EARLY)
    mutate(doc)
    with pytest.raises(ValueError, match=message):
        validate(doc)


def test_publication_mismatch_is_rejected():
    with pytest.raises(ValueError, match='公开号不一致'):
        evaluate(EARLY, copy.deepcopy(LATE))


def test_cli_scores_directories_and_gates_on_unflagged_errors(tmp_path, capsys):
    pred = copy.deepcopy(LATE)
    obs(pred, '2', 'ALK_WT_Ki')['relation'] = '='
    (tmp_path / 'WO2013132376A1.json').write_text(json.dumps(pred, ensure_ascii=False), encoding='utf8')
    out = tmp_path / 'report.json'
    assert main(['--pred', str(tmp_path), '--json', str(out), '--max-unflagged', '0']) == 1
    text = capsys.readouterr().out
    assert '未标记错误数: 1' in text and '尚未完成独立复核' in text
    summary = json.loads(out.read_text(encoding='utf8'))['summary']
    assert summary['publications'] == ['WO2011138751A2', 'WO2013132376A1']
    assert summary['rates']['compound_recall']['hit'] == 3
    assert main(['--pred', str(tmp_path), '--max-unflagged', '1']) == 0
