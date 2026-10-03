"""Studies (调研) behind the redesigned workbench: views read the ledger, never store evidence of their own."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from phase0.ledger.migrate import build
from phase0.ledger.store import LedgerStore
from phase0.sar import serve, study

EARLY, LATE = 'WO2011138751A2', 'WO2013132376A1'


@pytest.fixture(scope='module')
def ledger():
    return build()


def test_default_study_counts_match_the_ledger_scope(ledger):
    s = study.summary(ledger, study.DEFAULT_STUDY)
    assert (s['patents'], s['papers'], s['compounds'], s['observations']) == (2, 1, 35, 271)
    assert s['confirmed'] == 0 and s['pending'] == 35 and s['not_tested'] == 2


def test_tiers_come_from_provenance_and_confirmation_lifts_to_l3(ledger):
    by_doc = {c.document_id: study.tier(c) for c in ledger.compounds}
    assert by_doc[EARLY] == 'L2' and by_doc['CHEMBL3286195'] == 'L1'
    c = ledger.compounds[0]
    confirmed = c.model_copy(update={'review': c.review.model_copy(update={'record_status': 'confirmed', 'reviewer': 'x'})})
    assert study.tier(confirmed) == 'L3'


def test_evidence_keeps_source_precision_qualifiers_and_missing_values(ledger):
    ev = study.evidence(ledger, study.DEFAULT_STUDY)
    assert [g['id'] for g in ev['groups']] == [EARLY, LATE, 'CHEMBL3286195']  # patents by priority date, then papers
    early = {r['short']: r for r in ev['groups'][0]['rows']}
    wt = f'{EARLY}:ALK_WT_Ki'
    assert early['Example 1']['values'][wt][0]['text'] == '2.90'  # not 2.9
    ex6 = early['Example 6']['values'][f'{EARLY}:cell_WT_IC50'][0]
    assert ex6['status'] == 'not_tested' and 'value' not in ex6
    late = {r['short']: r for r in ev['groups'][1]['rows']}
    q = late['Example 2']['values'][f'{LATE}:ALK_WT_Ki'][0]
    assert q['qualified'] and q['text'] == '<0.200'
    assert early['Example 1']['mass']['status'] == 'consistent'
    assert len(ev['groups'][2]['assays']) <= 6  # papers show their most measured assays only


def test_cross_document_compare_never_computes_a_ratio(ledger):
    d = study.compare(ledger, f'{EARLY}:example:7', f'{LATE}:example:6')
    assert d['same_document'] is False and d['measurements'] is None
    row = next(p for p in d['parallel'] if p['assay'] == 'WT ALK 酶 Ki')
    assert row['b']['qualified'] and row['a']['text'] == '0.536'
    rot = next(p for p in d['properties'] if p['key'] == 'rotb')
    assert (rot['a'], rot['b'], rot['delta']) == (5, 0, -5)


def test_same_document_compare_gives_ratio_only_for_exact_values(ledger):
    ids = {c.label.split(' ·')[0]: c.id for c in ledger.compounds if c.document_id == 'CHEMBL3286195'}
    d = study.compare(ledger, ids['6f'], ids['6e'])
    by_assay = {m['assay_id']: m for m in d['measurements']}
    assert by_assay['CHEMBL3293161']['ratio'] == pytest.approx(2.8 / 22)
    assert all(m['ratio'] is None for m in d['measurements'] if not (m['a'] and m['b']))


def test_compare_rejects_same_molecule(ledger):
    with pytest.raises(ValueError):
        study.compare(ledger, f'{EARLY}:example:7', f'{EARLY}:example:7')


def test_timeline_reads_family_dates_from_curated_relations_and_aligns_offline(ledger):
    t = study.timeline(ledger, study.DEFAULT_STUDY)
    assert [(f['id'], f['priority_date'], f['family_id']) for f in t['families']] == \
        [(EARLY, '2010-05-04', '44278717'), (LATE, '2012-03-06', '48142828')]
    assert {'from': EARLY, 'to': LATE, 'label': '引用（来源：较晚专利引用列表）', 'type': 'cites'} in t['edges']
    assert len(t['alignment']['rows']) == 6 and all(r['matched'] for r in t['alignment']['rows'])
    assert t['cell'][f'{EARLY}:example:6']['status'] == 'not_tested'


def test_studies_are_created_scoped_and_signed(tmp_path, ledger):
    known = {d.id for d in ledger.documents}
    assert study.load_all(tmp_path)[0]['id'] == study.DEFAULT_ID and not (tmp_path / 'studies.json').exists()
    s = study.create(tmp_path, '  EGFR   第三代 ')
    assert s['name'] == 'EGFR 第三代' and s['documents'] == []
    with pytest.raises(ValueError):
        study.add_documents(tmp_path, s['id'], ['WO0000000000A1'], known)
    assert study.add_documents(tmp_path, s['id'], ['CHEMBL3351341'], known)['added'] == ['CHEMBL3351341']
    assert study.add_documents(tmp_path, s['id'], ['CHEMBL3351341'], known)['already'] == ['CHEMBL3351341']
    with pytest.raises(ValueError):
        study.add_note(tmp_path, s['id'], {'kind': 'hypothesis', 'text': '假设', 'author': ' '})
    note = study.add_note(tmp_path, s['id'], {'kind': 'hypothesis', 'text': '假设', 'author': '张三', 'context': '对照'})
    assert study.get(tmp_path, s['id'])['notes'] == [note]
    assert study.summary(ledger, study.get(tmp_path, s['id']))['compounds'] == 2


def test_review_confirms_compound_and_its_observations_with_a_named_reviewer(tmp_path):
    store = LedgerStore(tmp_path / 'l.sqlite')
    store.import_ledger(build(), 'test')
    cid = f'{LATE}:example:2'
    with pytest.raises(ValueError):
        study.review_compound(store, cid, 'confirmed', '', '理由')
    out = study.review_compound(store, cid, 'confirmed', '李四', '与 PDF p.260 一致')
    assert out['observations'] == 4
    led = store.load()
    assert next(c for c in led.compounds if c.id == cid).review.reviewer == '李四'
    assert {o.review.record_status for o in led.observations if o.compound_id == cid} == {'confirmed'}
    with pytest.raises(ValueError):
        study.review_compound(store, cid, 'rejected', '李四', '再次')
    assert study.tier(next(c for c in led.compounds if c.id == cid)) == 'L3'
    queue = study.review_queue(led, store)
    assert cid not in {i['id'] for i in queue['items']} and queue['counts']['cards'] == 5


def test_report_markdown_lists_pending_status_and_signed_notes(ledger):
    s = {**study.DEFAULT_STUDY, 'notes': [{'kind': 'gap', 'text': '缺 6f 细胞数据', 'author': '王五',
                                           'context': 'SAR 分析', 'created_at': '2026-10-03T00:00:00+00:00'}]}
    md = study.report_markdown(study.report(ledger, s), options={'include_hashes': True})
    assert md.startswith('# 竞对 SAR 调研：ALK 大环系列 · 辉瑞')
    assert '[证据缺口] 缺 6f 细胞数据（王五' in md and '| WO2013132376A1 Example 2 | L2 | 待确认 | PDF p.260 | PDF p.436 |' in md
    assert '88f35d03bdb57f33029a90e5b09f141cd7c457183abb9b3fc09e31df65a93096' in md
    assert '未经具名确认' in md


def test_smiles_and_molfile_are_parsed_locally():
    info = study.smiles_info('C[C@H]1Oc2cc(cnc2N)-c2c(nn(C)c2C#N)CN(C)C(=O)c2ccc(F)cc21')
    assert info['formula'] == 'C21H19FN6O2' and info['inchikey'] == 'IIXWYSCJSQVBQM-LLVKDONJSA-N'
    assert '已指定 R' in info['stereo']
    from rdkit import Chem
    block = Chem.MolToMolBlock(Chem.MolFromSmiles('CCO'))
    assert study.molfile_smiles(block + '$$$$\n' + block + '$$$$\n')['records'] == 2
    with pytest.raises(ValueError):
        study.smiles_info('C1CC')


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(serve.Handler, 'log_message', lambda *a: None)
    monkeypatch.delenv('SAR_LEDGER_DB', raising=False)
    srv = serve.create_server(0, tmp_path / 'cache')
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f'http://127.0.0.1:{srv.server_port}'
    srv.shutdown()
    srv.server_close()


def call(url, body=None):
    req = Request(url, data=None if body is None else json.dumps(body).encode(), method='GET' if body is None else 'POST',
                  headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req) as r:
            return r.status, r.read(), r.headers.get('Content-Type')
    except HTTPError as e:
        return e.code, e.read(), e.headers.get('Content-Type')


@pytest.mark.parametrize('path', ['/', '/search', '/upload', '/review', '/classic', '/classic/evidence', '/classic/project',
                                  '/s/alk-pfizer/overview', '/s/alk-pfizer/evidence', '/s/alk-pfizer/report'])
def test_pages_are_served(server, path):
    status, body, kind = call(server + path)
    assert status == 200 and kind.startswith('text/html') and b'<html' in body


def test_app_assets_are_whitelisted(server):
    assert call(server + '/app/app.css')[0] == 200
    assert call(server + '/app/../serve.py')[0] == 404
    assert call(server + '/app/nope.js')[0] == 404


def test_study_api_and_errors(server):
    status, body, _ = call(server + '/api/studies')
    data = json.loads(body)
    assert status == 200 and data['studies'][0]['id'] == 'alk-pfizer' and data['ledger_writable'] is False
    assert call(server + '/api/study?id=missing')[0] == 404
    assert call(server + '/api/study?id=alk-pfizer&view=bogus')[0] == 400
    status, body, kind = call(server + '/api/depict?smiles=CCO&size=s')
    assert status == 200 and kind == 'image/svg+xml' and body.startswith(b'<?xml')
    status, body, _ = call(server + '/api/review', {'id': f'{LATE}:example:2', 'status': 'confirmed', 'reviewer': 'a', 'note': 'b'})
    assert status == 409  # no ledger database: nothing is saved
    status, body, _ = call(server + '/api/study/compare', {'a': f'{EARLY}:example:7', 'b': f'{LATE}:example:6'})
    assert status == 200 and json.loads(body)['same_document'] is False


def test_saved_analysis_must_run_and_stay_in_scope(server):
    goal = {'label': 'g', 'properties': [{'id': 'p', 'label': 'p', 'direction': 'lower', 'assay_ids': ['CHEMBL3286195:CHEMBL3293161'],
                                          'threshold': {'kind': 'fold', 'value': 2, 'source': 'user'}}]}
    bad = call(server + '/api/study/analysis', {'id': 'alk-pfizer', 'analysis': {'goal': goal, 'documents': ['CHEMBL3351341']}})
    assert bad[0] == 400
    ok = call(server + '/api/study/analysis', {'id': 'alk-pfizer', 'analysis': {'goal': goal, 'documents': ['CHEMBL3286195']}})
    assert ok[0] == 200
    s = json.loads(call(server + '/api/study?id=alk-pfizer')[1])['study']
    assert s['analysis']['goal'] == goal
