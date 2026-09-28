"""Ledger tools: read, and propose. There is deliberately no confirm, reject or import tool.

Confirmation needs a named human reviewer (``LedgerStore.review``); an agent can
only add ``proposed`` records, and its writes are attributed to its run.
"""
import json
from typing import Annotated, Any, Literal, Optional

from pydantic import Field, ValidationError

from phase0.ledger.access import usable
from phase0.ledger.intake import from_activities, from_patent
from phase0.ledger.store import KINDS
from .core import PREVIEW, ToolFailure, envelope, tool

Kind = Literal['documents', 'compounds', 'assays', 'observations', 'relations']
Status = Literal['measured', 'not_tested', 'blank', 'not_reported', 'not_applicable']


def _dump(record):
    return json.loads(record.model_dump_json(exclude_unset=True))


def _compact(o):
    return {'id': o.id, 'document_id': o.document_id, 'compound_id': o.compound_id, 'assay_id': o.assay_id,
            'status': o.status, 'relation': o.relation, 'value': o.value, 'unit': o.unit, 'grade': o.grade,
            'record_status': o.review.record_status, 'gaps': o.review.gaps}


@tool('ledger')
def ledger_overview(ctx):
    """查看证据台账概况：各类记录数、复核状态分布、最常见的证据缺口。

    在开始查询或提交记录之前先调用一次，了解台账覆盖了哪些文档和实验。
    所有记录都带复核状态：proposed（待确认）、confirmed（人工确认）、rejected（人工拒绝）。
    """
    ledger = ctx.store().load()
    counts = {k: {} for k in KINDS}
    gaps = {}
    for k in KINDS:
        for r in getattr(ledger, k):
            counts[k][r.review.record_status] = counts[k].get(r.review.record_status, 0) + 1
            for g in r.review.gaps:
                gaps[g] = gaps.get(g, 0) + 1
    docs = [{'id': d.id, 'kind': d.kind, 'record_status': d.review.record_status} for d in ledger.documents]
    total = sum(sum(v.values()) for v in counts.values())
    return envelope(f'台账共 {total} 条记录，其中观测 {len(ledger.observations)} 条、文档 {len(docs)} 份。',
                    {'counts': counts, 'gaps': dict(sorted(gaps.items(), key=lambda x: -x[1])), 'documents': docs},
                    docs[:PREVIEW])


@tool('ledger')
def ledger_search_observations(ctx, compound_id: Optional[str] = None, assay_id: Optional[str] = None,
                               document_id: Optional[str] = None, status: Optional[Status] = None,
                               include_rejected: bool = False,
                               limit: Annotated[int, Field(ge=1, le=100)] = 20,
                               offset: Annotated[int, Field(ge=0)] = 0):
    """按化合物、实验（assay）、文档或观测状态筛选台账中的观测记录。

    需要某个分子或某个实验的原始测量时调用。返回原值、限定符（如 "<"）、单位和复核状态；
    缺失测量以 status 表示（not_tested / blank / not_reported），不会带数值。
    限定值不能当作精确值计算倍数；需要判断两条测量能否比较时，改用 chem_compare_observations。
    默认不含被人工拒绝的记录。
    """
    ledger = ctx.store().load()
    source = ledger if include_rejected else usable(ledger)
    rows = [o for o in source.observations
            if (compound_id is None or o.compound_id == compound_id)
            and (assay_id is None or o.assay_id == assay_id)
            and (document_id is None or o.document_id == document_id)
            and (status is None or o.status == status)]
    page = [_compact(o) for o in rows[offset:offset + limit]]
    return envelope(f'匹配 {len(rows)} 条观测，返回第 {offset + 1 if page else 0}–{offset + len(page)} 条。',
                    {'total': len(rows), 'offset': offset, 'rows': page}, page[:PREVIEW])


@tool('ledger')
def ledger_get_record(ctx, kind: Kind, record_id: str):
    """读取台账中一条完整记录（含来源定位、原始记录和复核缺口）。

    在引用某条证据、或提交与之相关的新记录之前调用，核对它的来源页码和复核状态。
    """
    ledger = ctx.store().load()
    for r in getattr(ledger, kind):
        if r.id == record_id:
            data = _dump(r)
            return envelope(f'{kind} {record_id}：复核状态 {r.review.record_status}。', data)
    raise ToolFailure(f'{kind} 中没有编号 {record_id}；可先用 ledger_search_observations 或 ledger_overview 查找。')


@tool('ledger')
def ledger_record_schema(ctx, kind: Kind):
    """返回某类台账记录的 JSON Schema。

    在调用 ledger_propose 之前调用，按 schema 构造记录：必填字段、缺失语义（status）、
    限定符取值、复核状态只能为 proposed。
    """
    from phase0.ledger.schema import Ledger
    schema = Ledger.model_json_schema()
    name = {'documents': 'Document', 'compounds': 'Compound', 'assays': 'Assay',
            'observations': 'Observation', 'relations': 'DocumentRelation'}[kind]
    return envelope(f'{kind} 记录的 JSON Schema（定义见 $defs）。',
                    {'record': schema['$defs'][name], '$defs': schema['$defs']})


