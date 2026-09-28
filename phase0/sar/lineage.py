"""Curated relations between source snapshots, never inferred from date order.

Relations live as records in ``data/relations/``. Each lists the conditions that
must hold on the loaded sources (family, priority date, evidence snapshot,
citation); a relation is shown only when every condition passes. Otherwise it is
reported under ``withheld`` with the failing conditions, so the page can say why.
"""
import json
from pathlib import Path

from phase0.ledger.schema import DocumentRelation
from .patent_evidence import DATA

RELATIONS = Path(__file__).resolve().parents[1] / 'data' / 'relations'


def load_relations(folder=RELATIONS):
    return [DocumentRelation.model_validate_json(p.read_text(encoding='utf8'))
            for p in sorted(Path(folder).glob('*.json'))]


def _evidence_sha(pid):
    path = DATA / (pid + '.json')
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf8'))['source_html_sha256']


def failed_checks(relation, docs):
    failed = []
    for c in relation.basis:
        d = docs.get(c.publication)
        if d is None:
            failed.append(f'{c.publication} 未加载')
        elif c.check == 'family_id' and d.get('family_id') != c.expected:
            failed.append(f'{c.publication} 家族应为 {c.expected}')
        elif c.check == 'priority_date' and d.get('priority_date') != c.expected:
            failed.append(f'{c.publication} 优先权日应为 {c.expected}')
        elif c.check == 'snapshot_matches_evidence' and (
                d.get('source_snapshot', {}).get('sha256') != _evidence_sha(c.publication)):
            failed.append(f'{c.publication} 来源快照与证据包不一致')
        elif c.check == 'has_evidence_cards' and not d.get('evidence_cards'):
            failed.append(f'{c.publication} 没有已映射的证据卡')
        elif c.check == 'cites' and c.expected not in d.get('references', []):
            failed.append(f'{c.publication} 的引用列表不含 {c.expected}')
    return failed


def _edge(relation, docs):
    sources = [{'label': s.label, 'url': s.url if s.url else docs[s.publication]['source_url'] + s.fragment}
               for s in relation.sources]
    return {'id': relation.id, 'type': relation.type,
            'from': relation.from_publication, 'to': relation.to_publication,
            'status': relation.status, 'label': relation.label,
            'facts': relation.facts, 'hypothesis': relation.hypothesis, 'gaps': relation.gaps,
            'sources': sources, 'pair': relation.pair,
            'review_status': relation.review.record_status}


def build_lineage(documents, relations=None):
    docs = {d['publication']: d for d in documents}
    groups = {}
    for d in docs.values():
        key = d.get('family_id') or d['publication']
        node = groups.setdefault(key, {'family_id': d.get('family_id'), 'publications': [],
                                      'priority_date': '', 'evidence_count': 0})
        node['publications'].append(d['publication'])
        date = d.get('priority_date')
        if date and (not node['priority_date'] or date < node['priority_date']):
            node['priority_date'] = date
        node['evidence_count'] += len(d.get('evidence_cards', []))
    result = {'nodes': sorted(groups.values(), key=lambda n: n['priority_date'] or '9999'),
              'edges': [], 'withheld': [],
              'notice': '按已加载公开文本的优先权日排列；同族合并。日期先后不证明分子研发顺序。'}
    for relation in load_relations() if relations is None else relations:
        if relation.from_publication not in docs or relation.to_publication not in docs:
            continue
        failed = failed_checks(relation, docs)
        if failed:
            result['withheld'].append({'id': relation.id, 'label': relation.label, 'failed': failed})
        else:
            result['edges'].append(_edge(relation, docs))
    return result
