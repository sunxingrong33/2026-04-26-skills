"""Adding online search results to the ledger: server-held data only, proposed only, refuse over guess."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from phase0.ledger import access
from phase0.ledger.migrate import build
from phase0.ledger.store import LedgerStore
from phase0.sar.patents import parse_patent
from phase0.tests.test_patents import HTML

COMMITTED_ACTIVITY = 14722315  # already in the curated lorlatinib package


def activity(aid, **patch):
    record = {'activity_id': aid, 'record_id': 1, 'document_chembl_id': 'CHEMBL9000001',
              'molecule_chembl_id': 'CHEMBL9100001', 'molecule_pref_name': 'demo',
              'canonical_smiles': 'Oc1ccccc1', 'assay_chembl_id': 'CHEMBL9200001',
              'assay_description': 'demo kinase assay', 'standard_type': 'IC50',
              'standard_relation': '=', 'standard_value': '12.0', 'standard_units': 'nM',
              'data_validity_comment': None, 'target_chembl_id': 'CHEMBL4247'}
    record.update(patch)
    return record


PAGE = [
    activity(1),
    activity(2, standard_relation='<', standard_value='5'),
    activity(3, standard_relation=None, standard_value=None, standard_units=None),
    activity(4, standard_relation=None, standard_value='3.0'),
    activity(5, document_chembl_id='CHEMBL9000002'),
    activity(6, canonical_smiles=None),
    activity(7, standard_type='Ki'),
    activity(8, document_chembl_id='CHEMBL9000003', molecule_chembl_id='CHEMBL9100003'),
    activity(COMMITTED_ACTIVITY, document_chembl_id='CHEMBL3286195'),
]
DOCS = [{'document_chembl_id': 'CHEMBL9000001', 'doc_type': 'PUBLICATION', 'doi': '10.1000/demo', 'year': 2020},
        {'document_chembl_id': 'CHEMBL9000002', 'doc_type': 'DATASET'},
        {'document_chembl_id': 'CHEMBL9000003', 'doc_type': 'PATENT', 'patent_id': 'WO-2020000000-A1'},
        {'document_chembl_id': 'CHEMBL3286195', 'doc_type': 'PUBLICATION'}]


@pytest.fixture
def fake_chembl(monkeypatch):
    def fetch(endpoint, params, cache):
        source = {'url': endpoint, 'sha256': 'f' * 64, 'retrieved_at': 'now', 'cache_hit': True}
        if endpoint == 'activity':
            return {'activities': PAGE, 'page_meta': {'total_count': len(PAGE)}}, source
        if endpoint == 'document':
            return {'documents': DOCS}, source
        raise AssertionError(endpoint)
    monkeypatch.setattr('phase0.sar.discovery.fetch', fetch)


@pytest.fixture
def server(tmp_path, monkeypatch):
    from phase0.sar.serve import create_server
    monkeypatch.delenv(access.ENV, raising=False)
    store = LedgerStore(tmp_path / 'ledger.sqlite')
    store.import_ledger(build(), 'migration')
    srv = create_server(0, tmp_path / 'patent-cache', ledger_db=str(store.path))
    srv.store = store
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join()


def call(srv, path, body=None):
    url = f'http://127.0.0.1:{srv.server_port}{path}'
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urlopen(Request(url, data=data, headers={'Content-Type': 'application/json'})) as r:
            return r.status, json.load(r)
    except HTTPError as exc:
        return exc.code, json.load(exc)


ACTIVITY_REQUEST = {'source': 'activities', 'entity': 'target', 'id': 'CHEMBL4247', 'offset': 0,
                    'activity_ids': [a['activity_id'] for a in PAGE]}


def test_disabled_store_writes_nothing(tmp_path, monkeypatch):
    from phase0.sar.serve import create_server
    monkeypatch.delenv(access.ENV, raising=False)
    srv = create_server(0, tmp_path / 'patent-cache')
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        assert call(srv, '/api/ledger/status')[1]['enabled'] is False
        status, body = call(srv, '/api/ledger/propose', {'source': 'patent', 'publication': 'WO2013132376A1'})
        assert status == 409 and '未写入' in body['error']
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()


def test_activities_are_proposed_and_every_refusal_has_a_reason(server, fake_chembl):
    status, body = call(server, '/api/ledger/propose', ACTIVITY_REQUEST)
    assert status == 200
    assert body['added'] == {'documents': 2, 'compounds': 2, 'assays': 2, 'observations': 4}
    assert body['already_present'] == [str(COMMITTED_ACTIVITY)]
    reasons = {r['id']: r['reason'] for r in body['refused']}
    assert '限定符缺失' in reasons['4'] and '文档类型未核实' in reasons['5']
    assert '结构' in reasons['6'] and '另一终点' in reasons['7']
    ledger = server.store.load()
    new = {o.id: o for o in ledger.observations if o.id.startswith('chembl:')}
    assert {o.review.record_status for o in new.values()} == {'proposed'}
    assert (new['chembl:activity:1'].relation, new['chembl:activity:1'].value) == ('=', 12.0)
    assert (new['chembl:activity:2'].relation, new['chembl:activity:2'].value) == ('<', 5.0)
    assert new['chembl:activity:3'].status == 'not_reported' and new['chembl:activity:3'].value is None
    assert {c.role for c in ledger.compounds if c.id.startswith('CHEMBL9000')} == {'unspecified'}
    patent_doc = next(d for d in ledger.documents if d.id == 'CHEMBL9000003')
    assert patent_doc.kind == 'patent'
    assert {e['actor'] for e in server.store.audit_log() if e['action'] == 'propose'} == {'workbench'}


def test_repeating_the_same_request_adds_nothing(server, fake_chembl):
    call(server, '/api/ledger/propose', ACTIVITY_REQUEST)
    status, body = call(server, '/api/ledger/propose', ACTIVITY_REQUEST)
    assert status == 200 and body['added'] == {}
    assert set(body['already_present']) == {'1', '2', '3', '8', str(COMMITTED_ACTIVITY)}


def test_values_sent_by_the_page_are_ignored(server, fake_chembl):
    forged = {**ACTIVITY_REQUEST, 'activity_ids': [1, 424242],
              'activities': [activity(1, standard_value='0.001')], 'value': 0.001}
    status, body = call(server, '/api/ledger/propose', forged)
    assert status == 200
    assert body['refused'] == [{'id': '424242', 'reason': '不在服务端读取的该页结果中'}]
    obs = next(o for o in server.store.load().observations if o.id == 'chembl:activity:1')
    assert obs.value == 12.0


def test_proposed_measurements_reach_the_analysis_view(server, fake_chembl, monkeypatch):
    call(server, '/api/ledger/propose', ACTIVITY_REQUEST)
    monkeypatch.setenv(access.ENV, str(server.store.path))
    status, view = call(server, '/api/evidence')
    assert status == 200 and view['summary']['observations'] == 324
    rows = {r['id']: r for r in view['observations']}
    assert rows['chembl:activity:1']['value'] == '12.0' and rows['chembl:activity:1']['kind'] == 'paper'
    assert rows['chembl:activity:8']['kind'] == 'patent' and rows['chembl:activity:8']['source_html_sha256'] is None
    assert rows['chembl:activity:3']['value_status'] == 'missing'


def test_patent_needs_a_prior_server_side_lookup(server):
    status, body = call(server, '/api/ledger/propose', {'source': 'patent', 'publication': 'WO2013132376A1'})
    assert status == 400 and '请先检索' in body['error']


def test_patent_structure_index_is_proposed_without_example_mapping(server):
    result = parse_patent(HTML, 'WO2013132376A1')  # fixture page; re-labelled below as a new publication
    result['source_snapshot'] = {'sha256': 'a' * 64}
    result['publication'] = 'WO2099999999A1'
    server.results['WO2099999999A1'] = result
    status, body = call(server, '/api/ledger/propose', {'source': 'patent', 'publication': 'WO2099999999A1'})
    assert status == 200 and body['added'] == {'documents': 1, 'compounds': 1}
    compound = next(c for c in server.store.load().compounds if c.document_id == 'WO2099999999A1')
    assert compound.role == 'unspecified' and 'structure_example_mapping' in compound.review.gaps
    assert compound.mapping_source.locator == '未映射实施例'
    again = call(server, '/api/ledger/propose', {'source': 'patent', 'publication': 'WO2099999999A1'})[1]
    assert again['added'] == {}


def test_status_counts_by_record_status(server, fake_chembl):
    call(server, '/api/ledger/propose', ACTIVITY_REQUEST)
    body = call(server, '/api/ledger/status')[1]
    assert body['enabled'] is True
    assert body['counts']['observations'] == {'proposed': 324}


@pytest.mark.parametrize('body', [
    {'source': 'upload'},
    {'source': 'activities', 'entity': 'target', 'id': 'CHEMBL4247', 'offset': 0, 'activity_ids': []},
    {'source': 'activities', 'entity': 'target', 'id': 'CHEMBL4247', 'offset': 0, 'activity_ids': list(range(21))},
])
def test_malformed_requests_are_rejected(server, fake_chembl, body):
    assert call(server, '/api/ledger/propose', body)[0] == 400
