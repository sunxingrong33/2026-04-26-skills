"""Extraction failures must never turn into invented example mappings."""
import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from phase0.sar.patents import normalize_id, parse_patent, retrieve
from phase0.sar.serve import create_server

HTML='''<html><meta name="citation_patent_publication_number" content="WO:2013132376:A1">
<meta name="DC.title" content="A patent"><meta name="DC.date" content="2013-09-12">
<time itemprop="priorityDate" datetime="2012-03-06"></time>
<section itemprop="family"><h2>ID=123</h2><span itemprop="representativePublication">US8680111B2</span></section>
<div class="description-paragraph" num="p1">See Example 2 for details.</div>
<div class="description-paragraph" num="p2">Example 2</div>
<div class="description-paragraph" num="p3">A measured result, not an assigned structure.</div>
<div class="description-paragraph" num="p4">Example 3</div>
<div class="description-paragraph" num="p5">Another section.</div>
<li itemprop="match"><span itemprop="smiles">CCO</span></li>
<li itemprop="match"><span itemprop="smiles">OCC</span></li>
<li itemprop="match"><span itemprop="smiles">*CC</span></li></html>'''

def test_normalize_publication_and_reject_urls():
    assert normalize_id('wo 2013/132376 a1')=='WO2013132376A1'
    for value in ['https://localhost/secrets','../../example','WO2013132376','', '<script>']:
        with pytest.raises(ValueError): normalize_id(value)

def test_parse_keeps_examples_separate_from_chemical_index():
    result=parse_patent(HTML,'WO2013132376A1')
    assert result['priority_date']=='2012-03-06'
    assert result['family_id']=='123'
    assert [e['label'] for e in result['examples']]==['Example 2','Example 3']
    assert 'Another section' not in result['examples'][0]['excerpt']
    assert len(result['structures'])==1
    assert result['structures'][0]['example'] is None
    assert result['invalid_or_query_structures']==1

def test_no_matching_metadata_fails():
    with pytest.raises(ValueError):parse_patent('<html>Access denied</html>','WO2013132376A1')
    with pytest.raises(ValueError):parse_patent(HTML,'US8680111B2')

def test_absent_fields_are_unknown_not_inferred():
    minimal='<meta name="citation_patent_publication_number" content="WO:2013132376:A1">'
    r=parse_patent(minimal,'WO2013132376A1')
    assert r['priority_date']=='' and r['structures']==[] and r['examples']==[]

def test_corrupt_cache_rejected_without_network(tmp_path):
    (tmp_path/'WO2013132376A1.html').write_text(HTML)
    (tmp_path/'WO2013132376A1.source.json').write_text(json.dumps({'sha256':'wrong'}))
    with pytest.raises(ValueError,match='缓存校验失败'):retrieve('WO2013132376A1',tmp_path)

@pytest.fixture
def service(tmp_path,monkeypatch):
    monkeypatch.setattr('phase0.sar.serve.retrieve',lambda *args:parse_patent(HTML,args[0]))
    server=create_server(0,tmp_path)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield server, f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close();thread.join()

def test_http_input_errors_and_cross_origin_rejection(service):
    server,url=service
    for path,headers,status in [('/api/patent?id=bad',{},400),('/api/health',{'Origin':'https://evil.example'},403),('/../../README.md',{},404)]:
        with pytest.raises(HTTPError) as exc:urlopen(Request(url+path,headers=headers))
        assert exc.value.code==status
    from http.client import HTTPConnection
    connection=HTTPConnection('127.0.0.1',server.server_port)
    connection.request('GET','/api/health',headers={'Host':'evil.example'})
    assert connection.getresponse().status==403
    connection.close()

def test_granted_patent_layout():
    html='<dd itemprop="publicationNumber">US8680111B2</dd><meta name="DC.date" scheme="issue" content="2014-03-25"><div class="description"><heading id="h-1">Example 2</heading><div class="description-paragraph">Context</div></div><li itemprop="match"><span itemprop="smiles">[Li]</span></li>'
    result=parse_patent(html,'US8680111B2')
    assert result['publication_date']=='2014-03-25'
    assert result['examples'][0]['locator']=='h-1'
    assert result['invalid_or_query_structures']==1

def test_http_retrieval_and_structure_comparison(service):
    server,url=service
    result=json.load(urlopen(url+'/api/patent?id=WO2013132376A1'))
    assert result['publication']=='WO2013132376A1'
    chosen={'publication':result['publication'],'index':0}
    body=json.dumps({'a':chosen,'b':chosen}).encode()
    with pytest.raises(HTTPError) as exc:
        urlopen(Request(url+'/api/compare',data=body,headers={'Content-Type':'application/json'}))
    assert exc.value.code==400
    assert '同一个分子' in json.load(exc.value)['error']
    # A duplicate record with equivalent SMILES must also be rejected.
    server.results[result['publication']]['structures'].append({**result['structures'][0], 'smiles':'OCC'})
    body=json.dumps({'a':chosen,'b':{**chosen,'index':1}}).encode()
    with pytest.raises(HTTPError) as exc:
        urlopen(Request(url+'/api/compare',data=body))
    assert '同一个分子' in json.load(exc.value)['error']
    with pytest.raises(HTTPError) as exc:
        urlopen(Request(url+'/api/compare',data=b'{"a":{"index":-1}}'))
    assert exc.value.code==400

def test_upstream_error_does_not_return_sample(service,monkeypatch):
    def fail(*args):raise ValueError('upstream failed')
    monkeypatch.setattr('phase0.sar.serve.retrieve',fail)
    _,url=service
    with pytest.raises(HTTPError) as exc:urlopen(url+'/api/patent?id=WO2013132376A1')
    assert exc.value.code==422
    assert json.load(exc.value)=={'error':'upstream failed'}
