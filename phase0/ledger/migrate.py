"""Build the typed ledger from committed evidence and prove nothing was lost.

Losslessness is checked by round trip: typed ledger -> legacy rows must equal
``evidence_ledger.build_ledger()`` exactly (compared by canonical SHA-256), and
every typed value must agree with the raw source record it came from.

    python -m phase0.ledger.migrate                 # summary + lossless check
    python -m phase0.ledger.migrate --out ledger.json
"""
import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

from phase0.sar.evidence_ledger import DATA, NOTICE, SCOPE, assemble, build_ledger
from phase0.sar.lineage import load_relations
from .schema import (Assay, Compound, CompoundRole, Document, InputFile, Ledger, Location,
                     Observation, ObservationStatus, RecordStatus, Review)

PAPER_COMPOUND_GAPS = ['original_structure_locator', 'compound_role', 'independent_review']
PATENT_COMPOUND_GAPS = ['independent_review']


def _location(d):
    return Location(**d)


def _same(seen, key, value, what):
    if key in seen and seen[key] != value:
        raise ValueError(f'{what} {key} 在不同观测中不一致，拒绝迁移')
    seen[key] = value


def _float(text):
    return None if text in (None, '') else float(text)


def build(data_dir=DATA):
    data_dir = Path(data_dir)
    legacy = build_ledger(data_dir)
    documents, compounds, assays, observations = {}, {}, {}, []
    compound_seen, assay_seen = {}, {}

    for folder in sorted((data_dir / 'curated').iterdir()):
        if folder.is_dir():
            src = json.loads((folder / 'sources.json').read_text(encoding='utf-8'))
            documents[src['document_chembl_id']] = Document(
                id=src['document_chembl_id'], kind='paper', url='https://doi.org/' + src['doi'],
                identifiers={k: src[k] for k in ('document_chembl_id', 'doi', 'pubmed_id', 'year') if k in src},
                source_sha256={},
                review=Review(record_status=RecordStatus.proposed,
                              provenance_status='database_import_pending_original_review',
                              gaps=['original_locators', 'independent_review']))
    packages = {}
    for path in sorted((data_dir / 'patent_evidence').glob('*.json')):
        package = json.loads(path.read_text(encoding='utf-8'))
        packages[package['publication']] = package
        documents[package['publication']] = Document(
            id=package['publication'], kind='patent', url=package['pdf_url'],
            identifiers={'publication': package['publication']},
            source_sha256={'html': package['source_html_sha256'], 'pdf': package['source_pdf_sha256']},
            review=Review(record_status=RecordStatus.proposed,
                          provenance_status=package['review']['status'],
                          reviewer=package['review'].get('independent_reviewer'),
                          gaps=['independent_review']))

    for row in legacy['observations']:
        doc, patent = row['document'], row['kind'] == 'patent'
        if doc not in documents:
            raise ValueError(f'观测 {row["id"]} 的文档 {doc} 没有来源元数据')
        cid = row['compound_id']
        card = None
        if patent:
            example = cid.rsplit(':', 1)[1]
            card = next(c for c in packages[doc]['cards'] if str(c['example']) == example)
        compound = dict(
            id=cid, document_id=doc, label=row['compound_label'], smiles=row['smiles'],
            role=CompoundRole.example if patent else CompoundRole.unspecified,
            structure_source=row['structure_source'], mapping_source=row['compound_mapping'],
            stereochemistry_note=row.get('stereochemistry_note'),
            reported_mass=card.get('reported_lcms_m_plus_h') if card else None)
        _same(compound_seen, cid, compound, '化合物')
        if cid not in compounds:
            compounds[cid] = Compound(
                **{**compound, 'structure_source': _location(compound['structure_source']),
                   'mapping_source': _location(compound['mapping_source'])},
                review=Review(record_status=RecordStatus.proposed, provenance_status=row['review_status'],
                              gaps=list(PATENT_COMPOUND_GAPS if patent else PAPER_COMPOUND_GAPS)))

        aid = f'{doc}:{row["assay_id"]}'
        assay_unit = (next(a for a in packages[doc]['assays'] if a['id'] == row['assay_id'])['unit']
                      if patent else (row['unit'] or None))
        assay = dict(id=aid, document_id=doc, source_assay_id=row['assay_id'], endpoint=row['endpoint'],
                     protocol=row['protocol'], protocol_locator=row['protocol_locator'], unit=assay_unit)
        _same(assay_seen, aid, assay, 'assay')
        assays.setdefault(aid, Assay(**assay))

        raw = row['raw']
        if patent:
            missing = 'relation' not in raw
            relation, value, unit = raw.get('relation'), raw.get('value'), raw.get('unit')
            # Patent packages list blanks under missing_measurements, defined in the
            # package README as "blank cell, stated as not tested".
            status = ObservationStatus.not_tested if missing else ObservationStatus.measured
        else:
            missing = raw['standard_value'] == ''
            relation = raw['standard_relation'] or None
            value, unit = _float(raw['standard_value']), raw['standard_units'] or None
            # ChEMBL holds the record without a value; the paper's table is not yet checked.
            status = ObservationStatus.not_reported if missing else ObservationStatus.measured
        observations.append(Observation(
            id=row['id'], document_id=doc, compound_id=cid, assay_id=aid, status=status,
            relation=None if missing else relation, value=None if missing else value,
            unit=None if missing else unit,
            missing_reason=row['missing_reason'], quality_flag=row['quality_flag'] or None,
            source=_location(row['measurement_source']), raw=raw,
            review=Review(record_status=RecordStatus.proposed, provenance_status=row['review_status'],
                          gaps=list(row['gaps']))))

    return Ledger(scope=SCOPE, notice=NOTICE, inputs=[InputFile(**i) for i in legacy['inputs']],
                  documents=list(documents.values()), compounds=list(compounds.values()),
                  assays=list(assays.values()), observations=observations,
                  relations=load_relations(data_dir / 'relations') if (data_dir / 'relations').exists() else [])


