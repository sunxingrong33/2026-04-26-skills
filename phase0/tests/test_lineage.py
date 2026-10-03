"""Cross-family evidence must not silently become a causal optimization claim."""
import copy
import json
import pytest
from pathlib import Path
from pydantic import ValidationError
from phase0.ledger.schema import DocumentRelation, Ledger
from phase0.sar.lineage import build_lineage, load_relations

RELATION = load_relations()[0]
EARLY, LATE = RELATION.from_publication, RELATION.to_publication
_basis = {(c.publication, c.check): c.expected for c in RELATION.basis}
EXPECTED = {p: (_basis[(p, 'family_id')], _basis[(p, 'priority_date')]) for p in (EARLY, LATE)}
from phase0.sar.patent_evidence import DATA, attach_evidence, compare_measurements


def document(pid):
    package = json.loads((DATA / (pid + '.json')).read_text(encoding='utf8'))
    family, date = EXPECTED[pid]
    d = {'publication': pid, 'family_id': family, 'priority_date': date,
         'source_snapshot': {'sha256': package['source_html_sha256']},
         'source_url': f'https://patents.google.com/patent/{pid}/en',
         'references': [EARLY] if pid == LATE else []}
    attach_evidence(d)
    return d


def test_early_mapping_retains_identity_and_missing_cell_measurements():
    cards = document(EARLY)['evidence_cards']
    assert [c['example'] for c in cards] == [1, 6, 7]
    assert [c['formula'] for c in cards] == ['C19H20FN5O2', 'C19H21FN4O2', 'C20H23FN4O2']
    assert [c['inchikey'] for c in cards] == ['VTMNUFHYUQWAFX-UHFFFAOYSA-N',
        'USZHUCMTOVFZNH-UHFFFAOYSA-N', 'HFVJJRYHCHARRC-UHFFFAOYSA-N']
    assert len(cards[1]['measurements']) == len(cards[1]['missing_measurements']) == 2
    assert all(c['table_source']['pdf_page'] == 186 for c in cards)
    assert cards[2]['measurements'][0]['raw'] == '0.536 nM'
    assert cards[2]['additional_structure_sources'][0]['url'].endswith('#page=127')


def test_cross_patent_measurements_never_compute_ratios_even_if_protocols_match():
    a, b = document(EARLY)['evidence_cards'][2], document(LATE)['evidence_cards'][2]
    for x, y in zip(a['measurements'], b['measurements']):
        y['assay'] = copy.deepcopy(x['assay'])
        y['relation'] = '='
    rows = compare_measurements(a, b)
    assert len(rows) == 4 and all(r['ratio'] is None for r in rows)
    assert all('跨专利' in r['note'] for r in rows)
    assert rows[2]['from'] == '33.0 nM' and rows[2]['to'] == '6.41 nM'


def test_missing_cells_remain_visible_in_comparison():
    a, b = document(EARLY)['evidence_cards'][1], document(LATE)['evidence_cards'][2]
    rows = compare_measurements(a, b)
    assert len(rows) == 4
    assert rows[2]['from'] == '未报告 / 未测' and rows[2]['ratio'] is None
    assert '缺失' in rows[2]['note']


def test_families_sorted_grouped_and_related_not_proven_evolution():
    a, b = document(EARLY), document(LATE)
    other = {**b, 'publication': 'US8680111B2', 'evidence_cards': []}
    graph = build_lineage([b, other, a])
    assert len(graph['nodes']) == 2
    assert graph['nodes'][0]['family_id'] == '44278717'
    assert graph['nodes'][1]['publications'] == [LATE, 'US8680111B2']
    assert graph['edges'][0]['status'] == 'related_series_not_direct_evolution'


@pytest.mark.parametrize('field,value', [('references', []), ('family_id', 'wrong'),
    ('priority_date', '2000-01-01'), ('source_snapshot', {'sha256': 'changed'}), ('evidence_cards', [])])
def test_dates_or_citations_alone_do_not_create_relation(field, value):
    a, b = document(EARLY), document(LATE)
    b[field] = value
    assert build_lineage([a, b])['edges'] == []


def test_single_document_has_no_edge():
    assert build_lineage([document(EARLY)])['edges'] == []


