import json
from copy import deepcopy
import pytest
from phase0.sar.sar_workflow import build_summary, save_review, review_history, suggest
from phase0.sar.evidence_ledger import build_ledger


@pytest.fixture(scope='module')
def pairs():
    rows = build_ledger()['observations']
    a = next(r for r in rows if r['compound_id']=='CHEMBL3286809')
    b = next(r for r in rows if r['compound_id']=='CHEMBL3286810')
    return [{'a':a['id'], 'b':b['id']}]


@pytest.fixture(scope='module')
def report(pairs):
    return build_summary(pairs)


def test_summary_dedup_and_evidence(pairs, report):
    doubled = build_summary(pairs + [{'a':pairs[0]['b'], 'b':pairs[0]['a']}])
    assert doubled['pair_count'] == 1
    assert doubled['groups'] == report['groups']
    assert report['excluded']
    assert all(g['source_documents'] == ['CHEMBL3286195'] for g in report['groups'])
    assert all(not g['independent_replication_confirmed'] for g in report['groups'])
    assert report['report_id'] == build_summary(pairs)['report_id']


def test_suggestions_support_counterexamples_and_abstention(report):
    smiles = report['groups'][0]['evidence'][0]['a']['smiles']
    lower = suggest(report, smiles, 'CHEMBL3293161', 'lower', [])
    assert lower['status'] == 'insufficient_evidence'
    assert lower['counterexamples']
    higher = suggest(report, smiles, 'CHEMBL3293161', 'higher', [])
    assert len(higher['candidates']) == 1
    assert higher['candidates'][0]['evidence']['warnings']
    assert suggest(report, 'CCO', 'CHEMBL3293161','higher',[])['status']=='insufficient_evidence'
    assert suggest(report, smiles,'CHEMBL3293161','higher',[{'decision':'reject'}])['status']=='review_blocked'


def test_reviews_persist_with_snapshot_and_do_not_change_evidence(report,tmp_path):
    original = deepcopy(report)
    request = {'report_id':report['report_id'],'reviewer':'test reviewer','reason':'test only','decision':'needs_evidence'}
    save_review(report,request,tmp_path)
    assert len(review_history(report['report_id'],tmp_path))==1
    assert json.loads((tmp_path/report['report_id']/'report.json').read_text(encoding='utf-8'))==report
    assert report==original
    request['decision']='accept'
    save_review(report,request,tmp_path)
    assert len(review_history(report['report_id'],tmp_path))==2
    request['report_id']='stale'
    with pytest.raises(ValueError,match='变化'):
        save_review(report,request,tmp_path)
    request.update(report_id=report['report_id'],reviewer=' ')
    with pytest.raises(ValueError):
        save_review(report,request,tmp_path)


def test_cross_source_cannot_become_suggestion():
    result=build_summary([{'a':'WO2011138751A2:example:7:0','b':'WO2013132376A1:example:6:0'}])
    assert not result['groups']
    assert result['excluded']


def test_request_limits():
    for pairs in (None, [], [{}]*9):
        with pytest.raises(ValueError):
            build_summary(pairs)
