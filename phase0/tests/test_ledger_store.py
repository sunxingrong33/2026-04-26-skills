"""SQLite ledger store: write contract, audit trail, and analyses reading through it."""
import json
import sqlite3
import threading
from urllib.request import urlopen

import pytest
from pydantic import ValidationError

from phase0.ledger import access
from phase0.ledger.migrate import build
from phase0.ledger.store import LedgerStore, main
from phase0.sar.evidence_ledger import build_ledger

OBS = 'WO2011138751A2:example:1:0'


@pytest.fixture
def store(tmp_path):
    s = LedgerStore(tmp_path / 'ledger.sqlite')
    s.import_ledger(build(), 'migration')
    return s


def proposal(**patch):
    record = {'id': 'agent:demo:1', 'document_id': 'WO2011138751A2',
              'compound_id': 'WO2011138751A2:example:1', 'assay_id': 'WO2011138751A2:ALK_WT_Ki',
              'status': 'measured', 'relation': '=', 'value': 12.0, 'unit': 'nM',
              'source': {'pdf_page': 186, 'locator': 'agent extraction'}, 'raw': {},
              'review': {'record_status': 'proposed', 'provenance_status': 'agent_extraction'}}
    record.update(patch)
    return record


def test_store_round_trip_matches_committed_evidence(store):
    assert access.analysis_view(store.load()) == build_ledger()
    assert store.audit_log()[0]['action'] == 'import'


def test_empty_store_and_anonymous_writes_are_refused(tmp_path):
    empty = LedgerStore(tmp_path / 'empty.sqlite')
    with pytest.raises(LookupError, match='为空'):
        empty.load()
    with pytest.raises(ValueError, match='操作者'):
        empty.import_ledger(build(), '')


def test_proposals_cannot_arrive_confirmed(store):
    confirmed = proposal(review={'record_status': 'confirmed', 'provenance_status': 'agent', 'reviewer': 'bot'})
    with pytest.raises(PermissionError, match='只能是 proposed'):
        store.propose('observations', confirmed, 'agent')


def test_proposals_cannot_overwrite_or_dangle(store):
    with pytest.raises(ValueError, match='已存在编号'):
        store.propose('observations', proposal(id=OBS), 'agent')
    with pytest.raises(ValidationError, match='不存在的化合物'):
        store.propose('observations', proposal(compound_id='nope'), 'agent')
    with pytest.raises(ValidationError, match='不能带数值'):
        store.propose('observations', proposal(status='not_tested'), 'agent')
    assert len(store.load().observations) == 320
    assert [e['action'] for e in store.audit_log()] == ['import']


def test_valid_proposal_is_stored_audited_and_visible(store):
    store.propose('observations', proposal(), 'extractor-v0', note='test run')
    view = access.analysis_view(store.load())
    row = next(r for r in view['observations'] if r['id'] == 'agent:demo:1')
    assert (row['relation'], row['value'], row['unit']) == ('=', 12.0, 'nM')
    assert row['review_status'] == 'agent_extraction'
    entry = store.audit_log('agent:demo:1')[0]
    assert entry['actor'] == 'extractor-v0' and entry['before_sha256'] is None and entry['after_sha256']


def test_review_needs_reviewer_and_reason(store):
    with pytest.raises(ValueError, match='复核人和理由'):
        store.review('observations', OBS, 'confirmed', '', 'looks fine')
    with pytest.raises(ValueError, match='复核人和理由'):
        store.review('observations', OBS, 'confirmed', 'chemist-a', '')
    with pytest.raises(ValueError, match='只能是 confirmed 或 rejected'):
        store.review('observations', OBS, 'proposed', 'chemist-a', 'reset')
    with pytest.raises(LookupError):
        store.review('observations', 'nope', 'confirmed', 'chemist-a', 'x')


def test_confirmation_is_recorded_with_before_and_after(store):
    item = store.review('observations', OBS, 'confirmed', 'chemist-a', '对照 PDF p.186 核对')
    assert item.review.record_status == 'confirmed' and item.review.reviewer == 'chemist-a'
    entry = store.audit_log(OBS)[-1]
    assert entry['action'] == 'confirmed' and entry['actor'] == 'chemist-a'
    assert entry['before_sha256'] != entry['after_sha256']
    assert next(o for o in store.load().observations if o.id == OBS).review.record_status == 'confirmed'


def test_rejected_records_leave_analysis_but_stay_in_the_store(store):
    store.review('observations', OBS, 'rejected', 'chemist-a', '数值抄错')
    ledger = store.load()
    assert any(o.id == OBS for o in ledger.observations)
    view = access.analysis_view(ledger)
    assert OBS not in {r['id'] for r in view['observations']}
    assert view['summary']['observations'] == 319


def test_rejecting_a_compound_hides_all_its_observations(store):
    store.review('compounds', 'WO2011138751A2:example:6', 'rejected', 'chemist-a', '结构转录有误')
    view = access.analysis_view(store.load())
    assert not [r for r in view['observations'] if r['compound_id'] == 'WO2011138751A2:example:6']
    assert view['summary']['observations'] == 316


def test_default_reads_committed_evidence_and_env_opts_into_store(store, monkeypatch):
    monkeypatch.delenv(access.ENV, raising=False)
    assert access.analysis_view() == build_ledger()
    store.review('observations', OBS, 'rejected', 'chemist-a', '数值抄错')
    monkeypatch.setenv(access.ENV, str(store.path))
    assert access.analysis_view()['summary']['observations'] == 319


def test_api_and_pair_analysis_read_through_the_store(store, monkeypatch):
    from phase0.sar.evidence_pair import analyse_pair
    from phase0.sar.serve import create_server
    request = {'mode': 'compare', 'a': 'WO2011138751A2:example:1:1', 'b': 'WO2011138751A2:example:7:0'}
    wt = lambda r: next(m for m in r['measurements'] if m['assay_id'] == 'ALK_WT_Ki')
    monkeypatch.delenv(access.ENV, raising=False)
    before = wt(analyse_pair(request))
    assert before['status'] == 'provisional_comparable' and before['a'][0]['id'] == OBS

    store.review('observations', OBS, 'rejected', 'chemist-a', '数值抄错')
    monkeypatch.setenv(access.ENV, str(store.path))
    after = wt(analyse_pair(request))
    assert after['a'] == [] and after['status'] == 'blocked' and after['ratio_b_over_a'] is None

    server = create_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/evidence') as response:
            assert json.load(response)['summary']['observations'] == 319
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_status_reports_staleness(store, capsys):
    assert main(['status', '--db', str(store.path)]) == 0
    with sqlite3.connect(store.path) as con:
        envelope = json.loads(con.execute("SELECT value FROM meta WHERE key='envelope'").fetchone()[0])
        envelope['inputs'][0]['sha256'] = 'old'
        con.execute("UPDATE meta SET value=? WHERE key='envelope'", (json.dumps(envelope),))
    assert main(['status', '--db', str(store.path)]) == 2
    assert '"stale_vs_committed_data": true' in capsys.readouterr().out
