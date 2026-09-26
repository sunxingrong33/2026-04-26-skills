import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from phase0.sar import discovery as d
from phase0.sar.serve import create_server

SMILES='C[C@H]1Oc2cc(cnc2N)-c2c(nn(C)c2C#N)CN(C)C(=O)c2ccc(F)cc21'

@pytest.mark.parametrize('value',['','invalid','CCO name','*CC','[Nh]','C'*201,None])
def test_invalid_smiles_never_call_network(value,tmp_path,monkeypatch):
    monkeypatch.setattr(d,'fetch',lambda *args:pytest.fail('unexpected network'))
    with pytest.raises(ValueError):d.discover({'mode':'smiles','query':value,'external':True},tmp_path)


def test_local_search_is_private_and_stereospecific(tmp_path,monkeypatch):
    monkeypatch.setattr(d,'fetch',lambda *args:pytest.fail('unexpected network'))
    r=d.discover({'mode':'smiles','query':SMILES},tmp_path)
    assert r['external_status']=='not_requested'
    assert r['local_matches'][0]['publication']=='WO2013132376A1'
    assert r['local_matches'][0]['example']==2
    other=d.discover({'mode':'smiles','query':SMILES.replace('@H','@@H')},tmp_path)
    assert other['local_matches']==[]


def test_smiles_remote_failure_keeps_local_result_but_not_fake_zero(tmp_path,monkeypatch):
    def fail(*args):raise OSError('offline')
    monkeypatch.setattr(d,'fetch',fail)
    r=d.discover({'mode':'smiles','query':SMILES,'external':True},tmp_path)
    assert r['external_status']=='failed' and r['local_matches']
    assert 'warning' in r and 'total' not in r


def test_remote_query_sends_key_not_raw_smiles(tmp_path,monkeypatch):
    def fetch(endpoint,params,cache):
        assert endpoint=='molecule'
        assert params=={'molecule_structures__standard_inchi_key':'IIXWYSCJSQVBQM-LLVKDONJSA-N','limit':20}
        return {'molecules':[],'page_meta':{'total_count':0,'next':None}},{'url':'source'}
    monkeypatch.setattr(d,'fetch',fetch)
    r=d.discover({'mode':'smiles','query':SMILES,'external':True},tmp_path)
    assert r['external_status']=='ok' and r['total']==0


def test_targets_preserve_organisms_and_do_not_auto_choose(tmp_path,monkeypatch):
    records=[{'target_chembl_id':'CHEMBL4247','organism':'Homo sapiens'}, {'target_chembl_id':'CHEMBL5771','organism':'Mus musculus'}]
    def fetch(endpoint,params,cache):
        assert endpoint=='target/search' and params['q']=='ALK'
        return {'targets':records,'page_meta':{'total_count':2}},{}
    monkeypatch.setattr(d,'fetch',fetch)
    r=d.discover({'mode':'target','query':'ALK'},tmp_path)
    assert r['targets']==records and 'activities' not in r


def test_activity_paging_preserves_bounds_missing_values_and_quality(tmp_path,monkeypatch):
    records=[{'standard_relation':'<','standard_value':'0.2','standard_units':'nM','potential_duplicate':1}, {'standard_value':None}]
    def fetch(endpoint,params,cache):
        assert endpoint=='activity' and params['offset']==20
        assert params['target_chembl_id']=='CHEMBL4247'
        return {'activities':records,'page_meta':{'total_count':99,'next':'next'}},{}
    monkeypatch.setattr(d,'fetch',fetch)
    r=d.discover({'mode':'activities','entity':'target','id':'CHEMBL4247','offset':20},tmp_path)
    assert r['activities']==records and r['has_more']

@pytest.mark.parametrize('query_request',[{'mode':'target','query':''},{'mode':'target','query':'x'*121}, {'mode':'activities','entity':'target','id':'https://example.com'}, {'mode':'activities','entity':'target','id':'CHEMBL4247','offset':-1}, {'mode':'activities','entity':'target','id':'CHEMBL4247','offset':True},{'mode':'unknown'}])
def test_invalid_requests_fail_before_network(query_request,tmp_path,monkeypatch):
    monkeypatch.setattr(d,'fetch',lambda *args:pytest.fail('unexpected network'))
    with pytest.raises(ValueError):d.discover(query_request,tmp_path)


def test_target_source_failure_is_not_empty_result(tmp_path,monkeypatch):
    def fail(*args):raise OSError()
    monkeypatch.setattr(d,'fetch',fail)
    with pytest.raises(RuntimeError):d.discover({'mode':'target','query':'ALK'},tmp_path)


def test_http_discovery_validation_and_origin(tmp_path):
    server=create_server(0,tmp_path/'patents');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/api/discover'
    try:
        r=json.load(urlopen(Request(url,data=json.dumps({'mode':'smiles','query':'CCO'}).encode())))
        assert r['structure']['formula']=='C2H6O'
        for raw,headers,code in [(b'[]',{},400),(b'bad',{},400),(b'{}',{'Origin':'https://evil.example'},403)]:
            with pytest.raises(HTTPError) as exc:urlopen(Request(url,data=raw,headers=headers))
            assert exc.value.code==code
    finally:server.shutdown();server.server_close();thread.join()
