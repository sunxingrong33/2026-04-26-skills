"""Structure search: exact, similarity and substructure, locally and in ChEMBL.

Similarity only ranks and filters hits. It is not evidence that two compounds
act alike, come from the same programme, or that a document claims a structure.
Every result records the query as searched (input, standardisation steps,
method, threshold) and the source snapshot, so a search can be replayed.

Local search covers the evidence ledger (``phase0.ledger.access``); ChEMBL
search uses its similarity / substructure endpoints through the cached
``discovery.fetch``. ChEMBL hits are re-checked locally with RDKit: a hit whose
substructure match or similarity cannot be reproduced is flagged, not dropped.
"""
from functools import lru_cache
from urllib.parse import quote

from rdkit import Chem, DataStructs, rdBase
from rdkit.Chem import rdFingerprintGenerator
from rdkit.Chem.MolStandardize import rdMolStandardize

METHODS = ('exact', 'similarity', 'substructure')
DEFAULT_THRESHOLD = 70          # percent Tanimoto; ChEMBL accepts 40-100
MIN_THRESHOLD = 40
MIN_SUBSTRUCTURE_ATOMS = 6      # smaller queries match too much to be useful
LOCAL_LIMIT = 50
FINGERPRINT = 'Morgan 半径 2、2048 位（RDKit）'
_morgan = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)

NOTICE = {
    'exact': '按标准 InChIKey 精确匹配；标准化会合并盐型与互变异构形式（见下方标准化记录）。',
    'similarity': ('相似度只用于排序和筛选，不说明活性相近、属于同一研发程序或被某专利覆盖。'
                   '骨架变化大（如大环化）时同一系列的相似度也可能很低；找跨系列关联请改用共有片段的子结构检索。'),
    'substructure': '子结构命中只说明含有该片段；数据库中的“分子—文档”关联不证明是实施例或被权利要求覆盖。',
}
ZERO_HITS = '未命中不代表不存在：检索范围仅限所列来源，且受阈值、标准化方式和返回上限影响。'


def _params():
    p = Chem.SmilesParserParams()
    p.parseName = False
    p.allowCXSMILES = False
    return p


def parse(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError('请输入不超过 2000 字符的 SMILES。')
    _ = rdBase.BlockLogs()
    mol = Chem.MolFromSmiles(text.strip(), _params())
    if mol is None or mol.GetNumAtoms() == 0 or mol.GetNumAtoms() > 200:
        raise ValueError('SMILES 无效或超过 200 个原子的限制。')
    return mol


def _smiles(mol):
    return Chem.MolToSmiles(mol, isomericSmiles=True)


def _stereo_count(mol):
    return len(Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)) + \
        sum(b.GetStereo() != Chem.BondStereo.STEREONONE for b in mol.GetBonds())


def standardize(mol, method, enabled=True):
    """Return (mol, steps). Substructure queries are never tautomer-canonicalised."""
    steps = []
    if not enabled:
        return mol, [{'step': 'disabled', 'note': '按输入结构原样检索（未标准化）'}]
    _ = rdBase.BlockLogs()
    before = _smiles(mol)
    out = rdMolStandardize.Cleanup(mol)
    if len(Chem.GetMolFrags(out)) > 1:
        out = rdMolStandardize.FragmentParent(out)
        steps.append({'step': 'largest_fragment', 'note': '去除盐和溶剂等小片段，保留最大有机片段'})
    charged = _smiles(out)
    out = rdMolStandardize.Uncharger().uncharge(out)
    if _smiles(out) != charged:
        steps.append({'step': 'neutralized', 'note': '中和可中和的电荷'})
    if method != 'substructure':
        stereo = _stereo_count(out)
        tautomer = rdMolStandardize.TautomerEnumerator().Canonicalize(out)
        if _smiles(tautomer) != _smiles(out):
            steps.append({'step': 'tautomer', 'note': '统一为 RDKit 规范互变异构形式'})
            if _stereo_count(tautomer) != stereo:
                steps.append({'step': 'stereo_changed', 'note': '互变异构标准化改变了立体信息，请核对'})
        out = tautomer
    if _smiles(out) == before and not steps:
        steps.append({'step': 'unchanged', 'note': '标准化未改变结构'})
    return out, steps


def similarity(a, b):
    return DataStructs.TanimotoSimilarity(_morgan.GetFingerprint(a), _morgan.GetFingerprint(b))


