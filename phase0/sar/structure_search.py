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
from collections import Counter, defaultdict
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
    measured = defaultdict(int)
    for o in ledger.observations:
        if o.status == 'measured' and o.review.record_status != 'rejected':
            measured[o.compound_id] += 1
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
                         'measured_observations': measured[c.id],
                         'structure_source': c.structure_source.model_dump(exclude_none=True), **detail})
    rows.sort(key=lambda r: (-r.get('similarity', 1), r['document_id'], r['label']))
    return {'total': len(rows), 'truncated': len(rows) > LOCAL_LIMIT, 'rows': rows[:LOCAL_LIMIT],
            'scope': f'本地证据台账 {len(ledger.compounds)} 个结构',
            'by_document': dict(sorted(Counter(r['document_id'] for r in rows).items())),
            'with_measurements': sum(1 for r in rows if r['measured_observations']),
            'structure_only': sum(1 for r in rows if not r['measured_observations'])}


def coverage(result):
    """What this structure search covered and what it did not: sources, counts, truncation, known gaps."""
    q = result.get('search') or {}
    local = result.get('ledger_matches') or {}
    sources = [{'source': '本地证据台账', 'status': 'ok', 'scope': local.get('scope'), 'total': local.get('total'),
                'returned': len(local.get('rows', [])), 'truncated': bool(local.get('truncated')),
                'note': f"其中有测量 {local.get('with_measurements', 0)} 个、只有结构 {local.get('structure_only', 0)} 个"}]
    chembl = result.get('external_status', 'not_requested')
    sources.append({'source': 'ChEMBL', 'status': chembl,
                    'total': result.get('total') if chembl == 'ok' else None,
                    'returned': len(result.get('molecules') or []) if chembl == 'ok' else 0,
                    'truncated': bool(result.get('truncated') or result.get('has_more')) if chembl == 'ok' else False,
                    'note': '精确检索按标准 InChIKey；相似性 / 子结构用 ChEMBL 自身算法' if chembl == 'ok' else ''})
    sc = result.get('surechembl') or {'status': 'not_requested'}
    sources.append({'source': 'SureChEMBL', 'status': sc['status'], 'total': sc.get('total'),
                    'returned': len(sc.get('rows', [])), 'truncated': bool(sc.get('truncated')),
                    'note': ('达到服务端 10,000 上限，实际命中可能更多；' if sc.get('capped') else '')
                            + (f"{sc['below_threshold']} 个低于阈值未显示" if sc.get('below_threshold') else '')})
    sources.append({'source': 'PubChem', 'status': 'per_compound', 'total': None, 'returned': None, 'truncated': False,
                    'note': '不做批量结构检索；可对检索结构或任一命中单独查询关联专利与文献'})
    gaps = []
    names = {'not_requested': '未查询', 'failed': '查询失败'}
    for src in sources:
        if src['status'] in names:
            gaps.append(f"{src['source']}：{names[src['status']]}，结果不包含该来源")
        if src['truncated']:
            gaps.append(f"{src['source']}：结果被截断，只显示前 {src['returned']} 个")
    if local.get('structure_only'):
        gaps.append(f"本地命中中 {local['structure_only']} 个只有结构、没有测量（如专利结构索引或 SureChEMBL 提取结构）")
    if sc['status'] == 'ok' and sc.get('rows'):
        gaps.append('SureChEMBL 命中只有结构，没有测量；专利中的活性数据需要 PDF 抽取（迭代 I3）后才有')
    gaps.append('未检索商业数据库（如 Reaxys、SciFinder、GOSTAR、智慧芽）；零命中不代表不存在')
    return {'query': {k: q.get(k) for k in ('method', 'threshold', 'standardize', 'searched_smiles')},
            'sources': sources, 'local_by_document': local.get('by_document', {}), 'gaps': gaps}


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
        row['local_check'] = recheck(q, query_mol, target, 'ChEMBL')
        rows.append(row)
    meta = payload.get('page_meta') or {}
    total = meta.get('total_count')
    return {'total': total, 'truncated': bool(meta.get('next')) or (isinstance(total, int) and total > len(rows)),
            'rows': rows, 'source': source}


def recheck(q, query_mol, target, source):
    """Reproduce a remote hit with RDKit; a hit that cannot be reproduced is flagged, never dropped."""
    if target is None:
        return {'status': 'no_structure', 'note': f'{source} 未返回可解析结构，无法本地复核'}
    hit, detail = match(query_mol, target, q['method'], q['threshold'] or DEFAULT_THRESHOLD)
    note = ('本地 RDKit 复核一致' if hit else
            '本地指纹相似度低于阈值（两边指纹算法不同）' if q['method'] == 'similarity' else
            '本地未复现该精确匹配（可能是立体或互变异构差异），请核对' if q['method'] == 'exact' else
            '本地未复现该子结构匹配，请核对')
    return {'status': 'agrees' if hit else 'disagrees', **detail, 'note': note}


def surechembl_search(q, query_mol, cache, limit=20):
    """SureChEMBL structure search (patent chemistry), each hit re-checked locally.

    SureChEMBL applies its own similarity cut-off and returns hits in server order, so
    similarity hits below the requested threshold are dropped and the rest sorted by score."""
    from . import surechembl
    payload, source = surechembl.search(q['searched_smiles'], q['method'], cache, limit)
    rows, below = [], 0
    for r in payload['records']:
        row = surechembl.compound_row(r)
        score = row['surechembl_similarity']
        if q['method'] == 'similarity' and score is not None and score * 100 < q['threshold']:
            below += 1
            continue
        row['local_check'] = recheck(q, query_mol, prepared(row['smiles'], q['method'], q['standardize']),
                                     'SureChEMBL')
        rows.append(row)
    if q['method'] == 'similarity':
        rows.sort(key=lambda r: -(r['surechembl_similarity'] or 0))
    total = payload['total']
    return {'total': total, 'truncated': total > len(payload['records']), 'capped': total >= surechembl.CAP,
            'below_threshold': below, 'rows': rows, 'source': source,
            'notice': surechembl.NOTICE, 'attribution': surechembl.ATTRIBUTION}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
