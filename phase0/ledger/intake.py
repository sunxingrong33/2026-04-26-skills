"""Turn server-held search results into proposed ledger records.

Only data the server itself retrieved is used: a patent result already parsed
and cached by the server, or a ChEMBL activity page re-read from the server's
own cache. The browser only says *which* result; values sent by the page are
never trusted.

Everything written is ``proposed``. Records that cannot be mapped without
guessing are refused with a reason rather than filled in:

* a structure-index entry has no example mapping, so its role stays
  ``unspecified``;
* a ChEMBL document whose type is not confirmed as paper or patent is refused;
* a value without a qualifier, an unknown qualifier, or a missing structure is
  refused;
* an assay seen with a different endpoint than already recorded is refused.
"""
import math

from rdkit import Chem, rdBase

API = 'https://www.ebi.ac.uk/chembl/api/data/'
RELATIONS = {'=', '<', '<=', '>', '>=', '~'}


def _review(provenance, gaps):
    return {'record_status': 'proposed', 'provenance_status': provenance, 'gaps': list(gaps)}


def _valid_smiles(smiles):
    _ = rdBase.BlockLogs()
    return isinstance(smiles, str) and Chem.MolFromSmiles(smiles) is not None


def from_patent(result):
    """Document plus its automatic structure index; no measurements, no example mapping."""
    pid = result['publication']
    url = result['source_url']
    identifiers = {k: result.get(k) for k in ('publication', 'family_id', 'priority_date', 'title', 'assignee')
                   if result.get(k)}
    sha = (result.get('source_snapshot') or {}).get('sha256')
    items = [('documents', {
        'id': pid, 'kind': 'patent', 'url': url, 'identifiers': identifiers,
        'source_sha256': {'html': sha} if sha else {},
        'review': _review('web_retrieval_pending_review', ['structure_example_mapping', 'independent_review'])})]
    refused = []
    seen = set()
    for s in result.get('structures', []):
        key = s.get('inchikey')
        if not key or key in seen or not _valid_smiles(s.get('smiles')):
            if key not in seen:
                refused.append({'id': key or s.get('smiles'), 'reason': '结构无法解析或缺少 InChIKey'})
            continue
        seen.add(key)
        items.append(('compounds', {
            'id': f'{pid}:index:{key}', 'document_id': pid, 'label': f'索引结构 {key[:14]}',
            'smiles': s['smiles'], 'role': 'unspecified',
            'structure_source': {'url': s.get('source_url') or url, 'locator': 'Google Patents 化学实体索引'},
            'mapping_source': {'url': url, 'locator': '未映射实施例'},
            'review': _review('web_retrieval_pending_review',
                              ['compound_role', 'structure_example_mapping', 'independent_review'])}))
    return items, refused


def from_surechembl(schembl, record, publication, patent):
    """A SureChEMBL compound as an unmapped structure of one patent it was extracted from (no measurements).

    ``record`` is the compound as re-read by the server, ``patent`` the matching row of the server's own
    documents_for_structures result; nothing here comes from the page.
    """
    smiles = (record or {}).get('smiles')
    if not _valid_smiles(smiles):
        return [], [{'id': schembl, 'reason': 'SureChEMBL 未返回可解析的结构'}]
    url = f'https://patents.google.com/patent/{publication}/en'
    identifiers = {'publication': publication, 'surechembl_doc_id': patent['doc_id']}
    if patent.get('title'):
        identifiers['title'] = patent['title']
    items = [('documents', {
        'id': publication, 'kind': 'patent', 'url': url, 'identifiers': identifiers, 'source_sha256': {},
        'review': _review('database_import_pending_original_review', ['original_locators', 'independent_review'])}),
        ('compounds', {
            'id': f'{publication}:surechembl:{schembl}', 'document_id': publication,
            'label': f'{schembl}（SureChEMBL 提取）', 'smiles': smiles, 'role': 'unspecified',
            'structure_source': {'url': f'https://www.surechembl.org/chemical/{schembl.removeprefix("SCHEMBL")}',
                                 'locator': 'SureChEMBL 自动化学标注提取的结构'},
            'mapping_source': {'url': patent['url'], 'locator': 'SureChEMBL 文档关联，未映射实施例'},
            'review': _review('database_import_pending_original_review',
                              ['compound_role', 'structure_example_mapping', 'extraction_verification',
                               'independent_review'])})]
    return items, []


