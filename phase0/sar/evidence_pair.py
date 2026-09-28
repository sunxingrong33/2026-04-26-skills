"""Pairwise structure alignment and conservative measurement gates for the ledger."""
import math
from collections import defaultdict

from rdkit import Chem

from phase0.ledger.access import analysis_view
from .scaffolds import align_evidence
from .units import normalise


def select_pair(ledger, a_id, b_id):
    selected = []
    for identity in (a_id, b_id):
        record = next((r for r in ledger['observations'] if r['id'] == identity), None)
        if record is None:
            raise ValueError('所选证据已不存在，请刷新台账后重新选择。')
        selected.append([r for r in ledger['observations']
                         if r['document'] == record['document'] and r['compound_id'] == record['compound_id']])
    identities = []
    for rows in selected:
        structures = {Chem.MolToSmiles(Chem.MolFromSmiles(r['smiles']), isomericSmiles=True) for r in rows}
        if len(structures) != 1:
            raise ValueError('同一来源分子记录包含冲突结构，需要先核对。')
        identities.append(next(iter(structures)))
    if identities[0] == identities[1]:
        raise ValueError('A 和 B 是同一个结构，请选择两个不同的分子。')
    return selected


def compare_rows(left, right):
    """Keep every observation; duplicated assay measurements require manual review."""
    groups = [defaultdict(list), defaultdict(list)]
    for group, rows in zip(groups, (left, right)):
        for row in rows:
            group[row['assay_id']].append(row)
    output = []
    for assay_id in sorted(groups[0].keys() | groups[1].keys()):
        aa, bb = groups[0][assay_id], groups[1][assay_id]
        reasons, ratio, normalized = [], None, None
        if not aa or not bb:
            reasons.append('缺少另一分子的同 assay 测量；不按相似实验名称配对。')
        elif len(aa) != 1 or len(bb) != 1:
            reasons.append('存在重复观测，需核对重复实验或数据冲突；未自动取平均。')
        else:
            a, b = aa[0], bb[0]
            if a['document'] != b['document'] or a['kind'] != b['kind']:
                reasons.append('跨来源协议可比性尚未核实。')
            if not assay_id or not a['protocol'] or a['protocol'] != b['protocol']:
                reasons.append('实验标识或条件缺失/不一致。')
            if not a['endpoint'] or a['endpoint'] != b['endpoint']:
                reasons.append('测量终点缺失/不一致。')
            if a.get('quality_flag') or b.get('quality_flag'):
                reasons.append('来源带有数据质量标记，需先复核。')
            if a['endpoint'].lower() in {'logd', 'logp', 'pka', 'pic50', 'pki'}:
                reasons.append('对数尺度指标不计算 B/A；当前保留原值供比较。')
            if a['value_status'] == 'missing' or b['value_status'] == 'missing':
                reasons.append('包含缺失或未测项。')
            elif a['relation'] != '=' or b['relation'] != '=':
                reasons.append('包含限定值或未知关系，不计算精确倍数。')
            else:
                try:
                    x, u = normalise(float(a['value']), a['unit'])
                    y, v = normalise(float(b['value']), b['unit'])
                    known = {'nM', '%', 'min', 'h', 's'}
                    unitless_ratio = not u and 'ratio' in a['endpoint'].lower()
                    if u != v or (u not in known and not unitless_ratio):
                        reasons.append('单位不一致、未知或缺失，无法确认量纲。')
                    elif x <= 0 or y <= 0:
                        reasons.append('非正数不计算倍数。')
                    elif not math.isfinite(y / x):
                        reasons.append('比值超出有限数值范围。')
                    else:
                        normalized = {'a': x, 'b': y, 'unit': u or 'dimensionless'}
                        if not reasons:
                            ratio = y / x
                except (ValueError, TypeError, OverflowError):
                    reasons.append('数值无效或非有限值。')
        output.append({'assay_id': assay_id, 'a': aa, 'b': bb,
                       'status': 'provisional_comparable' if ratio is not None else 'blocked',
                       'ratio_b_over_a': ratio, 'normalized': normalized,
                       'reasons': reasons or ['同来源、同 assay、同条件和终点的精确值；仅暂定数值可比，原文及独立复核状态保留。']})
    return output


def analyse_pair(request):
    if not isinstance(request, dict) or request.get('mode') not in ('align', 'compare'):
        raise ValueError('请选择结构对齐或可比性检查。')
    if not all(isinstance(request.get(k), str) for k in ('a', 'b')):
        raise ValueError('请选择 A、B 两个分子。')
    ledger = analysis_view()
    left, right = select_pair(ledger, request['a'], request['b'])
    out = {'mode': request['mode'], 'inputs': ledger['inputs'],
           'a': left[0], 'b': right[0],
           'notice': '仅分析所选分子对。B/A 是数值比值，不自动解释为改善倍数或单一基团的因果效果；所有记录仍待独立复核。'}
    if request['mode'] == 'compare':
        out['measurements'] = compare_rows(left, right)
    else:
        docs = []
        for slot, rows in zip(('A', 'B'), (left, right)):
            r = rows[0]
            docs.append({'publication': slot, 'evidence_cards': [{
                'example': 1, 'label': slot + ' · ' + r['compound_label'], 'smiles': r['smiles'],
                'structure_source': r['structure_source'], 'review_status': r['review_status']}]})
        aligned = align_evidence(docs)
        aligned['notice'] = '共同锚点及 R 标签来自本次算法对齐；保留立体结构输入，但不完整描述立体变化。存在对称歧义、多位点或桥连时，不能直接归因到一个基团。'
        changes = []
        if len(aligned['rows']) == 2 and all(r['matched'] for r in aligned['rows']):
            a, b = aligned['rows']
            changes = [{'site': site, 'a': a['fragments'].get(site), 'b': b['fragments'].get(site),
                        'bridged': site in a['bridged_labels'] or site in b['bridged_labels']}
                       for site in aligned['labels'] if a['fragments'].get(site) != b['fragments'].get(site)]
        aligned['changes'] = changes
        out['alignment'] = aligned
    return out
