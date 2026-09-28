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


@tool('sources', open_world=True)
def structure_search(ctx, smiles: str, method: Literal['exact', 'similarity', 'substructure'] = 'similarity',
                     threshold: Annotated[int, Field(ge=40, le=100)] = 70, standardize: bool = True,
                     external: bool = True, surechembl: bool = False):
    """按结构检索本地证据台账与 ChEMBL：精确（标准 InChIKey）、相似性（Tanimoto，阈值为百分比）或子结构。

    需要“从一个结构出发找相关化合物、文献和专利”时调用；ChEMBL 命中后可用 chembl_activities（entity=molecule）
    查看测量及其来源文档。相似度只用于排序，不说明活性相近、属于同一研发程序或被某专利覆盖；
    未命中不代表不存在。ChEMBL 命中已用本地 RDKit 复核，local_check 为 disagrees 的记录需回到原始记录核对。
    默认标准化（去盐、中和、互变异构；子结构查询不做互变异构），search.steps 记录实际做了哪些改动。
    surechembl=true 时另查 SureChEMBL（专利中自动提取的化学结构；异步任务，可能需要一两分钟）；
    命中只说明结构出现在专利文本或图像中，不说明是实施例或被权利要求覆盖。
    需要看某个命中出现在哪些专利时，调用 surechembl_patents。引用时保留 SCHEMBL 编号并注明 SureChEMBL（CC BY 4.0）。
    """
    from phase0.sar import structure_search as ss
    from phase0.sar.discovery import fetch
    try:
        q, mol = ss.query({'query': smiles, 'method': method, 'threshold': threshold, 'standardize': standardize})
    except ValueError as exc:
        raise ToolFailure(str(exc)) from None
    if ctx.ledger_db:
        ledger = ctx.store().load()
    else:
        from phase0.ledger.migrate import build
        ledger = build()
    local = ss.local_search(q, mol, ledger)
    data = {'search': q, 'notice': ss.NOTICE[method] + ss.ZERO_HITS, 'local': local, 'chembl': None,
            'surechembl': None}
    if external:
        try:
            if method == 'exact':
                from phase0.sar.discovery import discover
                page = discover({'mode': 'smiles', 'query': q['searched_smiles'], 'method': 'exact',
                                 'standardize': False, 'external': True}, ctx.cache_dir / 'discovery-cache')
                if page['external_status'] != 'ok':
                    raise RuntimeError('ChEMBL 暂不可用')
                rows = [{'molecule_chembl_id': m.get('molecule_chembl_id'), 'pref_name': m.get('pref_name')}
                        for m in page['molecules']]
                chembl = {'total': page.get('total'), 'truncated': bool(page.get('has_more')), 'rows': rows,
                          'source': page['sources'][0]}
            else:
                chembl = ss.chembl_search(q, mol, ctx.cache_dir / 'discovery-cache', fetch)
        except OSError:
            # Transport errors vary between runs; a fixed message keeps failures replayable.
            raise ToolFailure('ChEMBL 结构检索失败：连接失败或超时；可设 external=false 只查本地。') from None
        except (ValueError, RuntimeError) as exc:
            raise ToolFailure(f'ChEMBL 结构检索失败：{exc}；可设 external=false 只查本地。') from None
        # cache_hit is transport detail; dropping it keeps results identical on replay.
        chembl['source'] = {k: v for k, v in chembl['source'].items() if k != 'cache_hit'}
        data['chembl'] = chembl
    if surechembl:
        try:
            sc = ss.surechembl_search(q, mol, ctx.cache_dir / 'discovery-cache' / 'surechembl')
        except OSError:
            raise ToolFailure('SureChEMBL 检索失败：连接失败或超时；可设 surechembl=false。') from None
        except (ValueError, RuntimeError, KeyError) as exc:
            raise ToolFailure(f'SureChEMBL 检索失败：{exc}；可设 surechembl=false。') from None
        sc['source'] = {k: v for k, v in sc['source'].items() if k != 'cache_hit'}
        data['surechembl'] = sc
    n, sc = data['chembl'], data['surechembl']
    summary = (f"本地命中 {local['total']} 个" + ('' if n is None else
               f"；ChEMBL 命中 {n['total']} 个（本页 {len(n['rows'])} 个{'，已截断' if n['truncated'] else ''}）")
               + ('' if sc is None else
                  f"；SureChEMBL 命中 {sc['total']} 个（本页保留 {len(sc['rows'])} 个{'，已截断' if sc['truncated'] else ''}）"))
    rows = (n['rows'] if n else []) + (sc['rows'] if sc else []) or local['rows']
    return envelope(summary + '。', data, rows[:PREVIEW])


@tool('sources', open_world=True)
def surechembl_patents(ctx, compound: str):
    """列出 SureChEMBL 在哪些专利中提取到某个化合物（前 20 份，附总数）。

    需要知道某个 SureChEMBL 命中出现在哪些专利时调用；compound 为 SCHEMBL 编号（如 SCHEMBL1353），
    通常来自 structure_search 的 SureChEMBL 命中。
    结果说明该结构出现在专利文本或图像中，不说明是实施例、被权利要求覆盖或经过测试；
    公开号需用 patent_fetch 读取原始专利页面核实。引用时保留 SCHEMBL 编号并注明 SureChEMBL（CC BY 4.0）。
    """
    from phase0.sar.discovery import surechembl_documents
    try:
        r = surechembl_documents({'id': compound}, ctx.cache_dir / 'discovery-cache')
    except (ValueError, RuntimeError) as exc:
        raise ToolFailure(str(exc)) from None
    sources = [{k: v for k, v in x.items() if k != 'cache_hit'} for x in r['sources']]
    data = {k: r[k] for k in ('id', 'total', 'truncated', 'patents', 'notice', 'attribution')} | {'sources': sources}
    return envelope(f"{r['id']}：出现在 {r['total']} 份专利中（本页 {len(r['patents'])} 份）。", data,
                    r['patents'][:PREVIEW])


@tool('sources', open_world=True)
def pubchem_xrefs(ctx, inchikey: str):
    """列出 PubChem 中与某个结构（标准 InChIKey 精确匹配）关联的专利与 PubMed 文献（各前 20 条，附总数）。

    需要从一个具体分子找相关专利和文献时调用；InChIKey 可用 chem_describe 得到，或取自 structure_search 命中。
    关联由提交者登记，不说明是实施例、被权利要求覆盖或经过测试；专利关联与 SureChEMBL 可能同源，不要当作独立佐证。
    curated_matches 列出已整理证据包中的专利；其余公开号需用 patent_fetch 读取原始页面核实。引用时注明 PubChem 与 CID。
    """
    from phase0.sar.discovery import pubchem_xrefs as lookup
    try:
        r = lookup({'inchikey': inchikey}, ctx.cache_dir / 'discovery-cache')
    except (ValueError, RuntimeError) as exc:
        raise ToolFailure(str(exc)) from None
    r['sources'] = [{k: v for k, v in x.items() if k != 'cache_hit'} for x in r['sources']]
    summary = (f"{r['id']}：未找到 PubChem 化合物。" if not r['cids'] else
               f"CID {', '.join(map(str, r['cids']))}：专利 {r['patents']['total']} 份"
               f"（已整理 {len(r['patents']['curated_matches'])} 份），PubMed 文献 {r['literature']['total']} 篇。")
    return envelope(summary, r, r['patents']['rows'][:PREVIEW])