def _document(did, record):
    kind = str((record or {}).get('doc_type') or '').upper()
    kind = {'PUBLICATION': 'paper', 'PATENT': 'patent'}.get(kind)
    if kind is None:
        return None
    doi = record.get('doi')
    identifiers = {k: record.get(k) for k in ('document_chembl_id', 'doi', 'pubmed_id', 'year', 'patent_id')
                   if record.get(k) is not None}
    return {'id': did, 'kind': kind, 'url': f'https://doi.org/{doi}' if doi else f'{API}document/{did}.json',
            'identifiers': identifiers, 'source_sha256': {},
            'review': _review('database_import_pending_original_review', ['original_locators', 'independent_review'])}


def from_activities(activities, documents, ledger):
    """ChEMBL activity records -> documents, compounds, assays, observations (all proposed).

    ``documents`` maps ChEMBL document id -> document record (with ``doc_type``);
    ``ledger`` is the current typed ledger, used to reuse and cross-check records.
    """
    known_activity = {str(o.raw.get('activity_id')) for o in ledger.observations if o.raw.get('activity_id')}
    compounds = {c.id: c for c in ledger.compounds}
    assays = {a.id: a.endpoint for a in ledger.assays}
    items, refused, present, planned_assays = [], [], [], {}
    emitted = set()

    def emit(kind, record):
        if (kind, record['id']) not in emitted:
            emitted.add((kind, record['id']))
            items.append((kind, record))

    for a in activities:
        aid = str(a.get('activity_id'))
        did, mol, assay = a.get('document_chembl_id'), a.get('molecule_chembl_id'), a.get('assay_chembl_id')
        if aid in known_activity:
            present.append(aid)
            continue
        if not (did and mol and assay):
            refused.append({'id': aid, 'reason': '缺少文档、分子或 assay 编号'})
            continue
        doc = _document(did, documents.get(did))
        if doc is None:
            refused.append({'id': aid, 'reason': '文档类型未核实为论文或专利，不推断'})
            continue
        if not _valid_smiles(a.get('canonical_smiles')):
            refused.append({'id': aid, 'reason': '缺少可解析的结构'})
            continue
        relation, value = a.get('standard_relation'), a.get('standard_value')
        if value not in (None, ''):
            try:
                value = float(value)
            except (TypeError, ValueError):
                refused.append({'id': aid, 'reason': '数值无法解析'})
                continue
            if not math.isfinite(value):
                refused.append({'id': aid, 'reason': '数值不是有限数'})
                continue
            if relation not in RELATIONS:
                refused.append({'id': aid, 'reason': f'限定符缺失或未知（{relation or "未提供"}），不推断为等号'})
                continue
            status = 'measured'
        else:
            relation, value, status = None, None, 'not_reported'
        assay_id = f'{did}:{assay}'
        endpoint = a.get('standard_type') or '未注明'
        if assays.get(assay_id, planned_assays.get(assay_id, endpoint)) != endpoint:
            refused.append({'id': aid, 'reason': '同一 assay 已记录为另一终点，需人工整理'})
            continue
        cid = mol if compounds.get(mol) and compounds[mol].document_id == did else f'{did}:{mol}'
        emit('documents', doc)
        emit('compounds', {
            'id': cid, 'document_id': did, 'label': a.get('molecule_pref_name') or mol,
            'smiles': a['canonical_smiles'], 'role': 'unspecified',
            'structure_source': {'url': f'{API}molecule/{mol}.json', 'locator': 'ChEMBL molecule record'},
            'mapping_source': {'url': f'{API}activity/{aid}.json', 'locator': f'ChEMBL record {a.get("record_id")}'},
            'review': _review('database_import_pending_original_review',
                              ['original_structure_locator', 'compound_role', 'independent_review'])})
        if assay_id not in assays and assay_id not in planned_assays:
            planned_assays[assay_id] = endpoint
            emit('assays', {
                'id': assay_id, 'document_id': did, 'source_assay_id': assay, 'endpoint': endpoint,
                'protocol': a.get('assay_description'), 'unit': a.get('standard_units'),
                'review': _review('database_import_pending_original_review',
                                  ['full_protocol_review', 'independent_review'])})
        emit('observations', {
            'id': f'chembl:activity:{aid}', 'document_id': did, 'compound_id': cid, 'assay_id': assay_id,
            'status': status, 'relation': relation, 'value': value,
            'unit': a.get('standard_units') if status == 'measured' else None,
            'quality_flag': a.get('data_validity_comment') or None,
            'source': {'url': f'{API}activity/{aid}.json', 'locator': f'ChEMBL activity {aid}'},
            'raw': a,
            'review': _review('database_import_pending_original_review',
                              ['original_structure_locator', 'original_table_locator',
                               'full_protocol_review', 'independent_review'])})
    return items, refused, present
