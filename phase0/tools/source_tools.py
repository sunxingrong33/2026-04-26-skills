"""Source tools: read public sources through the existing cached retrievers. Read-only, network."""
from typing import Annotated, Literal

from pydantic import Field

from .core import PREVIEW, ToolFailure, envelope, tool


@tool('sources', open_world=True)
def patent_fetch(ctx, publication: str):
    """读取一份专利的公开页面：家族、优先权日、申请人、引用、实施例标题、化学实体索引，以及已整理的证据卡。

    需要某份专利的元数据或结构时调用，参数为完整公开号（如 WO2013132376A1）。
    化学实体索引可能包含试剂和中间体，且未映射实施例；只有 evidence_cards 是逐项核对过 PDF 的实施例与测量。
    """
    from phase0.sar.patents import normalize_id, retrieve
    try:
        r = retrieve(normalize_id(publication), ctx.cache_dir / 'patent-cache')
    except ValueError as exc:
        raise ToolFailure(str(exc)) from None
    cards = [{'example': c['example'], 'smiles': c['smiles'], 'review_status': c.get('review_status'),
              'measurements': [{'assay_id': m['assay_id'], 'raw': m.get('raw')} for m in c['measurements']],
              'missing': [m['assay_id'] for m in c.get('missing_measurements', [])],
              'mass_check': c.get('mass_check', {}).get('status')} for c in r.get('evidence_cards', [])]
    structures = [{'smiles': s['smiles'], 'inchikey': s['inchikey']} for s in r.get('structures', [])]
    data = {k: r.get(k) for k in ('publication', 'title', 'assignee', 'priority_date', 'family_id',
                                   'family_members', 'references', 'source_url', 'pdf_url')}
    data.update(source_sha256=r['source_snapshot']['sha256'], evidence_status=r.get('evidence_status'),
                evidence_cards=cards, examples=[e['label'] for e in r.get('examples', [])],
                structure_index=structures)
    return envelope(f"{r['publication']}：证据卡 {len(cards)} 张，索引结构 {len(structures)} 个，实施例标题 {len(data['examples'])} 个。",
                    data, structures[:PREVIEW])


@tool('sources', open_world=True)
def chembl_activities(ctx, entity: Literal['target', 'molecule'], chembl_id: str,
                      offset: Annotated[int, Field(ge=0, le=10000, multiple_of=20)] = 0):
    """读取 ChEMBL 中某靶点或某分子的一页测量记录（每页 20 条，按 activity_id 排序，不是效力排名）。

    需要公开数据库测量时调用。记录保留限定符、缺失值和质量标记；同一实验可能有重复测量；
    数据库关联不证明结构来自哪个实施例。确认要纳入的记录后，用 ledger_propose_chembl_activities 提交。
    """
    from phase0.sar.discovery import discover
    try:
        page = discover({'mode': 'activities', 'entity': entity, 'id': chembl_id, 'offset': offset},
                        ctx.cache_dir / 'discovery-cache')
    except (ValueError, RuntimeError) as exc:
        raise ToolFailure(str(exc)) from None
    keys = ('activity_id', 'molecule_chembl_id', 'canonical_smiles', 'assay_chembl_id', 'standard_type',
            'standard_relation', 'standard_value', 'standard_units', 'document_chembl_id', 'data_validity_comment')
    rows = [{k: a.get(k) for k in keys} for a in page['activities']]
    return envelope(f"共 {page.get('total')} 条，本页 {len(rows)} 条（offset {offset}）。",
                    {'total': page.get('total'), 'has_more': page.get('has_more'), 'rows': rows,
                     # cache_hit is transport detail; dropping it keeps results identical on replay.
                     'sources': [{k: v for k, v in s.items() if k != 'cache_hit'} for s in page['sources']],
                     'notice': page.get('notice')}, rows[:PREVIEW])
