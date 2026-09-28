"""Structure search: standardisation is recorded, similarity ranks only, ChEMBL hits are re-checked."""
import io
import json
from urllib.parse import unquote, urlsplit

import pytest
from rdkit import Chem

from phase0.ledger.migrate import build
from phase0.sar import discovery
from phase0.sar import structure_search as ss
from phase0.tools import source_tools  # noqa: F401  (register tools)
from phase0.tools.core import Run, replay

LEDGER = build()
EX2 = next(c for c in LEDGER.compounds if c.id == 'WO2013132376A1:example:2')  # lorlatinib
K8 = next(c for c in LEDGER.compounds if c.label.startswith('8k ·'))  # the same compound in the paper
CRIZOTINIB = next(c for c in LEDGER.compounds if c.label.endswith('Crizotinib'))
AMINOPYRIDINE_ETHER = 'Nc1ncccc1OC(C)c1ccccc1'  # shared by crizotinib and its acyclic analogues


def search(smiles, **kw):
    q, mol = ss.query({'query': smiles, **kw})
    return q, ss.local_search(q, mol, LEDGER)


def test_same_compound_is_found_in_patent_and_paper():
    q, r = search(EX2.smiles, method='exact')
    assert {x['compound_id'] for x in r['rows']} == {EX2.id, K8.id}  # Example 2 = 8k
    assert q['steps'] == [{'step': 'unchanged', 'note': '标准化未改变结构'}]


def test_salt_form_found_only_when_standardised():
    salt = EX2.smiles + '.Cl'
    q, r = search(salt, method='exact')
    assert EX2.id in {x['compound_id'] for x in r['rows']}
    assert [s['step'] for s in q['steps']] == ['largest_fragment']
    assert q['original_smiles'] != q['searched_smiles']
    q, r = search(salt, method='exact', standardize=False)
    assert r['total'] == 0 and q['steps'][0]['step'] == 'disabled'


def test_charged_and_tautomeric_inputs_are_recorded():
    q, _ = search('CC(=O)[O-].[Na+]', method='exact')
    assert [s['step'] for s in q['steps']] == ['largest_fragment', 'neutralized']
    a, _ = search('Oc1ccccn1', method='exact')
    b, _ = search('O=c1cccc[nH]1', method='exact')
    assert a['searched_smiles'] == b['searched_smiles']
    assert 'tautomer' in [s['step'] for s in a['steps'] + b['steps']]


def test_similarity_ranks_and_threshold_filters():
    q, r = search(EX2.smiles, method='similarity', threshold=60)
    sims = [x['similarity'] for x in r['rows']]
    assert sims == sorted(sims, reverse=True) and sims[0] == 1.0 and min(sims) >= 0.6
    assert q['fingerprint'].startswith('Morgan')
    _, strict = search(EX2.smiles, method='similarity', threshold=95)
    assert strict['total'] == 2 < r['total']


def test_substructure_finds_series_and_rejects_tiny_queries():
    q, r = search(AMINOPYRIDINE_ETHER, method='substructure')
    ids = {x['compound_id'] for x in r['rows']}
    assert CRIZOTINIB.id in ids and EX2.id in ids
    assert all(x['match_atoms'] for x in r['rows'])
    assert 'tautomer' not in [s['step'] for s in q['steps']]
    with pytest.raises(ValueError, match='至少需要 6 个重原子'):
        ss.query({'query': 'c1ccco1', 'method': 'substructure'})


@pytest.mark.parametrize('request_patch, message', [
    ({'method': 'fuzzy'}, '检索方式'),
    ({'method': 'similarity', 'threshold': 30}, '阈值'),
    ({'method': 'similarity', 'threshold': 70.5}, '阈值'),
    ({'standardize': 'yes'}, 'standardize'),
    ({'query': 'not a smiles'}, 'SMILES'),
])
def test_invalid_requests_are_refused(request_patch, message):
    with pytest.raises(ValueError, match=message):
        ss.query({'query': EX2.smiles, **request_patch})


def test_rejected_compounds_are_excluded():
    rejected = EX2.model_copy(update={'review': EX2.review.model_copy(update={'record_status': 'rejected'})})
    ledger = LEDGER.model_copy(update={'compounds': [rejected if c.id == EX2.id else c for c in LEDGER.compounds]})
    q, mol = ss.query({'query': EX2.smiles, 'method': 'exact'})
    assert EX2.id not in {x['compound_id'] for x in ss.local_search(q, mol, ledger)['rows']}


# ChEMBL: fixture pages served through urlopen so the real fetch cache is exercised.

