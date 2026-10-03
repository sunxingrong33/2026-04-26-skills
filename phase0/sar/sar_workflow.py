"""Evidence-bound SAR summaries, review records and local candidate directions."""
import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

from rdkit import Chem
from .evidence_pair import analyse_pair

REVIEWS = Path(__file__).resolve().parents[2] / 'artifacts' / 'sar-reviews'


def canonical(smiles):
    if not isinstance(smiles, str) or not 0 < len(smiles) <= 2000:
        raise ValueError('请输入有效 SMILES（最多 2000 字符）。')
    mol = Chem.MolFromSmiles(smiles)
    if mol is None or mol.GetNumHeavyAtoms() > 200:
        raise ValueError('结构无效或超过 200 个重原子。')
    return Chem.MolToSmiles(mol, isomericSmiles=True)


def build_summary(pairs):
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 8:
        raise ValueError('每批请选择 1–8 个分子对。')
    groups, excluded, seen, audits = {}, [], set(), []
    snapshots = None
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError('分子对格式无效。')
        request = {**pair, 'mode': 'align'}
        aligned = analyse_pair(request)
        measured = analyse_pair({**pair, 'mode': 'compare'})
        if aligned['inputs'] != measured['inputs'] or (snapshots is not None and snapshots != aligned['inputs']):
            raise ValueError('分析期间证据已变化，请重新运行。')
        snapshots = aligned['inputs']
        a, b = aligned['a'], aligned['b']
        # Reversed or duplicate pairs must not inflate evidence counts.
        identity = tuple(sorted((a['document']+'|'+canonical(a['smiles']), b['document']+'|'+canonical(b['smiles']))))
        if identity in seen:
            continue
        seen.add(identity)
        d = aligned['alignment']
        blocks = []
        if d['status'] != 'provisional_alignment':
            blocks.append('结构对齐未完整完成')
        if len(d['changes']) != 1 or any(c['bridged'] for c in d['changes']):
            blocks.append('不是可独立解释的单个 R 标签变化')
        if d.get('anchor', {}).get('low_coverage', True):
            blocks.append('共同锚点覆盖不足')
        warnings = sorted(set(a['gaps'] + b['gaps']))
        if any(n > 1 for n in d.get('anchor', {}).get('alternative_matches', [])):
            warnings.append('alignment_symmetry')
        audits.append({'a': a['id'], 'b': b['id'], 'blocking': blocks, 'warnings': warnings})
        for m in measured['measurements']:
            if blocks or m['ratio_b_over_a'] is None:
                excluded.append({'a': a['id'], 'b': b['id'], 'assay': m['assay_id'],
                                 'reasons': blocks + (m['reasons'] if m['ratio_b_over_a'] is None else [])})
                continue
            change = d['changes'][0]
            # Key includes labelled core and assay context. Never merge by R label alone.
            context = {'core': d['anchor']['labelled_core'], 'site': change['site'],
                       'from': change['a'], 'to': change['b'], 'document': a['document'],
                       'assay': m['assay_id'], 'endpoint': m['a'][0]['endpoint']}
            key = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
            g = groups.setdefault(key, {**context, 'id': key, 'evidence': []})
            ratio = m['ratio_b_over_a']
            g['evidence'].append({'a': a, 'b': b, 'measurements': m,
                                  'ratio': ratio, 'direction': 'unchanged' if math.isclose(ratio, 1, rel_tol=1e-9)
                                  else 'lower' if ratio < 1 else 'higher', 'warnings': warnings})
    for g in groups.values():
        g['counts'] = {k: sum(e['direction'] == k for e in g['evidence']) for k in ('lower','higher','unchanged')}
        g['source_documents'] = sorted({e['a']['document'] for e in g['evidence']})
        g['independent_replication_confirmed'] = False
    report = {'version': 1, 'scope': 'selected_pairs_only', 'pair_count': len(seen),
              'groups': list(groups.values()), 'excluded': excluded, 'audit': audits, 'inputs': snapshots,
              'notice': '仅汇总加入本批的分子对。升高/降低为数值方向，未考虑实验误差，不代表显著性或改善；单篇来源不证明独立重复。不同核心、位点、替换方向或 assay 不合并。'}
    report['report_id'] = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return report


def review_history(report_id, directory=REVIEWS):
    path = Path(directory) / report_id
    return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(path.glob('review-*.json'))] if path.exists() else []


def save_review(report, request, directory=REVIEWS):
    if request.get('report_id') != report['report_id']:
        raise ValueError('证据或分子对已变化，请重新汇总后复核。')
    decision = request.get('decision')
    reviewer, reason = request.get('reviewer'), request.get('reason')
    if decision not in ('accept', 'reject', 'needs_evidence') or not all(isinstance(v,str) and v.strip() and len(v)<=2000 for v in (reviewer, reason)):
        raise ValueError('请填写复核人、结论及理由（每项最多 2000 字符）。')
    row = {'report_id': report['report_id'], 'decision': decision, 'reviewer': reviewer.strip(),
           'reason': reason.strip(), 'recorded_at': datetime.now(timezone.utc).isoformat(),
           'identity_verified': False, 'scope': 'user_recorded_review_not_independent_certification'}
    path = Path(directory) / report['report_id']
    path.mkdir(parents=True, exist_ok=True)
    snapshot = path / 'report.json'
    if not snapshot.exists():
        snapshot.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    # Append immutable records; new evidence produces another report id.
    (path / ('review-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')+'-'+uuid.uuid4().hex+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
    return row


def suggest(report, smiles, assay, goal, reviews):
    query = canonical(smiles)
    if goal not in ('lower','higher') or not isinstance(assay,str) or not assay:
        raise ValueError('请选择具体 assay 和数值优化方向。')
    candidates, counterexamples = [], []
    rejected = bool(reviews and reviews[-1]['decision'] in ('reject','needs_evidence'))
    for group in report['groups']:
        if group['assay'] != assay:
            continue
        for e in group['evidence']:
            if canonical(e['a']['smiles']) != query:
                continue
            item = {'group_id': group['id'], 'from': group['from'], 'to': group['to'],
                    'candidate_smiles': e['b']['smiles'], 'candidate_label': e['b']['compound_label'],
                    'ratio_b_over_a': e['ratio'], 'evidence': e,
                    'status': 'hypothesis_pending_validation',
                    'validation_plan': ['核查 A/B 原始结构、测量表及实验协议', '在相同实验中重复测定 A/B 并报告误差',
                                        '另行测量选择性及所需 ADMET 指标，不从当前活性推断']}
            (candidates if e['direction'] == goal else counterexamples).append(item)
    return {'query_smiles': query, 'assay': assay, 'goal': goal,
            'status': 'review_blocked' if rejected else 'candidates' if candidates else 'insufficient_evidence',
            'candidates': [] if rejected else candidates, 'counterexamples': counterexamples,
            'notice': '候选仅限本批证据中与输入结构精确匹配的 A→B 替换，B 是已记录分子，不是新设计分子。无匹配或证据不足时不外推。人工记录不能消除原始数据缺口，也不证明实验效果。'}


def run_workflow(request):
    if not isinstance(request, dict) or request.get('mode') not in ('summary','review','suggest'):
        raise ValueError('流程请求无效。')
    report = build_summary(request.get('pairs'))
    if request['mode'] == 'review':
        save_review(report, request)
    reviews = review_history(report['report_id'])
    result = {'report': report, 'reviews': reviews}
    if request['mode'] == 'suggest':
        result['suggestions'] = suggest(report, request.get('smiles'), request.get('assay'), request.get('goal'), reviews)
    return result
