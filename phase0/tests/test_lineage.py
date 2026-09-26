"""Cross-family evidence must not silently become a causal optimization claim."""
import copy
import json
import pytest
from phase0.sar.lineage import EARLY, LATE, EXPECTED, build_lineage
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
