"""Deterministic scaffold network and provisional R-group alignment of evidence cards."""
from collections import Counter, defaultdict
from rdkit import Chem, rdBase
from rdkit.Chem import rdFMCS, rdRGroupDecomposition as rg
from rdkit.Chem.Scaffolds import MurckoScaffold, rdScaffoldNetwork as sn
from .report import molecule_svg

MAX_CARDS = 36


def evidence_molecules(documents):
    rows = []
    excluded = []
    for d in sorted(documents, key=lambda x: x['publication']):
        for c in sorted(d.get('evidence_cards', []), key=lambda x: (x.get('example', 0), x['smiles'])):
            mol = Chem.MolFromSmiles(c['smiles'])
            if mol is None or mol.GetNumHeavyAtoms() > 100 or len(Chem.GetMolFrags(mol)) != 1:
                excluded.append({'publication': d['publication'], 'label': c.get('label'), 'reason': '无效、多组分或超过 100 重原子'})
                continue
            sm = Chem.MolToSmiles(mol, isomericSmiles=True)
            rows.append({'publication': d['publication'], 'family': d.get('family_id') or d['publication'],
                'priority_date': d.get('priority_date') or '', 'label': c['label'], 'example': c['example'],
                'smiles': sm, 'source': c['structure_source'], 'review_status': c.get('review_status'), 'mol': mol})
    if len(rows) > MAX_CARDS:
        raise ValueError(f'单次最多分析 {MAX_CARDS} 张证据卡，请减少所选专利。')
    return rows, excluded


def network_for(mols):
    if not mols:
        return {'nodes': [], 'edges': []}
    params = sn.ScaffoldNetworkParams()
    params.includeGenericScaffolds = False
    params.includeGenericBondScaffolds = False
    params.includeScaffoldsWithAttachments = False
    params.includeScaffoldsWithoutAttachments = True
    params.collectMolCounts = True
    cores = []
    for mol in mols:
        core = MurckoScaffold.GetScaffoldForMol(mol)
        if core.GetNumAtoms():
            cores.append(core)
    if not cores:
        return {'nodes': [], 'edges': []}
    net = sn.CreateScaffoldNetwork(cores, params)
    nodes = []
    for i, sm in enumerate(net.nodes):
        m = Chem.MolFromSmiles(sm)
        if m is None:
            continue
        support = [j for j, mol in enumerate(mols) if mol.HasSubstructMatch(m)]
        nodes.append({'id': i, 'smiles': sm, 'atoms': m.GetNumHeavyAtoms(), 'support': support})
    return {'nodes': nodes, 'edges': [{'from': e.beginIdx, 'to': e.endIdx, 'type': str(e.type)} for e in net.edges]}


def scaffold_signatures(mols):
    # Single common phenyl rings should not alone establish a medicinal-chemistry series.
    return {n['smiles'] for n in network_for(mols)['nodes'] if n['atoms'] >= 8}