@tool('ledger', read_only=False)
def ledger_propose(ctx, kind: Kind, record: dict[str, Any], note: Annotated[str, Field(min_length=4)]):
    """向台账提交一条新记录，状态必须是 proposed（待人工复核）。

    仅在你已从原文或数据库中读到依据时调用，并在 note 中写明依据（例如 PDF 页码与表格位置）。
    规则：review.record_status 必须是 "proposed"；编号已存在时跳过、不覆盖；
    记录必须通过完整校验（引用的文档、化合物、实验必须存在；缺失测量不能带数值；限定符原样保留）。
    本工具不能确认或拒绝记录，确认只能由具名的人完成。
    """
    store = ctx.store()
    try:
        result = store.propose_batch([(kind, record)], ctx.actor, note=note)
    except PermissionError as exc:
        raise ToolFailure(str(exc)) from None
    except ValidationError as exc:
        problems = '；'.join((f"{'.'.join(map(str, e['loc']))}: " if e['loc'] else '') + e['msg'].removeprefix('Value error, ')
                             for e in exc.errors()[:8])
        raise ToolFailure(f'记录未通过校验，未写入：{problems}') from None
    except ValueError as exc:
        raise ToolFailure(f'记录未写入：{exc}') from None
    added = [i for _, i in result['added']]
    skipped = [i for _, i in result['already_present']]
    summary = f'已提交 {len(added)} 条（待确认）。' if added else f'编号已存在，未覆盖：{"、".join(skipped)}。'
    return envelope(summary, {'added': added, 'already_present': skipped, 'actor': ctx.actor})


@tool('ledger', read_only=False, open_world=True)
def ledger_propose_chembl_activities(ctx, entity: Literal['target', 'molecule'], chembl_id: str,
                                     activity_ids: Annotated[list[int], Field(min_length=1, max_length=20)],
                                     offset: Annotated[int, Field(ge=0, le=10000)] = 0,
                                     note: Annotated[str, Field(min_length=4)] = 'ChEMBL 测量页'):
    """把一页 ChEMBL 测量中选定的记录加入台账（待确认）。

    在用 chembl_activities 看过某一页、并确认要纳入的 activity_id 之后调用。数值由服务端重新读取该页得到，
    不接受你传入的数值；限定符缺失、文档类型未确认、缺少结构或同一实验终点冲突的记录会被拒绝并说明原因。
    """
    from phase0.sar.discovery import discover, discover_documents
    cache = ctx.cache_dir / 'discovery-cache'
    query = {'entity': entity, 'id': chembl_id, 'offset': offset}
    try:
        page = discover({**query, 'mode': 'activities'}, cache)
        wanted = {str(x) for x in activity_ids}
        chosen = [a for a in page['activities'] if str(a.get('activity_id')) in wanted]
        docs = discover_documents(query, cache)['documents'] if chosen else []
    except (ValueError, RuntimeError) as exc:
        raise ToolFailure(str(exc)) from None
    store = ctx.store()
    items, refused, present = from_activities(
        chosen, {d['id']: d['document'] for d in docs if d.get('document')}, store.load())
    refused += [{'id': x, 'reason': '不在该页结果中'} for x in sorted(wanted - {str(a.get('activity_id')) for a in chosen})]
    result = store.propose_batch(items, ctx.actor, note=note)
    added = {}
    for kind, _ in result['added']:
        added[kind] = added.get(kind, 0) + 1
    return envelope(f"已提交测量 {added.get('observations', 0)} 条（待确认），拒绝 {len(refused)} 条，已在台账中 {len(present)} 条。",
                    {'added': added, 'refused': refused, 'already_present': present}, refused[:PREVIEW])


@tool('ledger', read_only=False, open_world=True)
def ledger_propose_patent_index(ctx, publication: str, note: Annotated[str, Field(min_length=4)] = '专利化学实体索引'):
    """把一份专利及其自动化学实体索引结构加入台账（待确认）。

    在用 patent_fetch 读过该专利之后调用。索引结构未映射到实施例，角色记为 unspecified，也不带任何测量值；
    不要把索引结构当作实施例或已测化合物。
    """
    from phase0.sar.patents import normalize_id, retrieve
    try:
        result = retrieve(normalize_id(publication), ctx.cache_dir / 'patent-cache')
    except ValueError as exc:
        raise ToolFailure(str(exc)) from None
    items, refused = from_patent(result)
    out = ctx.store().propose_batch(items, ctx.actor, note=note)
    added = {}
    for kind, _ in out['added']:
        added[kind] = added.get(kind, 0) + 1
    return envelope(f"已提交：{added or '无新增'}；拒绝 {len(refused)} 条。",
                    {'added': added, 'refused': refused, 'already_present': [i for _, i in out['already_present']]})
