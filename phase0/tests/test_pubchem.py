"""PubChem cross-references, driven by a fake PUG-REST server."""
import io
import json
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit

import pytest

from phase0.sar import discovery, pubchem
from phase0.tools import source_tools  # noqa: F401  (register tools)
from phase0.tools.core import Run, replay

KEY = 'IIXWYSCJSQVBQM-LLVKDONJSA-N'  # lorlatinib, as curated in WO2013132376A1 Example 2
UNKNOWN = 'AAAAAAAAAAAAAA-BBBBBBBBBB-N'
PATENTS = [f'US-{9000000 + i}-B2' for i in range(25)] + ['WO-2013132376-A1', 'WO-2011138751-A2', 'odd id']


class Server:
    def __init__(self, busy=False):
        self.paths, self.busy = [], busy

    def __call__(self, request, timeout=None):
        path = unquote(urlsplit(request.full_url).path)
        self.paths.append(path)
        if self.busy:
            raise HTTPError(request.full_url, 503, 'busy', {}, io.BytesIO(
                json.dumps({'Fault': {'Code': 'PUGREST.ServerBusy'}}).encode()))
        if path.endswith(f'/inchikey/{UNKNOWN}/cids/JSON'):
            raise HTTPError(request.full_url, 404, 'nf', {}, io.BytesIO(
                json.dumps({'Fault': {'Code': 'PUGREST.NotFound', 'Message': 'No CID found'}}).encode()))
        if path.endswith(f'/inchikey/{KEY}/cids/JSON'):
            body = {'IdentifierList': {'CID': [71731823]}}
        elif path.endswith('/cid/71731823/xrefs/PatentID/JSON'):
            body = {'InformationList': {'Information': [{'CID': 71731823, 'PatentID': PATENTS + PATENTS[:2]}]}}
        elif path.endswith('/cid/71731823/xrefs/PubMedID/JSON'):
            body = {'InformationList': {'Information': [{'CID': 71731823, 'PubMedID': [24819116, 25061915, 3]}]}}
        else:
            raise AssertionError(path)
        return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def server(monkeypatch):
    s = Server()
    monkeypatch.setattr(pubchem, 'urlopen', s)
    return s


def test_curated_patents_first_and_everything_counted(server, tmp_path):
    r = discovery.discover({'mode': 'pubchem_xrefs', 'inchikey': KEY}, tmp_path)
    P = r['patents']
    assert r['cids'] == [71731823] and P['total'] == 28 and P['truncated'] is True and len(P['rows']) == 20
    assert P['curated_matches'] == ['WO2011138751A2', 'WO2013132376A1']
    assert [x['publication'] for x in P['rows'][:2]] == ['WO2011138751A2', 'WO2013132376A1']
    assert next(x for x in [*P['rows']] if x['patent_id'] == 'US-9000000-B2')['curated'] is False
    assert [x['pmid'] for x in r['literature']['rows']] == ['25061915', '24819116', '3']  # newest first
    assert len(server.paths) == 3 and len(r['sources']) == 3
    assert '不当作相互独立的佐证' in r['notice'] and 'CID' in r['attribution']


def test_not_found_is_an_answer_and_replays(server, tmp_path):
    r = discovery.discover({'mode': 'pubchem_xrefs', 'inchikey': UNKNOWN}, tmp_path)
    assert r['cids'] == [] and r['patents']['total'] == 0 and len(server.paths) == 1
    discovery.discover({'mode': 'pubchem_xrefs', 'inchikey': UNKNOWN}, tmp_path)
    assert len(server.paths) == 1  # the miss was cached


def test_busy_server_is_an_error_and_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(pubchem, 'urlopen', Server(busy=True))
    with pytest.raises(RuntimeError, match='PubChem 暂不可用'):
        discovery.discover({'mode': 'pubchem_xrefs', 'inchikey': KEY}, tmp_path)
    assert not list((tmp_path / 'pubchem').glob('*.json'))


@pytest.mark.parametrize('bad', [None, 'CCO', 'iixwyscjsqvbqm-llvkdonjsa-n', KEY + 'X'])
def test_only_standard_inchikeys_are_sent(bad, tmp_path):
    with pytest.raises(ValueError, match='InChIKey'):
        discovery.discover({'mode': 'pubchem_xrefs', 'inchikey': bad}, tmp_path)


def test_tool_replays_offline(server, tmp_path):
    run = Run(runs_dir=tmp_path / 'runs', cache_dir=tmp_path / 'cache')
    r = run.call('pubchem_xrefs', {'inchikey': KEY})
    assert r['summary'] == 'CID 71731823：专利 28 份（已整理 2 份），PubMed 文献 3 篇。'
    assert all('cache_hit' not in s for s in r['data']['sources'])
    assert run.call('pubchem_xrefs', {'inchikey': UNKNOWN})['summary'].endswith('未找到 PubChem 化合物。')
    report = replay(run.dir)
    assert report['faithful'] and report['calls'] == 2
