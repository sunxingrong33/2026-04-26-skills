"""Lossless observation ledger for the committed evidence packages (no network)."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / 'data'


def _csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def build_ledger(data_dir=DATA):
    data_dir = Path(data_dir)
    rows, inputs = [], []

    def track(path):
        inputs.append({'path': path.relative_to(data_dir).as_posix(),
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

    for folder in sorted((data_dir / 'curated').iterdir()):
        if not folder.is_dir():
            continue
        paths = [folder / name for name in
                 ('sources.json', 'compounds.csv', 'program_members.csv', 'observations.csv')]
        for path in paths:
            track(path)
        source = json.loads(paths[0].read_text(encoding='utf-8'))
        compounds = {r['compound_id']: r for r in _csv(paths[1])}
        members = {r['compound_id']: r for r in _csv(paths[2])}
        for obs in _csv(paths[3]):
            cid = obs['molecule_chembl_id']
            compound, member = compounds[cid], members[cid]
            rows.append({
                'id': f"{folder.name}:activity:{obs['activity_id']}",
                'kind': 'paper', 'document': source['document_chembl_id'],
                'document_url': 'https://doi.org/' + source['doi'],
                'compound_id': cid, 'compound_label': compound['compound_name'],
                'smiles': compound['smiles'],
                'structure_source': {'url': compound['structure_source'], 'locator': None},
                'compound_mapping': {'url': member['source_url'], 'locator': member['example_ref']},
                'measurement_source': {'url': obs['source_url'], 'locator': None},
                'assay_id': obs['assay_chembl_id'], 'endpoint': obs['standard_type'],
                'protocol': obs['assay_description'], 'protocol_locator': None,
                'relation': obs['standard_relation'], 'value': obs['standard_value'],
                'unit': obs['standard_units'], 'raw': dict(obs),
                'missing_reason': None, 'quality_flag': obs['data_validity_comment'],
                'review_status': 'database_import_pending_original_review',
                'gaps': ['original_structure_locator', 'original_table_locator',
                         'full_protocol_review', 'independent_review'],
            })

    for path in sorted((data_dir / 'patent_evidence').glob('*.json')):
        track(path)
        package = json.loads(path.read_text(encoding='utf-8'))
        assays = {a['id']: a for a in package['assays']}
        for card in package['cards']:
            for index, obs in enumerate(card['measurements'] + card.get('missing_measurements', [])):
                assay = assays[obs['assay_id']]
                rows.append({
                    'id': f"{package['publication']}:example:{card['example']}:{index}",
                    'kind': 'patent', 'document': package['publication'],
                    'document_url': package['pdf_url'],
                    'compound_id': f"{package['publication']}:example:{card['example']}",
                    'compound_label': card['label'], 'smiles': card['smiles'],
                    'structure_source': card['structure_source'],
                    'compound_mapping': {'locator': card['label'], 'url': card['structure_source']['url']},
                    'measurement_source': card['table_source'],
                    'assay_id': assay['id'], 'endpoint': assay['label'],
                    'protocol': assay['protocol'], 'protocol_locator': assay['protocol_locator'],
                    'relation': obs.get('relation'), 'value': obs.get('value'),
                    'unit': obs.get('unit', assay['unit']), 'raw': dict(obs),
                    'missing_reason': obs.get('reason'), 'quality_flag': None,
                    'review_status': package['review']['status'],
                    'source_html_sha256': package['source_html_sha256'],
                    'source_pdf_sha256': package['source_pdf_sha256'],
                    'stereochemistry_note': card.get('stereochemistry_note'),
                    'gaps': ['independent_review'],
                })
    ids = [r['id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate observation identity')
    for row in rows:
        row['value_status'] = ('missing' if row['value'] in (None, '') else
                               'exact' if row['relation'] == '=' else 'qualified_or_unknown')
    return {'schema_version': 1,
            'scope': 'committed_curated_packages_only',
            'notice': '仅汇总本地已整理数据；原始论文定位和独立复核仍有缺口。未联网刷新来源，不代表检索全库；此台账不判定测量可比性。',
            'summary': {'documents': len({r['document'] for r in rows}),
                        'structure_records': len({r['compound_id'] for r in rows}),
                        'observations': len(rows),
                        'value_status': dict(Counter(r['value_status'] for r in rows)),
                        'gaps': dict(Counter(gap for r in rows for gap in r['gaps']))},
            'inputs': inputs, 'observations': rows}
