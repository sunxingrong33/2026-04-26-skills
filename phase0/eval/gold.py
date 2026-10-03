"""Gold-standard documents for extraction evaluation.

One JSON document per publication. Predictions from any extractor use the same
format, optionally adding ``confidence`` (high / medium / low) and ``flags`` to
each compound or observation. Missing measurements keep their meaning: a blank
cell, an explicit "not tested" and "not reported" are different statuses and a
number never stands in for them.

    python -m phase0.eval.gold            # regenerate gold v0 from evidence packages
    python -m phase0.eval.gold --check    # fail if committed gold drifted
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

from phase0.sar.patent_evidence import DATA as EVIDENCE

SCHEMA_VERSION = 1
GOLD = Path(__file__).resolve().parents[1] / 'data' / 'gold'
ROOT = Path(__file__).resolve().parents[2]

STATUSES = {'measured', 'not_tested', 'blank', 'not_reported', 'not_applicable'}
RELATIONS = {'=', '<', '<=', '>', '>=', '~', 'grade'}
ROLES = {'example', 'intermediate', 'reference', 'reagent'}
CONFIDENCES = {'high', 'medium', 'low'}


def validate(doc):
    """Reject malformed documents instead of scoring them silently."""
    problems = []
    if doc.get('schema_version') != SCHEMA_VERSION:
        problems.append(f"schema_version 必须为 {SCHEMA_VERSION}")
    if not doc.get('publication'):
        problems.append('缺少 publication')
    seen = set()
    for c in doc.get('compounds', []):
        key = str(c.get('example', ''))
        if not key:
            problems.append('化合物缺少 example')
        if key in seen:
            problems.append(f'化合物 example 重复：{key}')
        seen.add(key)
        if c.get('role') not in ROLES:
            problems.append(f'Example {key} 的 role 无效：{c.get("role")}')
        _check_confidence(c, f'Example {key}', problems)
    seen = set()
    for o in doc.get('observations', []):
        key = (str(o.get('example', '')), o.get('assay_id'))
        label = f'Example {key[0]} / {key[1]}'
        if not key[0] or not key[1]:
            problems.append('观测缺少 example 或 assay_id')
        if key in seen:
            problems.append(f'观测重复：{label}')
        seen.add(key)
        status = o.get('status')
        if status not in STATUSES:
            problems.append(f'{label} 的 status 无效：{status}')
        elif status == 'measured':
            relation = o.get('relation')
            if relation not in RELATIONS:
                problems.append(f'{label} 的 relation 无效：{relation}')
            elif relation == 'grade':
                if not o.get('grade'):
                    problems.append(f'{label} 为分级值但缺少 grade')
                if o.get('value') is not None:
                    problems.append(f'{label} 为分级值，不能同时给出连续数值')
            else:
                value = o.get('value')
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                    problems.append(f'{label} 缺少有限数值')
                if not o.get('unit'):
                    problems.append(f'{label} 缺少单位')
        elif o.get('value') is not None or o.get('grade') is not None:
            problems.append(f'{label} 状态为 {status}，不能带数值或等级')
        _check_confidence(o, label, problems)
    if problems:
        raise ValueError('；'.join(problems))
    return doc


def _check_confidence(item, label, problems):
    if 'confidence' in item and item['confidence'] not in CONFIDENCES:
        problems.append(f'{label} 的 confidence 无效：{item["confidence"]}')
    if 'flags' in item and not isinstance(item['flags'], list):
        problems.append(f'{label} 的 flags 必须是列表')


def load(path):
    return validate(json.loads(Path(path).read_text(encoding='utf8')))


def from_evidence(path):
    """Gold v0 from a curated evidence package; inherits its review status."""
    raw = Path(path).read_bytes()
    package = json.loads(raw)
    compounds, observations = [], []
    for card in package['cards']:
        example = str(card['example'])
        compounds.append({
            'example': example,
            'role': 'example',
            'smiles': card['smiles'],
            'structure_pdf_page': card['structure_source']['pdf_page'],
            'reported_mass': card.get('reported_lcms_m_plus_h'),
        })
        page = card['table_source']['pdf_page']
        for m in card['measurements']:
            observations.append({
                'example': example, 'assay_id': m['assay_id'], 'status': 'measured',
                'relation': m['relation'], 'value': m['value'], 'unit': m['unit'],
                'grade': None, 'raw': m.get('raw'), 'table_pdf_page': page,
            })
        for m in card.get('missing_measurements', []):
            observations.append({
                'example': example, 'assay_id': m['assay_id'], 'status': 'not_tested',
                'relation': None, 'value': None, 'unit': None, 'grade': None,
                'note': m.get('reason'), 'table_pdf_page': page,
            })
    return validate({
        'schema_version': SCHEMA_VERSION,
        'publication': package['publication'],
        'source_pdf_sha256': package.get('source_pdf_sha256'),
        'gold_status': package['review']['status'],
        'derived_from': {
            'path': Path(path).resolve().relative_to(ROOT).as_posix(),
            'sha256': hashlib.sha256(raw).hexdigest(),
        },
        'assays': [{'id': a['id'], 'unit': a['unit'], 'protocol_locator': a.get('protocol_locator')}
                   for a in package['assays']],
        'compounds': compounds,
        'observations': observations,
    })


def dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=1) + '\n'


def derived_documents():
    return {p.stem: from_evidence(p) for p in sorted(EVIDENCE.glob('WO*.json'))}


def main(argv=None):
    ap = argparse.ArgumentParser(description='生成或核对由证据包派生的金标准 v0')
    ap.add_argument('--check', action='store_true', help='只核对，不写文件')
    args = ap.parse_args(argv)
    stale = []
    for name, doc in derived_documents().items():
        path = GOLD / f'{name}.json'
        if args.check:
            if not path.exists() or path.read_text(encoding='utf8') != dump(doc):
                stale.append(path.name)
        else:
            path.write_text(dump(doc), encoding='utf8')
            print(f'写入 {path.relative_to(ROOT)}')
    if stale:
        print('金标准与证据包不一致，请运行 python -m phase0.eval.gold：' + '、'.join(stale))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
