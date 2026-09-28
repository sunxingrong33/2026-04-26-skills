"""SureChEMBL adapter, driven by a fake server that follows the documented job protocol."""
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from phase0.sar import discovery, surechembl
from phase0.sar import structure_search as ss
from phase0.tests.test_structure_search import EX2, REGIO
from phase0.tools import source_tools  # noqa: F401  (register tools)
from phase0.tools.core import Run, replay

HITS = [
    {'id': '100', 'smiles': REGIO, 'inchi_key': 'R', 'similarity': '0.93'},
    {'id': 200, 'smiles': EX2.smiles, 'inchi_key': 'L', 'similarity': 1.0, 'name': 'lorlatinib'},
    {'id': '100', 'smiles': REGIO, 'inchi_key': 'R', 'similarity': '0.93'},  # page repeat
    {'id': '300', 'smiles': 'CCOCC', 'inchi_key': 'X', 'similarity': '0.71'},
    {'id': '400', 'smiles': 'CCN', 'inchi_key': 'Y', 'similarity': '0.55'},
]
DOCS = [
    {'docId': 'WO-2013132376-A1', 'pa': 'PFIZER', 'metadata': {'pd': '20130912', 'titles': [
        {'lang': 'fr', 'titles': ['Dérivés macrocycliques']}, {'lang': 'en', 'titles': ['Macrocyclic derivatives']}]}},
    {'docId': 'CN-ODD', 'pa': 'null', 'metadata': {'pd': 'null'}},
]


class Server:
    def __init__(self, total=4, status_messages=('Start/Loading search...', 'Searching finished.'), fail=None):
        self.calls, self.bodies, self.total = [], [], total
        self.messages = list(status_messages)
        self.fail = fail

    def env(self, data, status='OK', message=None):
        return io.BytesIO(json.dumps({'status': status, 'data': data, 'error_message': message}).encode())

    def __call__(self, request, timeout=None):
        url = urlsplit(request.full_url)
        self.calls.append((request.get_method(), url.path))
        if self.fail:
            raise self.fail
        if url.path.endswith('/search/structure'):
            self.bodies.append(json.loads(request.data))
            return self.env({'hash': 'abc123'})
        if url.path.endswith('/search/abc123/status'):
            message = self.messages.pop(0) if len(self.messages) > 1 else self.messages[0]
            return self.env({'message': message, 'resultCount': self.total})
        if url.path.endswith('/search/abc123/results'):
            return self.env({'results': {'structures': HITS}, 'pagination': {'num_pages': 1}})
        if url.path.endswith('/search/documents_for_structures'):
            assert parse_qs(url.query)['chemicalIds'] == ['200']
            return self.env({'results': {'documents': DOCS, 'total_hits': 57}})
        raise AssertionError(url.path)


@pytest.fixture
def server(monkeypatch):
    s = Server()
    monkeypatch.setattr(surechembl, 'urlopen', s)
    monkeypatch.setattr(surechembl, 'SLEEP', lambda seconds: None)
    return s


def query(**kw):
    return ss.query({'query': EX2.smiles, 'method': 'similarity', 'threshold': 70, **kw})


def test_job_protocol_dedup_and_cache(server, tmp_path):
    payload, source = surechembl.search(EX2.smiles, 'similarity', tmp_path)
    assert server.bodies == [{'StructureSearchRequest': {'struct': EX2.smiles, 'structSearchType': 'similarity'}}]
    assert [p for _, p in server.calls].count('/api/search/abc123/status') == 2  # polled until finished
    assert [r['id'] for r in payload['records']] == ['100', 200, '300', '400']
    assert source['sha256'] and source['cache_hit'] is False and 'abc123' not in json.dumps(source)
    calls = len(server.calls)
    again, source2 = surechembl.search(EX2.smiles, 'similarity', tmp_path)
    assert again == payload and source2['cache_hit'] is True and len(server.calls) == calls


