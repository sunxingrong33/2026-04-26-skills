"""Studies (调研): a named scope over the evidence ledger, used by the redesigned workbench.

A study is a list of ledger documents plus a few labels (target, goal template) and the
chemist's own signed notes. It never stores evidence of its own: every number shown on a
study page is read from the ledger at request time, so a study cannot drift from the ledger.

Studies live in one JSON file next to the patent cache (``artifacts/studies.json``). When
the file does not exist the built-in example study over the committed data is offered;
nothing is written until the user changes something.

Evidence tiers shown in the interface are derived from each record's provenance, never
stored: L0 automatic index (patent page index, SureChEMBL), L1 database import (ChEMBL),
L2 transcription checked against the source PDF, L3 confirmed by a named reviewer.
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

from .lineage import load_relations
from .mass_check import check_mass, summarise

DEFAULT_ID = 'alk-pfizer'
DEFAULT_STUDY = {
    'id': DEFAULT_ID, 'name': 'ALK 大环系列 · 辉瑞', 'builtin': True,
    'target': {'label': 'ALK', 'organism': '人源', 'chembl_id': 'CHEMBL4247'},
    'documents': ['WO2011138751A2', 'WO2013132376A1', 'CHEMBL3286195'],
    'goal_template': 'cell_potency_efflux',
    'notes': [],
}
PAPERS = {  # labels for the committed papers; the ledger itself only holds identifiers
    'CHEMBL3286195': {'title': '洛拉替尼发现论文', 'citation': 'Johnson et al., 2014', 'applicant': 'Pfizer'},
    'CHEMBL3351341': {'title': '奥希替尼发现论文', 'citation': 'Finlay et al., 2014', 'applicant': 'AstraZeneca'},
}
TIERS = {
    'L0': '自动索引', 'L1': '数据库', 'L2': '转录', 'L3': '具名确认',
}
STATUS_LABEL = {'proposed': '待确认', 'confirmed': '已确认', 'rejected': '被拒绝'}
NOTE_KINDS = {'hypothesis': '研究假设', 'gap': '证据缺口', 'followup': '补测建议', 'summary': '结论摘要'}
NAME_MAX, TEXT_MAX, AUTHOR_MAX = 40, 600, 40
_lock = threading.Lock()


def tier(record):
    """Evidence tier from provenance; confirmation by a named reviewer lifts any record to L3."""
    if record.review.record_status == 'confirmed':
        return 'L3'
    p = record.review.provenance_status or ''
    if p.startswith('agent_visual_checked') or p.startswith('manual_transcription'):
        return 'L2'
    if p.startswith('database_import') or p.startswith('chembl'):
        return 'L1'
    return 'L0'


# ---------------------------------------------------------------- storage

def _path(folder):
    return Path(folder) / 'studies.json'


def load_all(folder):
    path = _path(folder)
    if not path.exists():
        return [json.loads(json.dumps(DEFAULT_STUDY))]
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        raise ValueError('studies.json 格式无效')
    return data


def _save(folder, studies):
    path = _path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(studies, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)


def get(folder, study_id):
    for s in load_all(folder):
        if s['id'] == study_id:
            return s
    raise LookupError('没有这个调研。')


def _clean(text, limit, what):
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f'{what}不能为空。')
    text = ' '.join(text.split())
    if len(text) > limit:
        raise ValueError(f'{what}不超过 {limit} 个字符。')
    return text


def create(folder, name):
    name = _clean(name, NAME_MAX, '调研名称')
    with _lock:
        studies = load_all(folder)
        base = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'study'
        sid, n = base, 1
        while any(s['id'] == sid for s in studies):
            n += 1
            sid = f'{base}-{n}'
        study = {'id': sid, 'name': name, 'builtin': False, 'target': None, 'documents': [],
                 'goal_template': 'cell_potency_efflux', 'notes': [], 'created_at': _now()}
        studies.append(study)
        _save(folder, studies)
    return study


def add_documents(folder, study_id, documents, known):
    if not isinstance(documents, list) or not 1 <= len(documents) <= 20 or \
            any(not isinstance(d, str) for d in documents):
        raise ValueError('请选择 1–20 份文档。')
    missing = [d for d in documents if d not in known]
    if missing:
        raise ValueError('台账中没有这些文档，未加入调研：' + '、'.join(missing))
    with _lock:
        studies = load_all(folder)
        study = next((s for s in studies if s['id'] == study_id), None)
        if study is None:
            raise LookupError('没有这个调研。')
        added = [d for d in documents if d not in study['documents']]
        study['documents'] += added
        _save(folder, studies)
    return {'added': added, 'already': [d for d in documents if d not in added]}


def add_note(folder, study_id, request):
    kind = request.get('kind')
    if kind not in NOTE_KINDS:
        raise ValueError('判断类型必须是研究假设、证据缺口、补测建议或结论摘要。')
    note = {'kind': kind, 'text': _clean(request.get('text'), TEXT_MAX, '内容'),
            'author': _clean(request.get('author'), AUTHOR_MAX, '署名'),
            'context': _clean(request.get('context') or '未注明出处', 200, '出处'),
            'verdict': request.get('verdict') if request.get('verdict') in ('support', 'pending', 'reject') else None,
            'created_at': _now()}
    with _lock:
        studies = load_all(folder)
        study = next((s for s in studies if s['id'] == study_id), None)
        if study is None:
            raise LookupError('没有这个调研。')
        study['notes'].append(note)
        _save(folder, studies)
    return note


ANALYSIS_KEYS = ('goal', 'documents', 'max_change', 'lead', 'constraints', 'reference')


def save_analysis(folder, study_id, request):
    """Keep the analysis set-up the chemist confirmed, so overview and report reuse the same goal."""
    if not isinstance(request, dict) or not isinstance(request.get('goal'), dict):
        raise ValueError('缺少分析设置。')
    kept = {k: request[k] for k in ANALYSIS_KEYS if k in request}
    kept['mode'] = 'analyse'
    if len(json.dumps(kept, ensure_ascii=False)) > 30000:
        raise ValueError('分析设置过大。')
    with _lock:
        studies = load_all(folder)
        study = next((s for s in studies if s['id'] == study_id), None)
        if study is None:
            raise LookupError('没有这个调研。')
        study['analysis'] = {**kept, 'saved_at': _now()}
        _save(folder, studies)
    return study['analysis']


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ---------------------------------------------------------------- chemistry helpers

@lru_cache(maxsize=512)
def depict(smiles, width=240, height=160):
    """RDKit 2D depiction as SVG text; None when the SMILES does not parse."""
    _ = rdBase.BlockLogs()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    opts = drawer.drawOptions()
    opts.clearBackground = False
    opts.bondLineWidth = 1.2
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


@lru_cache(maxsize=512)
def descriptors(smiles):
    _ = rdBase.BlockLogs()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return {'mw': round(Descriptors.MolWt(mol), 1), 'clogp': round(Descriptors.MolLogP(mol), 2),
            'tpsa': round(rdMolDescriptors.CalcTPSA(mol), 1), 'rotb': Lipinski.NumRotatableBonds(mol),
            'hbd': Lipinski.NumHDonors(mol), 'hba': Lipinski.NumHAcceptors(mol),
            'heavy_atoms': mol.GetNumHeavyAtoms(), 'rings': rdMolDescriptors.CalcNumRings(mol),
            'formula': rdMolDescriptors.CalcMolFormula(mol)}


def molfile_smiles(text):
    """First record of a MOL / SDF file as isomeric SMILES; read locally, nothing is sent anywhere."""
    if not isinstance(text, str) or 'M  END' not in text or len(text) > 200000:
        raise ValueError('不是有效的 MOL / SDF 文本（需包含 M  END，且小于 200 KB）。')
    _ = rdBase.BlockLogs()
    block = text.split('$$$$')[0]
    mol = Chem.MolFromMolBlock(block)
    if mol is None or mol.GetNumAtoms() == 0:
        raise ValueError('MOL / SDF 第一条记录无法解析。')
    records = sum(1 for part in text.split('$$$$') if 'M  END' in part)
    return {'smiles': Chem.MolToSmiles(mol), 'records': records,
            'note': '已读取第一条记录' + (f'（文件共 {records} 条，其余未使用）' if records > 1 else '')}


def smiles_info(text):
    """Parse one SMILES for the search page preview; nothing leaves the machine."""
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError('请输入不超过 2000 字符的 SMILES。')
    _ = rdBase.BlockLogs()
    mol = Chem.MolFromSmiles(text.strip())
    if mol is None or mol.GetNumAtoms() == 0:
        raise ValueError('SMILES 无法解析，请检查括号、环编号与芳香性。')
    if mol.GetNumHeavyAtoms() > 200:
        raise ValueError('结构超过 200 个重原子，不做检索。')
    frags = Chem.GetMolFrags(mol, asMols=True)
    centers = Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)
    assigned = [c for c in centers if c[1] != '?']
    smiles = Chem.MolToSmiles(mol)
    d = descriptors(smiles)
    if len(centers) == 0:
        stereo = '无立体中心'
    elif len(assigned) == len(centers):
        stereo = f"{len(centers)} 个立体中心，已指定 " + '、'.join(c[1] for c in assigned)
    else:
        stereo = f"{len(centers)} 个立体中心，{len(centers) - len(assigned)} 个未指定"
    return {'smiles': text.strip(), 'canonical': smiles, 'formula': d['formula'],
            'mw': d['mw'], 'inchikey': Chem.MolToInchiKey(mol), 'heavy_atoms': mol.GetNumHeavyAtoms(),
            'stereo': stereo,
            'components': '单组分，未检测到盐或溶剂' if len(frags) == 1 else f'{len(frags)} 个组分，检索时可去盐与溶剂'}


# ---------------------------------------------------------------- views

def _doc_meta(doc, relations):
    """Family id and priority date come from the curated relation checks; nothing is guessed."""
    meta = {'id': doc.id, 'kind': doc.kind, 'url': doc.url, 'tier': tier(doc),
            'status': doc.review.record_status, 'identifiers': doc.identifiers}
    if doc.kind == 'paper':
        meta.update(PAPERS.get(doc.id, {'title': doc.id, 'citation': str(doc.identifiers.get('year') or '')}))
        meta['year'] = doc.identifiers.get('year')
        meta['date'] = str(meta['year']) if meta['year'] else None
        return meta
    meta['title'] = doc.id
    for r in relations:
        for c in r.basis:
            if c.publication == doc.id and c.check == 'family_id':
                meta['family_id'] = c.expected
            if c.publication == doc.id and c.check == 'priority_date':
                meta['priority_date'] = c.expected
            if c.publication == doc.id and c.check == 'cites':
                meta.setdefault('cites', []).append(c.expected)
    meta['date'] = meta.get('priority_date')
    return meta


def scope(ledger, study):
    docs = [d for d in ledger.documents if d.id in study['documents']]
    ids = {d.id for d in docs}
    return (docs, [c for c in ledger.compounds if c.document_id in ids],
            [a for a in ledger.assays if a.document_id in ids],
            [o for o in ledger.observations if o.document_id in ids])


def documents(ledger, all_studies):
    """Ledger documents with their snapshot hashes, so an uploaded PDF can be matched in the browser."""
    out = []
    for d in ledger.documents:
        meta = _doc_meta(d, ledger.relations)
        out.append({'id': d.id, 'kind': d.kind, 'title': meta.get('title'), 'url': d.url,
                    'pdf_sha256': d.source_sha256.get('pdf'), 'family_id': meta.get('family_id'),
                    'priority_date': meta.get('priority_date'),
                    'compounds': sum(1 for c in ledger.compounds if c.document_id == d.id),
                    'studies': [{'id': s['id'], 'name': s['name']} for s in all_studies if d.id in s['documents']]})
    return out


def summary(ledger, study):
    """Counts for the home page card and the overview header."""
    docs, comps, assays, obs = scope(ledger, study)
    live = [c for c in comps if c.review.record_status != 'rejected']
    pending = sum(1 for c in comps if c.review.record_status == 'proposed')
    return {'id': study['id'], 'name': study['name'], 'builtin': study.get('builtin', False),
            'target': study.get('target'), 'goal_template': study.get('goal_template'),
            'documents': study['documents'],
            'patents': sum(1 for d in docs if d.kind == 'patent'), 'papers': sum(1 for d in docs if d.kind == 'paper'),
            'compounds': len(live), 'observations': sum(1 for o in obs if o.review.record_status != 'rejected'),
            'not_tested': sum(1 for o in obs if o.status == 'not_tested'),
            'confirmed': sum(1 for c in comps if c.review.record_status == 'confirmed'),
            'pending': pending, 'notes': len(study.get('notes', [])), 'analysis': study.get('analysis')}


def examples(ledger):
    """The committed paper datasets, offered as read-only examples."""
    out = []
    for d in ledger.documents:
        if d.kind != 'paper':
            continue
        n_c = sum(1 for c in ledger.compounds if c.document_id == d.id)
        n_o = sum(1 for o in ledger.observations if o.document_id == d.id)
        meta = PAPERS.get(d.id, {'title': d.id, 'citation': ''})
        out.append({'id': d.id, 'title': meta['title'], 'citation': meta['citation'],
                    'compounds': n_c, 'observations': n_o, 'tier': tier(d)})
    return out


def _value(o):
    if o.status != 'measured':
        return {'status': o.status, 'text': {'not_tested': '未测', 'blank': '原表空白', 'not_reported': '未报告',
                                             'not_applicable': '不适用'}.get(o.status, o.status),
                'reason': o.missing_reason}
    if o.relation == 'grade':
        return {'status': 'grade', 'relation': 'grade', 'text': o.grade}
    raw = o.raw.get('raw') if isinstance(o.raw, dict) else None
    unit = o.unit or ''
    text = ('' if o.relation == '=' else o.relation) + f'{o.value:g}'
    if isinstance(raw, str) and unit and raw.strip().endswith(unit):
        text = raw.strip()[:-len(unit)].strip() or text  # keep the precision the source printed (2.90, not 2.9)
    return {'status': 'measured', 'relation': o.relation, 'value': o.value, 'unit': unit, 'text': text, 'raw': raw,
            'qualified': o.relation != '='}


def evidence(ledger, study):
    """Compound rows grouped by document with their measurements per assay column."""
    relations = ledger.relations
    docs, comps, assays, obs = scope(ledger, study)
    by_compound = defaultdict(list)
    for o in obs:
        by_compound[o.compound_id].append(o)
    groups = []
    order = sorted(docs, key=lambda d: (d.kind != 'patent', _doc_meta(d, relations).get('date') or '9999', d.id))
    for d in order:
        meta = _doc_meta(d, relations)
        d_assays = [a for a in assays if a.document_id == d.id]
        counts = Counter(o.assay_id for o in obs if o.document_id == d.id and o.status == 'measured')
        # Patents list every assay; papers keep their most measured assays as columns.
        columns = d_assays if d.kind == 'patent' else sorted(d_assays, key=lambda a: -counts[a.id])[:6]
        rows = []
        for c in (x for x in comps if x.document_id == d.id):
            values, shown = {}, {a.id for a in columns}
            for o in by_compound[c.id]:
                if o.assay_id in shown:
                    values.setdefault(o.assay_id, []).append({**_value(o), 'id': o.id, 'source': {
                        k: v for k, v in o.source.model_dump(exclude_none=True).items() if k in ('url', 'pdf_page')}})
            mass = None
            if c.reported_mass is not None:
                m = check_mass(c.smiles, c.reported_mass)
                mass = {'status': m.get('status'), 'summary': summarise(m)}
            rows.append({'id': c.id, 'label': c.label, 'short': c.label.split(' · ')[0], 'smiles': c.smiles,
                         'tier': tier(c), 'status': c.review.record_status,
                         'reviewer': c.review.reviewer, 'stereo': c.stereochemistry_note,
                         'structure_source': c.structure_source.model_dump(exclude_none=True),
                         'mass': mass, 'descriptors': descriptors(c.smiles), 'values': values,
                         'gaps': c.review.gaps})
        groups.append({**meta, 'assays': [{'id': a.id, 'source_id': a.source_assay_id,
                                           'label': a.endpoint if d.kind == 'patent' else f'{a.endpoint} · {a.source_assay_id}',
                                           'unit': a.unit, 'protocol': a.protocol,
                                           'protocol_locator': a.protocol_locator, 'measured': counts[a.id]}
                                          for a in columns],
                       'assay_total': len(d_assays), 'rows': rows,
                       'observations': sum(1 for o in obs if o.document_id == d.id)})
    return {'study': summary(ledger, study), 'groups': groups,
            'notice': '色阶只在同一文档、同一实验列内比较；跨文档只并列原始值，不计算倍数。限定值不参与排序；未测不填零。'}


def overview(ledger, study):
    relations = ledger.relations
    docs, comps, _, obs = scope(ledger, study)
    families = []
    for d in sorted((d for d in docs if d.kind == 'patent'), key=lambda d: _doc_meta(d, relations).get('date') or ''):
        meta = _doc_meta(d, relations)
        examples_ = [c for c in comps if c.document_id == d.id and c.role == 'example']
        meta.update(examples=[{'id': c.id, 'label': c.label, 'smiles': c.smiles} for c in examples_],
                    measurements=sum(1 for o in obs if o.document_id == d.id and o.status == 'measured'))
        families.append(meta)
    ids = {d.id for d in docs}
    rels = [{'id': r.id, 'label': r.label, 'from': r.from_publication, 'to': r.to_publication,
             'facts': r.facts, 'hypothesis': r.hypothesis, 'gaps': r.gaps, 'status': r.status,
             'sources': [s.model_dump(exclude_none=True) for s in r.sources]}
            for r in relations if r.from_publication in ids and r.to_publication in ids]
    papers = [_doc_meta(d, relations) for d in docs if d.kind == 'paper']
    pending_cards = [c for c in comps if c.review.record_status == 'proposed' and tier(c) == 'L2']
    return {'study': summary(ledger, study), 'families': families, 'papers': papers, 'relations': rels,
            'pending_cards': len(pending_cards),
            'notes': study.get('notes', [])}


# ---------------------------------------------------------------- review queue

def _queue_category(c, actors):
    actor = actors.get(c.id, '')
    if actor == 'workbench':
        return 'intake'
    if actor not in ('', 'browser-test', 'serve --ledger-db', 'init', 'migrate') and not actor.startswith('import'):
        return 'agent'
    return 'cards' if tier(c) == 'L2' else ('database' if tier(c) == 'L1' else 'intake')


QUEUE_LABEL = {'cards': '专利证据卡', 'database': '数据库导入', 'intake': '加入台账', 'pdf': 'PDF 抽取',
               'agent': 'agent 提交'}


def review_queue(ledger, store=None):
    """Pending compounds with their observations: what a named reviewer still has to confirm."""
    actors = {}
    if store is not None:
        for entry in store.audit_log():
            if entry.get('action') == 'propose' and entry.get('kind') == 'compounds':
                actors[entry['record_id']] = entry.get('actor', '')
    by_compound = defaultdict(list)
    for o in ledger.observations:
        by_compound[o.compound_id].append(o)
    assays = {a.id: a for a in ledger.assays}
    docs = {d.id: d for d in ledger.documents}
    items = []
    for c in ledger.compounds:
        if c.review.record_status != 'proposed':
            continue
        own = by_compound[c.id]
        mass = check_mass(c.smiles, c.reported_mass) if c.reported_mass is not None else None
        pages = sorted({o.source.pdf_page for o in own if o.source.pdf_page})
        items.append({
            'kind': 'compounds', 'id': c.id, 'document_id': c.document_id, 'label': c.label,
            'short': c.label.split(' · ')[0], 'category': _queue_category(c, actors),
            'tier': tier(c), 'smiles': c.smiles, 'stereo': c.stereochemistry_note,
            'structure_source': c.structure_source.model_dump(exclude_none=True),
            'mapping_source': c.mapping_source.model_dump(exclude_none=True),
            'document_url': docs[c.document_id].url if c.document_id in docs else None,
            'mass': {**mass, 'summary': summarise(mass)} if mass else None, 'gaps': c.review.gaps,
            'activity_pages': pages,
            'observations': [{'id': o.id, 'assay': assays[o.assay_id].endpoint if o.assay_id in assays else o.assay_id,
                              'assay_id': o.assay_id, **_value(o),
                              'status_record': o.review.record_status,
                              'source': o.source.model_dump(exclude_none=True)} for o in own],
        })
    counts = Counter(i['category'] for i in items)
    order = {'cards': 0, 'intake': 1, 'pdf': 2, 'agent': 3, 'database': 4}
    items.sort(key=lambda i: (order[i['category']], i['document_id'], i['label']))
    return {'items': items, 'counts': {k: counts.get(k, 0) for k in QUEUE_LABEL}, 'labels': QUEUE_LABEL,
            'notice': '确认或拒绝都要具名并写理由；审计日志只追加。拒绝的记录退出分析，但不会删除。'}


def review_compound(store, compound_id, status, reviewer, note):
    """Confirm or reject one compound together with its pending observations, after checking all exist."""
    if status not in ('confirmed', 'rejected'):
        raise ValueError('复核结论只能是确认或拒绝。')
    reviewer = _clean(reviewer, AUTHOR_MAX, '复核人')
    note = _clean(note, TEXT_MAX, '理由')
    ledger = store.load()
    comp = next((c for c in ledger.compounds if c.id == compound_id), None)
    if comp is None:
        raise LookupError('台账中没有该记录。')
    if comp.review.record_status != 'proposed':
        raise ValueError('该记录已复核过，不能重复复核。')
    own = [o for o in ledger.observations if o.compound_id == compound_id and o.review.record_status == 'proposed']
    store.review('compounds', compound_id, status, reviewer, note)
    if status == 'confirmed':  # a rejected compound already drops its observations from analysis
        for o in own:
            store.review('observations', o.id, status, reviewer, note)
    return {'compound': compound_id, 'status': status, 'observations': len(own) if status == 'confirmed' else 0}


# ---------------------------------------------------------------- comparison

def compare(ledger, a_id, b_id):
    """A/B: MCS highlight, RDKit descriptors and raw measurements; ratios only within one document."""
    from .evidence_pair import compare_rows
    from .report import structure_pair
    from phase0.ledger.access import analysis_view
    comps = {c.id: c for c in ledger.compounds}
    if a_id not in comps or b_id not in comps:
        raise LookupError('台账中没有所选分子。')
    if a_id == b_id:
        raise ValueError('A 和 B 是同一个分子，请选择两个不同的分子。')
    a, b = comps[a_id], comps[b_id]
    if Chem.MolToSmiles(Chem.MolFromSmiles(a.smiles)) == Chem.MolToSmiles(Chem.MolFromSmiles(b.smiles)):
        raise ValueError('A 和 B 是同一个结构，请选择两个不同的分子。')
    pair = structure_pair(a.smiles, b.smiles)
    legacy = analysis_view(ledger)
    rows = defaultdict(list)
    for r in legacy['observations']:
        rows[r['compound_id']].append(r)
    same_doc = a.document_id == b.document_id
    measurements = None
    if same_doc:
        def slim(xs):
            return [{'text': ('' if x['relation'] in ('=', None) else x['relation']) + str(x['value'])
                     if x.get('value_status') != 'missing' else '缺失', 'unit': x.get('unit') or '',
                     'url': (x.get('measurement_source') or {}).get('url')} for x in xs]
        measurements = []
        for m in compare_rows(rows.get(a_id, []), rows.get(b_id, [])):
            first = (m['a'] or m['b'])[0]
            measurements.append({'assay_id': m['assay_id'], 'endpoint': first.get('endpoint'),
                                 'protocol': first.get('protocol'), 'a': slim(m['a']), 'b': slim(m['b']),
                                 'status': m['status'], 'ratio': m['ratio_b_over_a'], 'reasons': m['reasons']})
    by_label = defaultdict(dict)
    if not same_doc:  # cross-document: line up raw values by assay label only, never a ratio
        assays = {x.id: x for x in ledger.assays}
        for side, cid in (('a', a_id), ('b', b_id)):
            for o in ledger.observations:
                if o.compound_id == cid and o.review.record_status != 'rejected':
                    label = assays[o.assay_id].endpoint
                    by_label[label][side] = {**_value(o), 'source': o.source.model_dump(exclude_none=True),
                                             'protocol': assays[o.assay_id].protocol}
    da, db = descriptors(a.smiles), descriptors(b.smiles)
    keys = [('mw', 'MW'), ('clogp', 'cLogP'), ('tpsa', 'TPSA'), ('rotb', '可旋转键'), ('hbd', 'HBD'), ('hba', 'HBA'),
            ('heavy_atoms', '重原子数'), ('rings', '环数')]
    props = [{'key': k, 'label': lbl, 'a': da[k], 'b': db[k], 'delta': round(db[k] - da[k], 2)} for k, lbl in keys]
    relations = ledger.relations
    docs = {d.id: d for d in ledger.documents}

    def side(c):
        return {'id': c.id, 'label': c.label, 'short': c.label.split(' · ')[0], 'document_id': c.document_id,
                'document': _doc_meta(docs[c.document_id], relations), 'tier': tier(c),
                'status': c.review.record_status, 'stereo': c.stereochemistry_note, 'smiles': c.smiles,
                'structure_source': c.structure_source.model_dump(exclude_none=True)}
    return {'a': side(a), 'b': side(b), 'a_svg': pair['from_svg'], 'b_svg': pair['to_svg'],
            'mcs_note': pair['note'], 'mcs_timed_out': pair['timed_out'], 'same_document': same_doc,
            'measurements': measurements, 'parallel': [{'assay': k, **v} for k, v in by_label.items()],
            'properties': props,
            'notice': ('同一文档内：同一实验的精确值可给出 B/A 数值比，不自动解释为改善倍数。' if same_doc else
                       '跨文档对照：原始值并列展示，不计算 B/A 倍数；限定值不参与比较。')}


# ---------------------------------------------------------------- timeline

def timeline(ledger, study):
    """Families by priority date plus an R-group alignment of the curated patent examples (offline)."""
    from .patent_evidence import attach_evidence
    from .scaffolds import align_evidence
    relations = ledger.relations
    docs, comps, _, obs = scope(ledger, study)
    patents = sorted((d for d in docs if d.kind == 'patent'), key=lambda d: _doc_meta(d, relations).get('date') or '')
    families, packages = [], []
    for d in patents:
        meta = _doc_meta(d, relations)
        meta['examples'] = [{'id': c.id, 'label': c.label, 'smiles': c.smiles}
                            for c in comps if c.document_id == d.id and c.role == 'example']
        families.append(meta)
        pkg = {'publication': d.id, 'source_snapshot': {'sha256': d.source_sha256.get('html')}}
        try:
            attach_evidence(pkg)
        except Exception:
            pkg = None
        if pkg and pkg.get('evidence_cards'):
            packages.append(pkg)
    alignment = None
    if packages:
        try:
            full = align_evidence(packages)
            full['anchor'] = {k: v for k, v in (full.get('anchor') or {}).items() if k != 'svg'}
            alignment = {k: full.get(k) for k in ('status', 'reason', 'anchor', 'labels', 'notice', 'input_cards',
                                                   'unique_molecules', 'excluded', 'site_frequencies')}
            alignment['rows'] = [{k: r.get(k) for k in ('publication', 'label', 'example', 'smiles', 'matched',
                                                         'fragments', 'core', 'bridged_labels')}
                                 for r in full.get('rows', [])]
        except Exception as exc:  # alignment failures are shown, never replaced by a default
            alignment = {'error': f'对齐失败：{exc.__class__.__name__}'}
    ids = {d.id for d in docs}
    edges = [{'from': r.from_publication, 'to': r.to_publication, 'label': r.label, 'type': r.type}
             for r in relations if r.from_publication in ids and r.to_publication in ids]
    for f in families:
        for cited in f.get('cites', []):
            if cited in ids:
                edges.append({'from': cited, 'to': f['id'], 'label': '引用（来源：较晚专利引用列表）', 'type': 'cites'})
    # Cell potency column for the alignment table: the first cell assay each patent reports.
    cell = {}
    assays = {a.id: a for a in ledger.assays}
    for o in obs:
        a = assays.get(o.assay_id)
        if a and a.source_assay_id == 'cell_WT_IC50':
            cell[o.compound_id] = _value(o)
    return {'study': summary(ledger, study), 'families': families, 'edges': edges, 'alignment': alignment,
            'cell': cell, 'papers': [_doc_meta(d, relations) for d in docs if d.kind == 'paper']}


# ---------------------------------------------------------------- report

def report(ledger, study):
    ov = overview(ledger, study)
    ev = evidence(ledger, study)
    appendix = []
    for g in ev['groups']:
        if g['kind'] != 'patent':
            continue
        for r in g['rows']:
            pages = sorted({v['source'].get('pdf_page') for vals in r['values'].values() for v in vals
                            if v['source'].get('pdf_page')})
            appendix.append({'label': f"{g['id']} {r['short']}", 'tier': r['tier'], 'status': r['status'],
                             'structure': r['structure_source'].get('pdf_page'),
                             'structure_url': r['structure_source'].get('url'),
                             'activity': pages, 'stereo': r['stereo']})
    docs, _, _, _ = scope(ledger, study)
    papers = []
    for g in ev['groups']:
        if g['kind'] == 'paper':
            papers.append({'id': g['id'], 'title': g['title'], 'citation': g.get('citation'), 'tier': g['tier'],
                           'compounds': len(g['rows']), 'observations': g['observations'], 'url': g['url']})
    return {'study': ov['study'], 'families': ov['families'], 'relations': ov['relations'], 'papers': papers,
            'documents': [{'id': d.id, 'kind': d.kind, 'url': d.url, 'sha256': d.source_sha256} for d in docs],
            'notes': study.get('notes', []), 'appendix': appendix,
            'note_kinds': NOTE_KINDS, 'generated_at': _now()}


def report_markdown(rep, analysis=None, options=None):
    options = options or {}
    s = rep['study']
    lines = [f"# 竞对 SAR 调研：{s['name']}", '',
             f"生成于 {rep['generated_at']} · {s['patents']} 个专利家族 · {s['papers']} 篇论文 · "
             f"已确认证据 {s['confirmed']} 条，其余均为“待确认”。", '',
             '> 工具不自动生成结论；以下“研究假设”均由署名者填写，证据未经独立复核时逐条标明。', '',
             '## 1 结论摘要', '']
    summaries = [n for n in rep['notes'] if n['kind'] == 'summary']
    lines += [f"- {n['text']}（{n['author']}）" for n in summaries] or ['（尚未撰写）']
    lines += ['', '## 2 家族时间线', '', '| 优先权日 | 公开号 | 家族 | 收录实施例 |', '|---|---|---|---|']
    for f in rep['families']:
        lines.append(f"| {f.get('priority_date') or '未记载'} | {f['id']} | {f.get('family_id') or '未记载'} | "
                     f"{'、'.join(e['label'] for e in f['examples']) or '无'} |")
    n = 3
    if analysis:
        lines += ['', '## 3 SAR 分析 · 讨论材料', '', analysis.strip()]
        n = 4
    lines += ['', f'## {n} 事实、假设与缺口', '']
    for r in rep['relations']:
        lines += [f"### {r['label']}", '', '**可核查事实**', ''] + [f'- {x}' for x in r['facts']]
        if r['hypothesis']:
            lines += ['', '**研究假设（未署名）**', '', f"- {r['hypothesis']}"]
        lines += ['', '**证据缺口**', ''] + [f'- {x}' for x in r['gaps']] + ['']
    signed = [n for n in rep['notes'] if n['kind'] != 'summary']
    if signed:
        lines += ['### 署名判断', '']
        lines += [f"- [{NOTE_KINDS[n['kind']]}] {n['text']}（{n['author']}，{n['created_at'][:10]}；出处：{n['context']}）"
                  for n in signed]
    lines += ['', '## 附录 · 证据来源', '', '| 实施例 | 等级 | 复核 | 结构来源 | 活性来源 |', '|---|---|---|---|---|']
    for a in rep['appendix']:
        lines.append(f"| {a['label']} | {a['tier']} | {STATUS_LABEL[a['status']]} | "
                     f"{'PDF p.' + str(a['structure']) if a['structure'] else '未记载'} | "
                     f"{'、'.join('PDF p.' + str(p) for p in a['activity']) or '无测量'} |")
    if options.get('include_l1') and rep['papers']:
        lines += ['', '### 数据库导入（L1，原文定位待核对）', '']
        lines += [f"- {p['title']}（{p['citation']}）· ChEMBL 文档 {p['id']} · {p['compounds']} 个结构 · {p['observations']} 条观测"
                  for p in rep['papers']]
    if options.get('include_hashes'):
        lines += ['', '### 来源快照', '', '| 文档 | 类型 | 快照 SHA-256 |', '|---|---|---|']
        for d in rep['documents']:
            hashes = '；'.join(f'{k} {v}' for k, v in d['sha256'].items()) or '无（数据库记录）'
            lines.append(f"| {d['id']} | {'专利' if d['kind'] == 'patent' else '论文'} | {hashes} |")
    if not rep['study']['confirmed']:
        lines += ['', '> 本报告引用的证据均未经具名确认（待确认）。']
    return '\n'.join(lines) + '\n'
