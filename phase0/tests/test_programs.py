"""Program grouping and chemistry regressions; no network or known-story overrides."""
import copy
import json
from pathlib import Path
import pytest
from phase0.sar.programs import analyse_programs, assignee_name
from phase0.sar.scaffolds import align_evidence, evidence_molecules
from phase0.sar.patent_evidence import DATA, attach_evidence


def doc(pid='WO2020000001A1', family='1', smiles=('Cc1ccncc1','CCc1ccncc1'), assignee='Pfizer Inc', title='ALK inhibitors'):
    return {'publication':pid,'family_id':family,'priority_date':'2020-01-01','assignee':assignee,
        'inventors':['Jane Smith','John Doe'],'title':title,'abstract':'', 'source_url':'https://example.org/'+pid,
        'source_snapshot':{'sha256':'example-fixture'},'evidence_cards':[
            {'example':i+1,'label':f'Example {i+1}','smiles':s,'structure_source':{'url':'https://example.org/source'},'review_status':'fixture'} for i,s in enumerate(smiles)]}


def test_same_program_alias_and_order_invariance():
    a,b=doc(),doc('WO2020000002A1','2',assignee='PFIZER INC.')
    b['inventors']=['JOHN DOE','jane smith']
    result=analyse_programs([b,a]);other=analyse_programs([a,b])
    assert result['programs']==other['programs']
    assert result['pairs'][0]['eligible']
    assert result['pairs'][0]['signals']['inventors']==1
    assert len(result['programs'])==1


def test_different_assignee_is_hard_gate():
    a,b=doc(),doc('WO2020000002A1','2',assignee='Unrelated Company')
    r=analyse_programs([a,b])
    assert not r['pairs'][0]['eligible'] and len(r['programs'])==2


def test_missing_evidence_never_uses_entity_index():
    a,b=doc(),doc('WO2020000002A1','2',smiles=())
    b['structures']=a['evidence_cards']
    r=analyse_programs([a,b])
    assert not r['pairs'][0]['eligible']
    assert r['families'][1]['evidence_molecules']==0


def test_priority_changes_order_not_score_or_membership():
    a,b=doc(),doc('WO2020000002A1','2')
    before=analyse_programs([a,b]);b['priority_date']='1990-01-01';after=analyse_programs([a,b])
    assert before['pairs']==after['pairs']
    assert after['programs'][0]['families'][0]=='family:2'


def test_same_family_is_one_node_and_unique_structures_not_recounted():
    r=analyse_programs([doc(),doc('WO2020000002A1','1')])
    assert len(r['families'])==1 and r['pairs']==[]
    assert r['families'][0]['evidence_molecules']==2


def test_complete_link_blocks_transitive_bridge():
    from phase0.sar.programs import CONFIG
    import yaml
    config=yaml.safe_load(CONFIG.read_text(encoding='utf8'))
    config.update(weights={'inventors':0,'scaffolds':0,'targets':1},threshold=.4)
    a,b,c=doc(title='ALK'),doc('WO2020000002A1','2',title='ALK EGFR'),doc('WO2020000003A1','3',title='EGFR')
    r=analyse_programs([a,b,c],config)
    assert sorted(len(p['families']) for p in r['programs'])==[1,2]


def test_single_generic_ring_does_not_enable_fallback():
    a,b=doc(smiles=('Cc1ccccc1',)),doc('WO2020000002A1','2',smiles=('Oc1ccccc1',))
    p=analyse_programs([a,b])['pairs'][0]
    assert p['scaffold_detail']['shared_informative_rings']==[]
    assert p['scaffold_detail']['method']=='network_jaccard'


def test_r_group_variants_and_source_rows_are_preserved():
    result=align_evidence([doc()])
    assert result['status']=='provisional_alignment'
    assert result['anchor']['atoms']>=6
    assert len(result['rows'])==2 and all(r['matched'] for r in result['rows'])
    assert any(len(s['variants'])==2 for s in result['site_frequencies'])
    assert all(r['source']['url']=='https://example.org/source' for r in result['rows'])


def test_duplicate_card_does_not_inflate_support():
    a=doc();a['evidence_cards'].append(copy.deepcopy(a['evidence_cards'][0]))
    r=align_evidence([a])
    assert r['input_cards']==3 and r['unique_molecules']==2
    assert all(s['total_unique']==2 for s in r['site_frequencies'])


def test_alignment_order_is_reproducible():
    a=doc();b=copy.deepcopy(a);b['evidence_cards'].reverse()
    assert align_evidence([a])==align_evidence([b])


def test_insufficient_and_disconnected_inputs_report_limits():
    assert align_evidence([doc(smiles=('CCO',))])['status']=='insufficient_evidence'
    r=align_evidence([doc(smiles=('CCO.Cl','CCN'))])
    assert len(r['excluded'])==1 and r['status']=='insufficient_evidence'


def test_macrocycle_bridge_is_not_two_independent_substitutions():
    documents=[]
    for pid in ['WO2011138751A2','WO2013132376A1']:
        pkg=json.loads((DATA/(pid+'.json')).read_text(encoding='utf8'))
        d=doc(pid,pid,smiles=());d['source_snapshot']={'sha256':pkg['source_html_sha256']};attach_evidence(d);documents.append(d)
    r=align_evidence(documents)
    assert r['status']=='provisional_alignment'
    early=[x for x in r['rows'] if x['publication']=='WO2011138751A2']
    late=[x for x in r['rows'] if x['publication']=='WO2013132376A1']
    assert all(not x['bridged_labels'] for x in early)
    assert all(len(x['bridged_labels'])>=2 for x in late)
    assert r['anchor']['low_coverage']
    assert len(r['family_comparisons'])==1
    assert r['family_comparisons'][0]['differences']


def test_excessive_card_count_is_rejected():
    with pytest.raises(ValueError):align_evidence([doc(smiles=('CCO',)*37)])


def test_metadata_keeps_all_names_and_abstract():
    from phase0.sar.patents import parse_patent
    html='''<meta name="citation_patent_publication_number" content="WO:2020000001:A1"><meta name="DC.contributor" scheme="inventor" content="Jane Smith"><meta name="DC.contributor" scheme="inventor" content="John Doe"><meta name="DC.contributor" scheme="assignee" content="Pfizer Inc"><section itemprop="abstract">ALK inhibitor.</section>'''
    d=parse_patent(html,'WO2020000001A1')
    assert d['inventors']==['Jane Smith','John Doe']
    assert d['assignees']==['Pfizer Inc']
    assert d['abstract']=='ALK inhibitor.'


def test_http_analysis_requires_loaded_docs_and_exports_snapshots(tmp_path):
    from phase0.sar.serve import create_server
    import threading
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    server=create_server(0,tmp_path)
    a=doc();server.results[a['publication']]=a
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        for endpoint in ('programs','scaffolds'):
            r=json.load(urlopen(Request(url+'/api/'+endpoint,data=json.dumps({'publications':[a['publication']]}).encode())))
            assert r['input_snapshots'][0]['source']==a['source_snapshot']
        for ids in ([],['WO2020000002A1'],[a['publication']]*2):
            with pytest.raises(HTTPError) as exc:urlopen(Request(url+'/api/programs',data=json.dumps({'publications':ids}).encode()))
            assert exc.value.code==400
    finally:server.shutdown();server.server_close();thread.join()
