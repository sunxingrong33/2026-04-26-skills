import csv
import json
import threading
from urllib.request import urlopen

from phase0.sar.evidence_ledger import DATA, build_ledger
from phase0.sar.serve import create_server


def test_all_observations_preserved_without_aggregation():
    ledger = build_ledger()
    for folder in (DATA / 'curated').iterdir():
        if not folder.is_dir():
            continue
        with (folder / 'observations.csv').open(encoding='utf-8', newline='') as stream:
            expected = list(csv.DictReader(stream))
        actual = [r['raw'] for r in ledger['observations'] if r['id'].startswith(folder.name + ':')]
        assert actual == expected
    assert ledger['summary']['observations'] == 320
    assert ledger['summary']['structure_records'] == 37
    assert ledger == build_ledger()


def test_qualifiers_missing_values_and_review_gaps_survive():
    rows = build_ledger()['observations']
    early = [r for r in rows if r['compound_id'] == 'WO2011138751A2:example:6']
    missing = [r for r in early if r['value_status'] == 'missing']
    assert len(missing) == 2
    assert all(r['value'] is None and r['missing_reason'] for r in missing)
    late = [r for r in rows if r['document'] == 'WO2013132376A1' and r['assay_id'] == 'ALK_WT_Ki']
    assert len(late) == 3
    assert all(r['relation'] == '<' and r['value_status'] == 'qualified_or_unknown' for r in late)
    assert all('independent_review' in r['gaps'] for r in rows)
    assert all(r['measurement_source']['locator'] is None for r in rows if r['kind'] == 'paper')
    assert all(r['measurement_source']['pdf_page'] for r in rows if r['kind'] == 'patent')


def test_ledger_http_and_page():
    server = create_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f'http://127.0.0.1:{server.server_port}'
        with urlopen(root + '/api/evidence') as response:
            assert json.load(response) == build_ledger()
        with urlopen(root + '/evidence') as response:
            assert '证据台账' in response.read().decode('utf-8')
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