def to_legacy(ledger):
    """Reconstruct ``build_ledger()`` output from the typed ledger alone."""
    docs = {d.id: d for d in ledger.documents}
    compounds = {c.id: c for c in ledger.compounds}
    assays = {a.id: a for a in ledger.assays}
    loc = lambda l: l.model_dump(exclude_unset=True)
    rows = []
    for o in ledger.observations:
        d, c, a, raw = docs[o.document_id], compounds[o.compound_id], assays[o.assay_id], o.raw
        patent = d.kind == 'patent'
        row = {
            'id': o.id, 'kind': d.kind, 'document': d.id, 'document_url': d.url,
            'compound_id': c.id, 'compound_label': c.label, 'smiles': c.smiles,
            'structure_source': loc(c.structure_source), 'compound_mapping': loc(c.mapping_source),
            'measurement_source': loc(o.source),
            'assay_id': a.source_assay_id, 'endpoint': a.endpoint,
            'protocol': a.protocol, 'protocol_locator': a.protocol_locator,
            'relation': raw.get('relation') if patent else raw['standard_relation'],
            'value': raw.get('value') if patent else raw['standard_value'],
            'unit': raw.get('unit', a.unit) if patent else raw['standard_units'],
            'raw': raw, 'missing_reason': o.missing_reason,
            'quality_flag': o.quality_flag if patent else raw['data_validity_comment'],
            'review_status': o.review.provenance_status,
        }
        if patent:
            row.update(source_html_sha256=d.source_sha256['html'], source_pdf_sha256=d.source_sha256['pdf'],
                       stereochemistry_note=c.stereochemistry_note)
        row['gaps'] = list(o.review.gaps)
        rows.append(row)
    return assemble(rows, [i.model_dump() for i in ledger.inputs])


def value_mismatches(ledger):
    """Typed fields that disagree with the raw record they were read from."""
    out = []
    for o in ledger.observations:
        raw = o.raw
        if 'standard_value' in raw:
            expect = (_float(raw['standard_value']), raw['standard_relation'] or None, raw['standard_units'] or None)
        else:
            expect = (raw.get('value'), raw.get('relation'), raw.get('unit'))
        got = (o.value, o.relation, o.unit)
        same_value = (got[0] is None and expect[0] is None) or (
            got[0] is not None and expect[0] is not None and math.isclose(got[0], expect[0], rel_tol=0, abs_tol=0))
        if not same_value or got[1:] != expect[1:]:
            out.append({'id': o.id, 'typed': got, 'raw': expect})
    return out


def canonical_sha256(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def dump(ledger):
    return ledger.model_dump_json(exclude_unset=True, indent=1)


def load(text):
    return Ledger.model_validate_json(text)


def summary(ledger):
    return {
        'documents': dict(Counter(d.kind for d in ledger.documents)),
        'compounds': len(ledger.compounds),
        'compound_roles': dict(Counter(c.role for c in ledger.compounds)),
        'assays': len(ledger.assays),
        'observations': len(ledger.observations),
        'observation_status': dict(Counter(o.status for o in ledger.observations)),
        'relations': dict(Counter(o.relation for o in ledger.observations if o.relation)),
        'record_status': dict(Counter(o.review.record_status for o in ledger.observations)),
        'document_relations': dict(Counter(r.type for r in ledger.relations)),
    }


def verify(data_dir=DATA):
    ledger = build(data_dir)
    reloaded = load(dump(ledger))
    before = canonical_sha256(build_ledger(data_dir))
    after = canonical_sha256(to_legacy(reloaded))
    return {'lossless': before == after and not value_mismatches(reloaded),
            'legacy_sha256': before, 'roundtrip_sha256': after,
            'value_mismatches': value_mismatches(reloaded), 'summary': summary(reloaded)}, reloaded


def main(argv=None):
    ap = argparse.ArgumentParser(description='构建类型化证据台账并核对无损迁移')
    ap.add_argument('--data', default=str(DATA))
    ap.add_argument('--out', help='写出类型化台账 JSON')
    args = ap.parse_args(argv)
    result, ledger = verify(args.data)
    print(json.dumps({k: v for k, v in result.items() if k != 'value_mismatches'}, ensure_ascii=False, indent=1))
    if result['value_mismatches']:
        print(f"类型化数值与原始记录不一致：{len(result['value_mismatches'])} 条")
    if args.out:
        Path(args.out).write_text(dump(ledger), encoding='utf-8')
    return 0 if result['lossless'] else 1


if __name__ == '__main__':
    sys.exit(main())
