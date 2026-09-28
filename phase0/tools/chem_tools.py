"""Deterministic chemistry and comparison tools. Every number comes from code, not the model."""
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import Field

from phase0.sar.evidence_pair import analyse_pair
from phase0.sar.features import StructureError, compute_features
from phase0.sar.mass_check import IONS, check_mass
from .core import ToolFailure, envelope, tool

Ion = Literal['[M+H]+', '[M+Na]+', '[M-H]-', '[M+2H]2+', 'M']
assert set(Ion.__args__) == set(IONS)


@tool('chem')
def chem_describe(ctx, smiles: str):
    """计算一个结构的规范化 SMILES、分子式、InChIKey 和 RDKit 描述符（分子量、cLogP、TPSA 等）。

    需要结构性质或确认两个 SMILES 是否为同一分子时调用。不要自行估算这些数值。
    """
    try:
        f = compute_features(smiles)
    except (StructureError, ValueError) as exc:
        raise ToolFailure(f'结构无法解析：{exc}') from None
    return envelope(f'{f.formula} · {f.inchikey}',
                    {'formula': f.formula, 'inchikey': f.inchikey, 'descriptors': f.numeric})


@tool('chem')
def chem_mass_check(ctx, smiles: str, reported: Union[int, float, str], ion: Ion = '[M+H]+'):
    """比对原文报告的质谱值与结构计算值，用于发现转录结构的原子数错误。

    从专利或论文转录结构后、提交台账前调用。reported 请按原文字符串传入（如 "357"、"370.22"），以保留报告精度。容差随报告精度变化（整数 ±0.6 Da，1–2 位小数 ±0.3 Da，
    ≥3 位小数 10 ppm）。consistent 只说明分子式相符，不能证明结构正确（同分异构、立体错误查不出）；
    inconsistent 或 alternative_match 时应回到原文核对，并在提交时说明。
    """
    result = check_mass(smiles, reported, ion)
    return envelope(f"{result['status']}（{ion}，报告 {reported}）", result)


def _values(rows):
    return [{'id': r['id'], 'relation': r['relation'], 'value': r['value'], 'unit': r['unit'],
             'value_status': r['value_status']} for r in rows]


@tool('chem')
def chem_compare_observations(ctx, a_observation_id: str, b_observation_id: str,
                              mode: Literal['compare', 'align'] = 'compare'):
    """对台账中两个分子做确定性对照：compare 逐实验检查能否计算 B/A，align 给出共同骨架与取代位点变化。

    需要判断“A 改成 B 后某实验数值如何变化”时调用，参数是两个分子各自任一条观测的编号。
    只有同来源、同实验、同终点、双方为精确值时才给出暂定比值；其余情况给出不计算的原因。
    比值是数值对照，不是改善倍数，也不代表单一基团的因果效果。
    """
    typed = ctx.store().load() if ctx.ledger_db else None
    try:
        result = analyse_pair({'mode': mode, 'a': a_observation_id, 'b': b_observation_id}, typed)
    except ValueError as exc:
        raise ToolFailure(str(exc)) from None
    base = {'a': {k: result['a'][k] for k in ('compound_id', 'compound_label', 'smiles', 'document')},
            'b': {k: result['b'][k] for k in ('compound_id', 'compound_label', 'smiles', 'document')},
            'notice': result['notice']}
    if mode == 'compare':
        rows = [{'assay_id': m['assay_id'], 'status': m['status'], 'ratio_b_over_a': m['ratio_b_over_a'],
                 'reasons': m['reasons'], 'a': _values(m['a']), 'b': _values(m['b'])} for m in result['measurements']]
        n = sum(r['ratio_b_over_a'] is not None for r in rows)
        return envelope(f'{len(rows)} 个实验中 {n} 个可暂定计算 B/A。', {**base, 'measurements': rows}, rows[:5])
    aligned = result['alignment']
    data = {**base, 'labels': aligned.get('labels'), 'changes': aligned.get('changes'),
            'alignment_notice': aligned.get('notice'),
            'alignment_status': aligned.get('status')}
    return envelope(f"取代位点变化 {len(aligned.get('changes') or [])} 处。", data)


def _ledger(ctx):
    if ctx.ledger_db:
        return ctx.store().load()
    from phase0.ledger.migrate import build
    return build()


@tool('chem')
def project_goal_suggest(ctx, template: Literal['cell_potency_efflux', 'potency_metabolic_stability'],
                         focus: Optional[str] = None, documents: Optional[list[str]] = None):
    """为项目目标模板建议每个性质对应的台账实验，并列出未归入的实验和范围内全部实验。

    做多性质 SAR 分析前调用。focus 为靶点关键词（如 "ALK"），用来排除脱靶选择性实验。
    建议只是起点：把结果交给用户确认，或只保留你能从实验描述中说明理由的实验；阈值为默认值，待化学家确认。
    """
    from phase0.sar.project_sar import handle
    try:
        r = handle({'mode': 'suggest', 'template': template, 'focus': focus, 'documents': documents}, _ledger(ctx))
    except ValueError as exc:
        raise ToolFailure(str(exc)) from None
    summary = '；'.join(f"{p['label']} {len(p['suggested'])} 个实验" for p in r['properties'])
    return envelope(f"{r['label']}：{summary}；未归入 {len(r['unmapped'])} 个。", r,
                    [{'property': p['label'], 'assays': [h['assay_id'] for h in p['suggested']]} for p in r['properties']])


@tool('chem')
def project_sar_analyse(ctx, goal: dict[str, Any], documents: Optional[list[str]] = None,
                        max_change: Annotated[int, Field(ge=1, le=20)] = 12, include_report: bool = False):
    """按确认过的项目目标，在台账中自动找分子对（MMP），逐性质、逐实验比较并按替换 / 位点汇总，给出补测建议。

    在 project_goal_suggest 之后、目标（性质、实验、方向、阈值、可选目标区间）确认后调用。
    结果是讨论材料：候选方向 / 取舍 / 不利 / 证据不足按固定规则分类，证据等级按公开规则，不合成综合分数；
    默认阈值待化学家确认，合成可行性只列待评估项。include_report=true 时附 Markdown 讨论材料。
    """
    from phase0.sar.project_sar import handle
    request = {'mode': 'report' if include_report else 'analyse', 'goal': goal, 'documents': documents,
               'max_change': max_change}
    try:
        r = handle(request, _ledger(ctx))
    except ValueError as exc:
        raise ToolFailure(str(exc)) from None
    result = r['analysis'] if include_report else r
    counts = {}
    for t in result['transforms']:
        counts[t['category']['label'].split('：')[0]] = counts.get(t['category']['label'].split('：')[0], 0) + 1
    summary = (f"{result['pair_count']} 个分子对、{len(result['transforms'])} 种替换（"
               + '，'.join(f'{k} {v}' for k, v in counts.items()) + f"）；补测建议 {result['followups']['total']} 项。")
    return envelope(summary, r, result['followups']['items'][:5])
