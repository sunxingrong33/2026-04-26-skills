"""Auditable candidate program clustering; no chronological or causal inference."""
import hashlib
import json
import re
import unicodedata
from itertools import combinations
from pathlib import Path
import yaml
from .scaffolds import evidence_molecules, scaffold_signatures
from .align import ring_systems, GENERIC_RINGS

CONFIG = Path(__file__).resolve().parents[1] / 'data' / 'program_analysis.yaml'


def normalized(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value).casefold()))


def assignee_name(value, config):
    raw = normalized(value)
    for canonical, aliases in config['assignee_aliases'].items():
        if raw in {normalized(a) for a in [canonical, *aliases]}:
            return canonical
    return raw


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else None


def target_signals(doc, config):
    signals = []
    for target, patterns in config['targets'].items():
        for field in ('title', 'abstract'):
            text = doc.get(field, '')
            found = next((re.search(p, text, re.I) for p in patterns if re.search(p, text, re.I)), None)
            if found:
                signals.append({'target': target, 'field': field,
                    'excerpt': text[max(0, found.start()-60):found.end()+100],
                    'url': doc['source_url'], 'status': 'dictionary_mention_not_target_validation'})
    return signals


def analyse_programs(documents, config=None):
    if len(documents) > 16:
        raise ValueError('最多分析 16 份已加载专利。')
    config = config or yaml.safe_load(CONFIG.read_text(encoding='utf-8-sig'))
    docs = {d['publication']: d for d in documents}
    molecules, excluded = evidence_molecules(list(docs.values()))
    groups = {}
    for pid, doc in sorted(docs.items()):
        key = 'family:' + doc['family_id'] if doc.get('family_id') else 'publication:' + pid
        node = groups.setdefault(key, {'id': key, 'family_id': doc.get('family_id'), 'publications': [],
            'priority_dates': [], 'assignees_raw': [], 'assignees': set(), 'inventors': set(),
            'targets': set(), 'target_evidence': [], 'sources': [], 'mols': {}})
        node['publications'].append(pid)
        if doc.get('priority_date'):
            node['priority_dates'].append(doc['priority_date'])
        raw = doc.get('assignees') or ([doc['assignee']] if doc.get('assignee') else [])
        node['assignees_raw'].extend(raw)
        node['assignees'].update(assignee_name(a, config) for a in raw if normalized(a))
        node['inventors'].update(normalized(i) for i in doc.get('inventors', []) if normalized(i))
        targets = target_signals(doc, config)
        node['target_evidence'].extend(targets)
        node['targets'].update(t['target'] for t in targets)
        node['sources'].append({'publication': pid, 'url': doc['source_url'], 'snapshot': doc.get('source_snapshot')})
        for m in molecules:
            if m['publication'] == pid:
                node['mols'][m['smiles']] = m['mol']
    for node in groups.values():
        node['scaffolds'] = scaffold_signatures(list(node['mols'].values()))
        node['informative_rings'] = set().union(*(ring_systems(m) for m in node['mols'].values())) - GENERIC_RINGS
        node['priority_date'] = min(node.pop('priority_dates')) if node['priority_dates'] else ''
        node['evidence_molecules'] = len(node.pop('mols'))
    pairs = []
    for a, b in combinations(groups.values(), 2):
        signals = {k: jaccard(a[k], b[k]) for k in ('inventors', 'scaffolds', 'targets')}
        network_score = signals['scaffolds']
        ring_score = jaccard(a['informative_rings'], b['informative_rings'])
        shared_rings = sorted(a['informative_rings'] & b['informative_rings'])
        scaffold_method = 'network_jaccard'
        if len(shared_rings) >= 2 and (ring_score or 0) > (network_score or 0):
            signals['scaffolds'] = ring_score
            scaffold_method = 'informative_ring_fallback'
        shared = {k: sorted(a[k] & b[k]) for k in ('assignees', 'inventors', 'scaffolds', 'targets')}
        score = sum(config['weights'][k] * (v or 0) for k, v in signals.items())
        reasons = []
        if not a['assignees'] or not b['assignees']:
            reasons.append('申请人信息缺失')
        elif not shared['assignees']:
            reasons.append('申请人未匹配；不会自动推定子公司归属')
        if not a['evidence_molecules'] or not b['evidence_molecules']:
            reasons.append('缺少可用于骨架计算的实施例证据卡')
        if sum(v is not None and v > 0 for v in signals.values()) < 2:
            reasons.append('不足两个相互支持的信号')
        if score < config['threshold']:
            reasons.append('加权分数未达到候选阈值')
        pairs.append({'a': a['id'], 'b': b['id'], 'score': round(score, 4), 'signals': signals,
            'scaffold_detail': {'method': scaffold_method, 'network_jaccard': network_score, 'ring_jaccard': ring_score, 'shared_informative_rings': shared_rings},
            'shared': shared, 'eligible': not reasons, 'reasons': reasons,
            'missing_signals': [k for k, v in signals.items() if v is None]})
    # Complete-link merges prevent a bridge patent from joining unsupported A/C pairs.
    clusters = [{k} for k in sorted(groups)]
    by_pair = {frozenset((p['a'], p['b'])): p for p in pairs}
    for pair in sorted(pairs, key=lambda p: (-p['score'], p['a'], p['b'])):
        if not pair['eligible']:
            continue
        a = next(c for c in clusters if pair['a'] in c)
        b = next(c for c in clusters if pair['b'] in c)
        if a is b:
            continue
        if all(by_pair[frozenset((x, y))]['eligible'] for x in a for y in b):
            a.update(b)
            clusters.remove(b)
    programs = []
    for c in sorted(clusters, key=lambda x: sorted(x)):
        ordered = sorted(c, key=lambda k: (groups[k]['priority_date'] or '9999', k))
        programs.append({'id': hashlib.sha256('|'.join(sorted(c)).encode()).hexdigest()[:12],
            'families': ordered, 'publications': [p for k in ordered for p in groups[k]['publications']],
            'status': 'candidate_program' if len(c) > 1 else 'unassigned_family'})
    serial = []
    for n in groups.values():
        serial.append({k: sorted(v) if isinstance(v, set) else v for k, v in n.items()})
    return {'programs': programs, 'families': serial, 'pairs': pairs, 'excluded': excluded,
        'config': config, 'config_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        'notice': '候选分组不是已证实研发程序。分数是启发式加权和，不是概率；缺失信号不重新归一化。日期仅用于展示排序，不参与聚类。只分析已加载公开文本及其证据卡，未对全库搜索。'}