def align_evidence(documents):
    rows, excluded = evidence_molecules(documents)
    # Duplicate structures retain all source cards but enter support counts once.
    unique = {}
    for row in rows:
        unique.setdefault(row['smiles'], row['mol'])
    smiles = sorted(unique)
    mols = [unique[s] for s in smiles]
    out = {'rdkit_version': rdBase.rdkitVersion, 'excluded': excluded, 'input_cards': len(rows),
        'unique_molecules': len(mols), 'status': 'insufficient_evidence', 'rows': [], 'site_frequencies': [],
        'notice': '仅分析来源匹配的实施例证据卡，不使用可能含试剂的实体索引。R 标签是本次对齐的算法编号，不是专利原文编号；顺序及编号不证明历史代际。锚点使用严格元素/键型匹配，不完整表达立体变化或杂原子替换。'}
    if len(mols) < 2:
        out['reason'] = '至少需要两个不同的证据卡结构。'
        return out
    network = network_for(mols)
    out['network'] = network
    common = sorted((n for n in network['nodes'] if len(n['support']) == len(mols)), key=lambda n: (-n['atoms'], n['smiles']))
    anchor = Chem.MolFromSmiles(common[0]['smiles']) if common else None
    method = 'scaffold_network'
    mcs = rdFMCS.FindMCS(mols, atomCompare=rdFMCS.AtomCompare.CompareElements,
        bondCompare=rdFMCS.BondCompare.CompareOrderExact, ringMatchesRingOnly=True,
        completeRingsOnly=False, matchValences=True, timeout=3)
    out['mcs'] = {'smarts': mcs.smartsString, 'atoms': mcs.numAtoms, 'timed_out': bool(mcs.canceled)}
    if not mcs.canceled and mcs.numAtoms > (anchor.GetNumHeavyAtoms() if anchor else 0):
        candidate = Chem.MolFromSmarts(mcs.smartsString)
        if candidate is not None and all(m.HasSubstructMatch(candidate) for m in mols):
            anchor, method = candidate, 'strict_element_bond_MCS'
    if anchor is None or anchor.GetNumHeavyAtoms() < 6:
        out['reason'] = '没有至少 6 个重原子的共同锚点；不强制分配 R 位点。'
        return out
    coverages = [round(anchor.GetNumHeavyAtoms() / m.GetNumHeavyAtoms(), 3) for m in mols]
    out['anchor'] = {'method': method, 'smarts': Chem.MolToSmarts(anchor), 'atoms': anchor.GetNumHeavyAtoms(),
        'coverage_min': min(coverages), 'coverage_max': max(coverages),
        'low_coverage': min(coverages) < .6,
        'alternative_matches': [len(m.GetSubstructMatches(anchor, uniquify=False, maxMatches=16)) for m in mols]}
    params = rg.RGroupDecompositionParameters()
    params.timeout = 3
    params.matchingStrategy = rg.RGroupMatching.GreedyChunks
    params.removeAllHydrogenRGroups = False
    params.removeAllHydrogenRGroupsAndLabels = False
    params.substructMatchParams.useChirality = True
    try:
        decomposed, unmatched = rg.RGroupDecompose([anchor], mols, asSmiles=True, asRows=True, options=params)
    except Exception:
        out['reason'] = 'R 基团分解失败或达到时间限制；保留骨架结果供人工查看。'
        return out
    unmatched = set(unmatched)
    mapped = {}
    cursor = 0
    for i, sm in enumerate(smiles):
        if i in unmatched:
            continue
        if cursor >= len(decomposed):
            break
        mapped[sm] = decomposed[cursor]
        cursor += 1
    labels = sorted({k for r in decomposed for k in r if k.startswith('R')}, key=lambda x: int(x[1:]))
    out['labels'] = labels
    for row in rows:
        parts = mapped.get(row['smiles'])
        entry = {k: v for k, v in row.items() if k != 'mol'}
        entry['matched'] = parts is not None
        entry['fragments'] = {label: (parts or {}).get(label) for label in labels}
        entry['core'] = (parts or {}).get('Core')
        entry['fragment_svgs'] = {}
        entry['bridged_labels'] = []
        for label, sm in entry['fragments'].items():
            frag = Chem.MolFromSmiles(sm) if sm else None
            if frag is not None:
                entry['fragment_svgs'][label] = molecule_svg(sm)
            if frag and sum(a.GetAtomicNum() == 0 for a in frag.GetAtoms()) > 1:
                entry['bridged_labels'].append(label)
        entry['svg'] = molecule_svg(row['smiles'])
        out['rows'].append(entry)
    if decomposed:
        out['anchor']['labelled_core'] = decomposed[0].get('Core')
        try:
            out['anchor']['svg'] = molecule_svg(decomposed[0]['Core'])
        except Exception:
            out['anchor']['svg'] = None
    families = defaultdict(dict)
    for row in out['rows']:
        families[row['family']].setdefault(row['smiles'], row)
    for family, compounds in sorted(families.items()):
        for label in labels:
            matched = [r for r in compounds.values() if r['matched']]
            count = Counter(r['fragments'][label] for r in matched if r['fragments'][label] is not None)
            out['site_frequencies'].append({'family': family, 'label': label, 'total_unique': len(compounds),
                'matched_unique': len(matched), 'missing': sum(r['fragments'][label] is None for r in matched),
                'variants': [{'smiles': k, 'count': v} for k, v in sorted(count.items())]})
    dates = {family: min((r['priority_date'] for r in entries.values() if r['priority_date']), default='') for family, entries in families.items()}
    family_order = sorted(families, key=lambda f: (dates[f] or '9999', f))
    frequencies = {(f['family'], f['label']): f for f in out['site_frequencies']}
    out['family_comparisons'] = []
    for a, b in zip(family_order, family_order[1:]):
        differences = []
        for label in labels:
            left = {v['smiles'] for v in frequencies[a, label]['variants']}
            right = {v['smiles'] for v in frequencies[b, label]['variants']}
            if left != right:
                differences.append({'label': label, 'only_in_a': sorted(left-right), 'only_in_b': sorted(right-left),
                    'shared': sorted(left & right), 'a_matched': frequencies[a, label]['matched_unique'],
                    'b_matched': frequencies[b, label]['matched_unique']})
        out['family_comparisons'].append({'a': a, 'b': b, 'date_a': dates[a], 'date_b': dates[b],
            'differences': differences, 'notice': '仅比较当前样本的出现情况；未出现不等于停止探索，日期顺序不证明实际优化步骤。'})
    out['status'] = 'provisional_alignment' if len(mapped) == len(mols) else 'partial_alignment'
    out['reason'] = '存在对称匹配或部分环锚点的可能；多连接点片段可能表示成环，不能当作独立单点替换。低覆盖率仅提示对齐范围有限，不自动认定骨架跃迁。'
    return out
