"""Bounded ChEMBL discovery; database links are not patent/example evidence."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from rdkit import Chem
from .features import compute_features
from .report import molecule_svg
from .patent_evidence import DATA

API = 'https://www.ebi.ac.uk/chembl/api/data/'
LIMIT = 20


def fetch(endpoint, params, cache):
    url = API + endpoint + '.json?' + urlencode(params)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    hit = path.exists()
    if hit:
        raw = path.read_bytes()
    else:
        with urlopen(Request(url, headers={'User-Agent': 'SAR-Atlas/0.3'}), timeout=30) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError('来源响应超过大小限制')
        json.loads(raw)
        path.write_bytes(raw)
    return json.loads(raw), {'url': url, 'sha256': hashlib.sha256(raw).hexdigest(),
        'retrieved_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), 'cache_hit': hit}


def structure(smiles):
    if not isinstance(smiles, str) or not smiles.strip() or len(smiles) > 2000:
        raise ValueError('请输入不超过 2000 字符的 SMILES。')
    params = Chem.SmilesParserParams()
    params.parseName = False
    params.allowCXSMILES = False
    mol = Chem.MolFromSmiles(smiles.strip(), params)
    if mol is None or mol.GetNumAtoms() == 0 or mol.GetNumAtoms() > 200:
        raise ValueError('SMILES 无效或超过 200 个原子的限制。')
    canonical = Chem.MolToSmiles(mol, isomericSmiles=True)
    f = compute_features(canonical)
    return {'smiles': canonical, 'inchikey': f.inchikey, 'formula': f.formula,
            'features': f.numeric, 'svg': molecule_svg(canonical)}


def local_matches(canonical):
    rows = []
    for path in sorted(DATA.glob('*.json')):
        package = json.loads(path.read_text(encoding='utf8'))
        for card in package['cards']:
            if Chem.MolToSmiles(Chem.MolFromSmiles(card['smiles']), isomericSmiles=True) == canonical:
                rows.append({'publication': package['publication'], 'example': card['example'],
                    'label': card['label'], 'structure_source': card['structure_source'],
                    'review_status': package['review']['status']})
    return rows


def chembl_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'CHEMBL\d+', value):
        raise ValueError('请选择有效的 ChEMBL 记录。')
    return value


def discover(request, cache):
    mode = request.get('mode')
    result = {'mode': mode, 'sources': [], 'local_matches': []}
    if mode == 'documents':
        return discover_documents(request, cache)
    if mode == 'smiles':
        result['structure'] = structure(request.get('query'))
        result['local_matches'] = local_matches(result['structure']['smiles'])
        result['notice'] = '本地按规范化异构 SMILES 匹配；ChEMBL 按标准 InChIKey 查询，标准化可能合并互变异构形式。未做相似性、子结构或去盐检索；未命中不代表不存在。'
        result['molecules'] = []
        if request.get('external', False) is not True:
            result['external_status'] = 'not_requested'
            return result
        endpoint = 'molecule'
        params = {'molecule_structures__standard_inchi_key': result['structure']['inchikey'], 'limit': LIMIT}
    elif mode == 'target':
        query = request.get('query')
        if not isinstance(query, str) or not query.strip() or len(query) > 120:
            raise ValueError('请输入不超过 120 字符的靶点名称、基因符号或 ChEMBL ID。')
        query = query.strip()
        result['query'] = query
        endpoint = 'target' if re.fullmatch(r'CHEMBL\d+', query.upper()) else 'target/search'
        params = {'target_chembl_id': query.upper(), 'limit': LIMIT} if endpoint == 'target' else {'q': query, 'limit': LIMIT}
        result['notice'] = '请按名称、物种和靶点类型选择记录；同名、复合物及其他物种不会自动合并。最多展示 20 个候选，请细化名称或使用 ChEMBL ID。'
    elif mode == 'activities':
        entity = request.get('entity')
        if entity not in ('target', 'molecule'):
            raise ValueError('查询对象必须是靶点或分子。')
        cid = chembl_id(request.get('id'))
        offset = request.get('offset', 0)
        if type(offset) is not int or offset < 0 or offset > 10000 or offset % LIMIT:
            raise ValueError('分页位置无效；最多查看前 10020 条记录。')
        endpoint = 'activity'
        params = {entity + '_chembl_id': cid, 'limit': LIMIT, 'offset': offset, 'order_by': 'activity_id'}
        result.update(entity=entity, id=cid, offset=offset)
        result['notice'] = 'ChEMBL 测量记录按 activity_id 分页，非效力排名；测量可能重复、含限定值或质量标记，不据此认定结合、药物有效性或专利归属。'
    else:
        raise ValueError('不支持的检索类型。')
    try:
        payload, source = fetch(endpoint, params, cache)
        result['sources'].append(source)
        key = {'smiles': 'molecules', 'target': 'targets', 'activities': 'activities'}[mode]
        records = payload[key]
        if not isinstance(records, list):
            raise ValueError('来源格式变化')
        result[key] = records[:LIMIT]
        meta = payload.get('page_meta', {})
        result['total'] = meta.get('total_count')
        result['has_more'] = bool(meta.get('next'))
        result['external_status'] = 'ok'
    except Exception:
        if mode != 'smiles':
            raise RuntimeError('ChEMBL 暂不可用或响应无法解析，请稍后重试；未返回样例结果。') from None
        result['external_status'] = 'failed'
        result['warning'] = 'ChEMBL 查询失败；以下仅为本地结构解析及本地证据匹配，不代表数据库无结果。'
    return result


def discover_documents(request, cache):
    """Resolve only documents linked by a freshly validated activity-page query."""
    from .patents import normalize_id
    page = discover({**request, 'mode': 'activities'}, cache)
    grouped = {}
    for activity in page['activities']:
        did = activity.get('document_chembl_id')
        if isinstance(did, str) and re.fullmatch(r'CHEMBL\d+', did):
            grouped.setdefault(did, []).append({k: activity.get(k) for k in
                ('activity_id', 'molecule_chembl_id', 'target_chembl_id', 'assay_chembl_id')})
    result = {**page, 'mode': 'documents', 'documents': [],
        'notice': '仅解析当前测量页关联的文档，按文档 ID 去重。数据库文档关联不证明具体实施例、权利要求覆盖或历史演化；专利加载后才核实公开号与家族。'}
    if not grouped:
        return result
    try:
        payload, source = fetch('document', {'document_chembl_id__in': ','.join(grouped), 'limit': LIMIT}, cache)
        records = payload['documents']
        if not isinstance(records, list):
            raise ValueError('Invalid document response')
        by_id = {r['document_chembl_id']: r for r in records if isinstance(r, dict) and r.get('document_chembl_id') in grouped}
        result['sources'].append(source)
    except Exception:
        by_id = {}
        result['warning'] = '文档来源查询失败；保留测量关联，文档标为待核实，不当作零命中。'
    for did, chain in grouped.items():
        record = by_id.get(did)
        item = {'id': did, 'activities': chain, 'document': record,
                'publication': None, 'kind': 'unresolved', 'status': '文档未返回，待核实'}
        if record:
            kind = str(record.get('doc_type') or '').upper()
            item['kind'] = 'patent' if kind == 'PATENT' else 'paper' if kind == 'PUBLICATION' else 'other'
            item['status'] = '未提供可加载的专利公开号'
            patent = record.get('patent_id')
            if item['kind'] == 'patent' and isinstance(patent, str):
                try:
                    item['publication'] = normalize_id(patent)
                    item['status'] = '数据库专利公开号；待原始专利页面核验'
                except ValueError:
                    item['status'] = '专利编号缺少有效公开号格式；不猜测 A1/B2 后缀'
        result['documents'].append(item)
    result.pop('activities', None)
    return result
