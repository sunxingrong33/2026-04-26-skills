from copy import deepcopy
import pytest

from phase0.sar.evidence_ledger import build_ledger
from phase0.sar.evidence_pair import analyse_pair, compare_rows, select_pair


@pytest.fixture
def pair():
    ledger = build_ledger()
    return select_pair(ledger, 'WO2011138751A2:example:6:0', 'WO2011138751A2:example:7:0')


def test_missing_cells_not_zero(pair):
    rows = compare_rows(*pair)
    assert sum(r['ratio_b_over_a'] is not None for r in rows) == 2
    assert all(r['ratio_b_over_a'] is None for r in rows if r['assay_id'].startswith('cell_'))


@pytest.mark.parametrize('field,value', [('document','OTHER'), ('protocol','different'),
    ('endpoint','other'), ('quality_flag','invalid'), ('relation','<'), ('unit','unknown'),
    ('value','NaN'), ('value','0'), ('value','-1')])
def test_comparison_blocks_unsafe_values(pair, field, value):
    a, b = deepcopy(pair[0][0]), deepcopy(pair[1][0])
    b[field] = value
    result = compare_rows([a], [b])[0]
    assert result['ratio_b_over_a'] is None
    assert result['reasons']


def test_units_and_duplicates(pair):
    a, b = deepcopy(pair[0][0]), deepcopy(pair[1][0])
    a.update(value=2, unit='uM')
    b.update(value=1000, unit='nM')
    assert compare_rows([a], [b])[0]['ratio_b_over_a'] == .5
    assert compare_rows([a,a], [b])[0]['ratio_b_over_a'] is None


def test_identical_structure_rejected():
    with pytest.raises(ValueError, match='同一个结构'):
        analyse_pair({'mode':'align','a':'WO2011138751A2:example:6:0','b':'WO2011138751A2:example:6:1'})


def test_paper_pair_alignment_and_comparison():
    ledger = build_ledger()
    records = []
    for cid in ('CHEMBL3286809', 'CHEMBL3286810'):
        records.append(next(r for r in ledger['observations'] if r['compound_id']==cid))
    request = {'mode':'align','a':records[0]['id'],'b':records[1]['id']}
    result = analyse_pair(request)
    assert result['alignment']['unique_molecules'] == 2
    assert result['alignment']['changes']
    request['mode'] = 'compare'
    compared = analyse_pair(request)['measurements']
    assert any(r['ratio_b_over_a'] is not None for r in compared)
    assert all(r['a'] or r['b'] for r in compared)


def test_cross_patent_same_assay_not_comparable():
    rows = analyse_pair({'mode':'compare','a':'WO2011138751A2:example:7:0',
                         'b':'WO2013132376A1:example:6:0'})['measurements']
    assert all(r['ratio_b_over_a'] is None for r in rows)


def test_invalid_request():
    for request in ([], {}, {'mode':'align','a':'missing','b':'missing'}):
        with pytest.raises(ValueError):
            analyse_pair(request)


def test_logarithmic_endpoint_never_ratio(pair):
    a, b = deepcopy(pair[0][0]), deepcopy(pair[1][0])
    a.update(endpoint='LogD', value=2, unit='')
    b.update(endpoint='LogD', value=3, unit='')
    result = compare_rows([a], [b])[0]
    assert result['ratio_b_over_a'] is None
    assert any('对数' in reason for reason in result['reasons'])