REGIO = EX2.smiles.replace('c2ccc(F)cc21', 'c2cc(F)ccc21')  # fluorine moved: similar, not identical
HITS = [
    {'molecule_chembl_id': 'CHEMBL9000001', 'pref_name': 'SAME', 'similarity': '100.0', 'max_phase': 4,
     'molecule_structures': {'canonical_smiles': EX2.smiles, 'standard_inchi_key': 'X'}},
    {'molecule_chembl_id': 'CHEMBL9000002', 'pref_name': None, 'similarity': '81.2', 'max_phase': None,
     'molecule_structures': {'canonical_smiles': 'CCOCC', 'standard_inchi_key': 'Y'}},
    {'molecule_chembl_id': 'CHEMBL9000003', 'pref_name': None, 'similarity': '75.0', 'max_phase': None,
     'molecule_structures': None},
]


def fake_urlopen(calls, total=3, has_next=False):
    def urlopen(request, timeout=None):
        calls.append(request.full_url)
        path = unquote(urlsplit(request.full_url).path)
        assert '/similarity/' in path or '/substructure/' in path, path
        meta = {'total_count': total, 'next': '/next' if has_next else None}
        return io.BytesIO(json.dumps({'molecules': HITS, 'page_meta': meta}).encode())
    return urlopen


def test_chembl_similarity_hits_are_rechecked_locally(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(discovery, 'urlopen', fake_urlopen(calls))
    r = discovery.discover({'mode': 'smiles', 'query': EX2.smiles, 'method': 'similarity', 'threshold': 70,
                            'external': True}, tmp_path)
    assert r['external_status'] == 'ok' and len(calls) == 1
    assert unquote(urlsplit(calls[0]).path).endswith(f"/similarity/{r['search']['searched_smiles']}/70.json")
    checks = {m['molecule_chembl_id']: m['local_check']['status'] for m in r['molecules']}
    assert checks == {'CHEMBL9000001': 'agrees', 'CHEMBL9000002': 'disagrees', 'CHEMBL9000003': 'no_structure'}
    assert r['molecules'][0]['chembl_similarity'] == 100.0
    assert r['truncated'] is False and '未命中不代表不存在' in r['notice']
    assert '子结构检索' in r['notice']  # similarity misses scaffold hops such as macrocyclisation
    assert r['sources'][0]['sha256']


def test_chembl_truncation_and_failure_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, 'urlopen', fake_urlopen([], total=500, has_next=True))
    r = discovery.discover({'mode': 'smiles', 'query': AMINOPYRIDINE_ETHER, 'method': 'substructure',
                            'external': True}, tmp_path)
    assert r['truncated'] is True and r['total'] == 500

    def down(*a, **k):
        raise OSError('blocked')
    monkeypatch.setattr(discovery, 'urlopen', down)
    r = discovery.discover({'mode': 'smiles', 'query': REGIO, 'method': 'similarity', 'external': True},
                           tmp_path / 'fresh')
    assert r['external_status'] == 'failed' and '不代表数据库无结果' in r['warning']
    assert r['ledger_matches']['total'] > 0  # local results still shown


def test_local_only_by_default_and_original_structure_shown(tmp_path):
    r = discovery.discover({'mode': 'smiles', 'query': EX2.smiles + '.Cl', 'method': 'similarity'}, tmp_path)
    assert r['external_status'] == 'not_requested' and r['molecules'] == []
    assert r['original_structure']['smiles'] != r['structure']['smiles']
    assert 'local_matches' not in r  # evidence-card shortcut belongs to exact search only
    assert r['ledger_matches']['rows'][0]['compound_id'] in {EX2.id, K8.id}


def test_tool_result_replays_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, 'urlopen', fake_urlopen([]))
    run = Run(runs_dir=tmp_path / 'runs', cache_dir=tmp_path / 'cache')
    r = run.call('structure_search', {'smiles': EX2.smiles, 'threshold': 70})
    assert r['summary'].startswith('本地命中 ') and 'ChEMBL 命中 3 个' in r['summary']
    assert 'cache_hit' not in r['data']['chembl']['source']
    local = run.call('structure_search', {'smiles': AMINOPYRIDINE_ETHER, 'method': 'substructure',
                                          'external': False})
    assert local['data']['chembl'] is None
    assert Chem.MolFromSmiles(local['data']['search']['searched_smiles']) is not None
    report = replay(run.dir)  # network blocked: must come from the fetch cache
    assert report['faithful'] and report['calls'] == 2


def test_hand_drawn_fixtures_match_curated_structures():
    # The lorlatinib fixture once carried the fluorine on the wrong ring position (a regioisomer);
    # exact structure search exposed it. Chemistry facts in tests are asserted by RDKit, not by eye.
    from phase0.tests.fixtures import SMILES
    key = lambda s: Chem.MolToInchiKey(Chem.MolFromSmiles(s))  # noqa: E731
    assert key(SMILES['lorlatinib']) == key(EX2.smiles) == key(K8.smiles)
    assert key(SMILES['crizotinib']) == key(CRIZOTINIB.smiles)
