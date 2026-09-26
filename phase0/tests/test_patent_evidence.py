"""Regressions for source-backed, provisional example/measurement mappings."""
import copy
import json
from pathlib import Path
import pytest
from phase0.sar.patent_evidence import DATA, attach_evidence, compare_measurements, provisional_direction

PACKAGE=json.loads((DATA/'WO2013132376A1.json').read_text(encoding='utf8'))

def mapped():
    result={'publication':'WO2013132376A1','source_snapshot':{'sha256':PACKAGE['source_html_sha256']}}
    attach_evidence(result)
    return result

def test_source_snapshot_change_disables_mapping():
    result={'publication':'WO2013132376A1','source_snapshot':{'sha256':'different'}}
    attach_evidence(result)
    assert result['evidence_cards']==[]
    assert '变化' in result['evidence_status']

def test_family_publication_does_not_inherit_example_numbering():
    result={'publication':'US8680111B2','source_snapshot':{'sha256':PACKAGE['source_html_sha256']}}
    attach_evidence(result)
    assert result['evidence_cards']==[]

def test_bounds_never_become_exact_fold_changes():
    cards=mapped()['evidence_cards']
    rows=compare_measurements(cards[1],cards[0])
    assert rows[0]['from']==rows[0]['to']=='<0.200 nM'
    assert rows[0]['ratio'] is None
    assert rows[1]['ratio']==pytest.approx(.78/1.2)
    assert rows[2]['ratio']==pytest.approx(1.33/28.1)
    assert rows[3]['ratio']==pytest.approx(20.7/184)
    assert all('Example 4' in x['source_from']['locator'] and 'Example 2' in x['source_to']['locator'] for x in rows)

def test_protocol_mismatch_blocks_comparison_and_candidate():
    a,b=[copy.deepcopy(c) for c in mapped()['evidence_cards'][:2]]
    for m in b['measurements']:m['assay']['protocol']='different protocol'
    rows=compare_measurements(a,b)
    assert all(r['ratio'] is None for r in rows)
    assert provisional_direction(rows)['status']=='insufficient_evidence'

def test_candidate_reverses_with_selected_direction_and_stays_provisional():
    a,b=mapped()['evidence_cards'][:2]
    assert provisional_direction(compare_measurements(b,a))['status']=='provisional_pending_independent_review'
    assert provisional_direction(compare_measurements(a,b))['status']=='insufficient_evidence'

def test_structures_keep_review_status_and_source_page_identity():
    cards=mapped()['evidence_cards']
    assert [c['example'] for c in cards]==[2,4,6]
    assert [c['formula'] for c in cards]==['C21H19FN6O2','C20H17FN6O2','C20H20FN5O2']
    assert all(c['review_status']=='agent_visual_checked_pending_independent_review' for c in cards)
    assert all(c['table_source']['pdf_page']==436 and c['table_source']['printed_page']==434 for c in cards)
    assert '两个峰' in cards[1]['stereochemistry_note']
