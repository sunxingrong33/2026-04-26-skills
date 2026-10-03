"""Mass check catches atom-count errors in transcribed structures, and only those."""
import copy
import json
import pytest
from phase0.eval.extraction_metrics import evaluate
from phase0.eval.gold import GOLD, load
from phase0.sar.mass_check import check_mass, flag_prediction, summarise
from phase0.sar.patent_evidence import attach_evidence, DATA

LATE = load(GOLD / 'WO2013132376A1.json')
EARLY = load(GOLD / 'WO2011138751A2.json')


def smiles(doc, example):
    return next(c['smiles'] for c in doc['compounds'] if c['example'] == example)


def test_all_committed_cards_are_consistent():
    for doc in (EARLY, LATE):
        for c in doc['compounds']:
            r = check_mass(c['smiles'], c['reported_mass'])
            assert r['status'] == 'consistent', (doc['publication'], c['example'], r)


def test_nominal_report_uses_nominal_tolerance_and_rejects_one_hydrogen():
    s = smiles(EARLY, '6')
    ok = check_mass(s, 357)
    assert ok['resolution'] == 'nominal' and ok['delta'] == pytest.approx(-0.1721, abs=1e-4)
    off = check_mass(s, 358)
    assert off['status'] == 'inconsistent' and off['alternatives'] == []


def test_dropped_fluorine_is_inconsistent():
    s = smiles(EARLY, '6').replace('(F)', '')
    assert check_mass(s, 357)['status'] == 'inconsistent'


@pytest.mark.parametrize('report, ion', [(356, 'M'), (379, '[M+Na]+'), (179, '[M+2H]2+')])
def test_other_ions_are_reported_for_review_not_accepted(report, ion):
    r = check_mass(smiles(EARLY, '6'), report)
    assert r['status'] == 'alternative_match'
    assert [a['ion'] for a in r['alternatives']] == [ion]


def test_chlorine_isotope_peak_is_recognised():
    crizotinib = 'C[C@@H](Oc1cc(-c2cnn(C3CCNCC3)c2)cnc1N)c1c(Cl)ccc(F)c1Cl'
    assert check_mass(crizotinib, 450)['status'] == 'consistent'
    r = check_mass(crizotinib, 452)
    assert r['status'] == 'alternative_match'
    assert 'Cl 同位素峰' in r['alternatives'][0]['ion']


def test_high_resolution_uses_ppm():
    s = smiles(EARLY, '6')
    assert check_mass(s, '357.1721')['status'] == 'consistent'
    r = check_mass(s, '357.1800')
    assert r['resolution'] == 'high' and r['status'] == 'inconsistent'


def test_reported_text_is_parsed_and_bad_inputs_are_explicit():
    assert check_mass(smiles(EARLY, '1'), '370.22 (M+H)+')['status'] == 'consistent'
    assert check_mass(smiles(EARLY, '1'), None)['status'] == 'no_report'
    assert check_mass(smiles(EARLY, '1'), 'n/a')['status'] == 'unparsed_report'
    assert check_mass('C1CC', 100)['status'] == 'invalid_structure'
    with pytest.raises(ValueError, match='未知离子'):
        check_mass(smiles(EARLY, '1'), 370, ion='[M+K]+')


def test_stereo_errors_are_invisible_to_mass():
    s = smiles(LATE, '2')
    assert '@' in s
    assert check_mass(s.replace('[C@H]', 'C'), 407)['status'] == 'consistent'


def test_mass_flags_turn_silent_structure_errors_into_flagged_ones():
    pred = copy.deepcopy(EARLY)
    c = next(x for x in pred['compounds'] if x['example'] == '6')
    c['smiles'] = c['smiles'].replace('(F)', '')
    before = evaluate(EARLY, pred)['counts']['unflagged_errors']
    checks = flag_prediction(pred)
    after = evaluate(EARLY, pred)
    assert before == 1 and checks['6']['status'] == 'inconsistent'
    assert c['flags'] == ['mass_mismatch']
    assert after['counts']['errors'] == 1 and after['counts']['unflagged_errors'] == 0


def test_stereo_error_stays_unflagged_after_mass_check():
    pred = copy.deepcopy(LATE)
    c = next(x for x in pred['compounds'] if x['example'] == '2')
    c['smiles'] = c['smiles'].replace('[C@H]', 'C')
    flag_prediction(pred)
    assert 'flags' not in c
    assert evaluate(LATE, pred)['counts']['unflagged_errors'] == 1


def test_evidence_cards_carry_the_mass_check():
    package = json.loads((DATA / 'WO2013132376A1.json').read_text(encoding='utf8'))
    result = {'publication': 'WO2013132376A1', 'source_snapshot': {'sha256': package['source_html_sha256']}}
    attach_evidence(result)
    assert [c['mass_check']['status'] for c in result['evidence_cards']] == ['consistent'] * 3
    assert all(c['mass_check']['summary']['level'] == 'ok' for c in result['evidence_cards'])
    assert all(c['mass_check']['summary']['text'].startswith('质谱校验：一致') for c in result['evidence_cards'])


@pytest.mark.parametrize('reported, level, phrase', [
    (None, 'none', '未报告'),
    ('n/a', 'warn', '无法解析'),
    ('371', 'ok', '一致'),
    ('358', 'warn', '不一致'),
    ('370', 'warn', '与 M 相符'),
])
def test_summary_never_reads_as_confirmation(reported, level, phrase):
    s = summarise(check_mass('CC(Oc1cc(-c2c(C)n(C)nc2C)cnc1N)c1cc(F)ccc1OC', reported))
    assert s['level'] == level and phrase in s['text']
    assert '确认' not in s['text']