def test_threshold_sort_and_local_recheck(server, tmp_path):
    q, mol = query()
    r = ss.surechembl_search(q, mol, tmp_path)
    assert [x['schembl_id'] for x in r['rows']] == ['SCHEMBL200', 'SCHEMBL100', 'SCHEMBL300']  # by score
    assert r['below_threshold'] == 1
    checks = {x['schembl_id']: x['local_check']['status'] for x in r['rows']}
    assert checks == {'SCHEMBL200': 'agrees', 'SCHEMBL100': 'agrees', 'SCHEMBL300': 'disagrees'}
    assert 'CC BY 4.0' in r['attribution'] and '不说明它是实施例' in r['notice']
    assert r['truncated'] is False and r['capped'] is False


def test_exact_maps_to_identical_and_flags_stereo_differences(server, tmp_path):
    q, mol = ss.query({'query': EX2.smiles, 'method': 'exact'})
    r = ss.surechembl_search(q, mol, tmp_path)
    assert server.bodies[0]['StructureSearchRequest']['structSearchType'] == 'identical'
    assert {x['schembl_id']: x['local_check']['status'] for x in r['rows']}['SCHEMBL100'] == 'disagrees'


def test_server_errors_are_errors(monkeypatch, tmp_path):
    s = Server(status_messages=('Search not complete due to internal error.',))
    monkeypatch.setattr(surechembl, 'urlopen', s)
    with pytest.raises(surechembl.SureChEMBLError, match='检索任务失败'):
        surechembl.search(EX2.smiles, 'substructure', tmp_path)

    def bad(request, timeout=None):
        return io.BytesIO(json.dumps({'status': 'BAD_REQUEST', 'error_message': 'nope'}).encode())
    monkeypatch.setattr(surechembl, 'urlopen', bad)
    with pytest.raises(surechembl.SureChEMBLError, match='BAD_REQUEST'):
        surechembl.documents('SCHEMBL1', tmp_path)
    assert not list(tmp_path.glob('*.json'))  # failures are never cached


def test_documents_map_publication_numbers(server, tmp_path):
    r = discovery.discover({'mode': 'surechembl_documents', 'id': 'SCHEMBL200'}, tmp_path)
    first, odd = r['patents']
    assert first['publication'] == 'WO2013132376A1' and first['title'] == 'Macrocyclic derivatives'
    assert first['publication_date'] == '20130912' and first['assignee'] == 'PFIZER'
    assert odd['publication'] is None and odd['assignee'] is None and odd['publication_date'] is None
    assert r['total'] == 57 and r['truncated'] is True and '核实' in r['notice']
    for bad in ('SCHEMBLx', '0', None):
        with pytest.raises(ValueError):
            discovery.discover({'mode': 'surechembl_documents', 'id': bad}, tmp_path)


def test_discover_keeps_local_results_when_surechembl_fails(monkeypatch, tmp_path):
    r = discovery.discover({'mode': 'smiles', 'query': EX2.smiles, 'method': 'similarity'}, tmp_path)
    assert r['surechembl'] == {'status': 'not_requested'}
    monkeypatch.setattr(surechembl, 'urlopen', Server(fail=OSError('blocked')))
    r = discovery.discover({'mode': 'smiles', 'query': EX2.smiles, 'method': 'similarity', 'surechembl': True},
                           tmp_path)
    assert r['surechembl']['status'] == 'failed' and '不代表 SureChEMBL 无结果' in r['surechembl']['warning']
    assert r['ledger_matches']['total'] > 0


def test_tools_replay_offline(server, tmp_path):
    run = Run(runs_dir=tmp_path / 'runs', cache_dir=tmp_path / 'cache')
    r = run.call('structure_search', {'smiles': EX2.smiles, 'external': False, 'surechembl': True})
    assert 'SureChEMBL 命中 4 个' in r['summary'] and 'cache_hit' not in r['data']['surechembl']['source']
    p = run.call('surechembl_patents', {'compound': 'SCHEMBL200'})
    assert p['summary'].startswith('SCHEMBL200：出现在 57 份专利中')
    assert p['data']['patents'][0]['publication'] == 'WO2013132376A1'
    report = replay(run.dir)  # network blocked, including SureChEMBL: answers come from the cache
    assert report['faithful'] and report['calls'] == 2