def test_http_lineage_and_cross_comparison_are_scoped_to_loaded_documents(tmp_path):
    import threading
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from phase0.sar.serve import create_server
    server = create_server(0, tmp_path)
    server.results = {pid: document(pid) for pid in (EARLY, LATE)}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        graph = json.load(urlopen(url + f'/api/lineage?id={EARLY}&id={LATE}'))
        assert len(graph['nodes']) == 2 and len(graph['edges']) == 1
        assert json.load(urlopen(url + f'/api/lineage?id={EARLY}'))['edges'] == []
        with pytest.raises(HTTPError) as exc:
            urlopen(url + '/api/lineage?id=US8680111B2')
        assert exc.value.code == 400
        request = {'a': {'publication': EARLY, 'index': 2, 'kind': 'evidence'},
                   'b': {'publication': LATE, 'index': 2, 'kind': 'evidence'}}
        result = json.load(urlopen(Request(url + '/api/compare',
            data=json.dumps(request).encode(), headers={'Content-Type': 'application/json'})))
        assert len(result['measurements']) == 4
        assert all(m['ratio'] is None for m in result['measurements'])
        assert result['candidate']['status'] == 'insufficient_evidence'
        assert result['from_svg'].startswith('data:image/svg+xml;base64,')
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def relation(**patch):
    data = RELATION.model_dump()
    data.update(patch)
    return DocumentRelation.model_validate(data)


def test_withheld_relation_explains_which_condition_failed():
    a, b = document(EARLY), document(LATE)
    b['references'] = []
    graph = build_lineage([a, b])
    assert graph['edges'] == []
    assert graph['withheld'][0]['id'] == RELATION.id
    assert graph['withheld'][0]['failed'] == [f'{LATE} 的引用列表不含 {EARLY}']


def test_unloaded_end_is_neither_shown_nor_listed_as_withheld():
    graph = build_lineage([document(EARLY)])
    assert graph['edges'] == [] and graph['withheld'] == []


def test_new_relation_needs_only_a_data_record():
    sibling = relation(id='sibling-demo', type='sibling_application', status='sibling_not_same_priority',
                       label='姊妹申请示例', hypothesis=None, pair=None,
                       basis=[{'check': 'has_evidence_cards', 'publication': EARLY},
                              {'check': 'has_evidence_cards', 'publication': LATE}])
    graph = build_lineage([document(EARLY), document(LATE)], relations=[sibling])
    assert [(e['id'], e['type']) for e in graph['edges']] == [('sibling-demo', 'sibling_application')]
    assert graph['edges'][0]['review_status'] == 'proposed'


def test_dynamic_sources_resolve_to_the_loaded_source_page():
    edge = build_lineage([document(EARLY), document(LATE)])['edges'][0]
    assert edge['sources'][1]['url'] == f'https://patents.google.com/patent/{LATE}/en#patentCitations'


@pytest.mark.parametrize('patch, message', [
    ({'basis': []}, 'at least 1'),
    ({'to_publication': EARLY}, '同一文档'),
    ({'basis': [{'check': 'cites', 'publication': 'US8680111B2', 'expected': EARLY}]}, '两端以外'),
    ({'basis': [{'check': 'family_id', 'publication': EARLY}]}, 'expected 设置不正确'),
    ({'basis': [{'check': 'has_evidence_cards', 'publication': EARLY, 'expected': 'x'}]}, 'expected 设置不正确'),
    ({'basis': [{'check': 'filing_date_same_day', 'publication': EARLY}]}, 'check'),
    ({'sources': [{'label': 'x', 'url': 'https://example.org', 'publication': EARLY}]}, '只能给出'),
    ({'type': 'evolution'}, 'type'),
])
def test_malformed_relation_records_are_rejected(patch, message):
    with pytest.raises(ValidationError, match=message):
        relation(**patch)


def test_ledger_rejects_relation_to_unknown_document():
    from phase0.ledger.migrate import build, dump
    data = json.loads(dump(build()))
    assert [r['id'] for r in data['relations']] == [RELATION.id]
    data['relations'][0]['to_publication'] = 'WO9999999999A1'
    data['relations'][0]['basis'] = [{'check': 'has_evidence_cards', 'publication': EARLY}]
    data['relations'][0]['sources'] = []
    with pytest.raises(ValidationError, match='不存在的文档 WO9999999999A1'):
        Ledger.model_validate(data)


def test_lineage_code_holds_no_publication_numbers():
    import re
    from phase0.sar import lineage
    source = Path(lineage.__file__).read_text(encoding='utf8')
    assert not re.search(r'\b(WO|US|EP)\d{6,}', source)