def query(request):
    """Validate and prepare a structure search request; raises ValueError."""
    method = request.get('method') or 'exact'
    if method not in METHODS:
        raise ValueError('检索方式必须是 exact、similarity 或 substructure。')
    threshold = request.get('threshold', DEFAULT_THRESHOLD)
    if method == 'similarity' and (type(threshold) is not int or not MIN_THRESHOLD <= threshold <= 100):
        raise ValueError(f'相似度阈值须为 {MIN_THRESHOLD}–100 的整数（百分比）。')
    enabled = request.get('standardize', True)
    if enabled not in (True, False):
        raise ValueError('standardize 必须是 true 或 false。')
    original = parse(request.get('query'))
    mol, steps = standardize(original, method, enabled)
    if method == 'substructure' and mol.GetNumHeavyAtoms() < MIN_SUBSTRUCTURE_ATOMS:
        raise ValueError(f'子结构查询至少需要 {MIN_SUBSTRUCTURE_ATOMS} 个重原子，否则命中过多；请细化片段。')
    return {'method': method, 'threshold': threshold if method == 'similarity' else None,
            'standardize': enabled, 'input': request.get('query').strip(),
            'original_smiles': _smiles(original), 'searched_smiles': _smiles(mol),
            'steps': steps, 'fingerprint': FINGERPRINT if method == 'similarity' else None}, mol


@lru_cache(maxsize=4096)  # ledger structures are re-standardised on every search otherwise
def prepared(smiles, method, enabled):
    if not isinstance(smiles, str) or not smiles.strip():
        return None
    _ = rdBase.BlockLogs()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    # Targets always get the full standardisation, so salts or tautomers do not hide a hit.
    return standardize(mol, 'exact' if method != 'substructure' else 'substructure', enabled)[0]


def match(query_mol, target, method, threshold):
    """(hit, detail) for one prepared target molecule, computed locally with RDKit."""
    if method == 'exact':
        return Chem.MolToInchiKey(query_mol) == Chem.MolToInchiKey(target), {}
    if method == 'similarity':
        s = similarity(query_mol, target)
        return s * 100 >= threshold, {'similarity': round(s, 3)}
    atoms = target.GetSubstructMatch(query_mol, useChirality=False)
    return bool(atoms), {'match_atoms': list(atoms)}


def local_search(q, query_mol, ledger=None):
    """Search the evidence ledger (rejected compounds excluded)."""
    from phase0.ledger.access import current_ledger
    ledger = ledger if ledger is not None else current_ledger()
    rows = []
    for c in ledger.compounds:
        if c.review.record_status == 'rejected':
            continue
        target = prepared(c.smiles, q['method'], q['standardize'])
        if target is None:
            continue
        hit, detail = match(query_mol, target, q['method'], q['threshold'])
        if hit:
            rows.append({'compound_id': c.id, 'document_id': c.document_id, 'label': c.label,
                         'smiles': c.smiles, 'role': c.role, 'record_status': c.review.record_status,
                         'structure_source': c.structure_source.model_dump(exclude_none=True), **detail})
    rows.sort(key=lambda r: (-r.get('similarity', 1), r['document_id'], r['label']))
    return {'total': len(rows), 'truncated': len(rows) > LOCAL_LIMIT, 'rows': rows[:LOCAL_LIMIT],
            'scope': f'本地证据台账 {len(ledger.compounds)} 个结构'}


def chembl_search(q, query_mol, cache, fetch, limit=20):
    """ChEMBL similarity / substructure page, each hit re-checked locally."""
    smiles = quote(q['searched_smiles'], safe='')
    endpoint = (f"similarity/{smiles}/{q['threshold']}" if q['method'] == 'similarity'
                else f'substructure/{smiles}')
    payload, source = fetch(endpoint, {'limit': limit}, cache)
    records = payload.get('molecules')
    if not isinstance(records, list):
        raise ValueError('来源格式变化')
    rows = []
    for r in records[:limit]:
        structures = r.get('molecule_structures') or {}
        target = prepared(structures.get('canonical_smiles'), q['method'], q['standardize'])
        row = {'molecule_chembl_id': r.get('molecule_chembl_id'), 'pref_name': r.get('pref_name'),
               'canonical_smiles': structures.get('canonical_smiles'),
               'standard_inchi_key': structures.get('standard_inchi_key'), 'max_phase': r.get('max_phase')}
        if q['method'] == 'similarity':
            row['chembl_similarity'] = _number(r.get('similarity'))
        if target is None:
            row['local_check'] = {'status': 'no_structure', 'note': 'ChEMBL 未返回可解析结构，无法本地复核'}
        else:
            hit, detail = match(query_mol, target, q['method'], q['threshold'] or DEFAULT_THRESHOLD)
            row['local_check'] = {'status': 'agrees' if hit else 'disagrees', **detail,
                                  'note': '本地 RDKit 复核一致' if hit else
                                  ('本地指纹相似度低于阈值（两边指纹算法不同）' if q['method'] == 'similarity'
                                   else '本地未复现该子结构匹配，请核对')}
        rows.append(row)
    meta = payload.get('page_meta') or {}
    total = meta.get('total_count')
    return {'total': total, 'truncated': bool(meta.get('next')) or (isinstance(total, int) and total > len(rows)),
            'rows': rows, 'source': source}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
