"""Offline walkthrough of tiered extraction control, scored by the evaluator.

    python -m phase0.extract.demo

The extractors here are **simulated** from gold v0 (the curated WO2011138751A2 cards),
with one deliberate transcription error: Example 6 loses its fluorine at L1. The page
texts used for partitioning are short samples. This shows the control logic, the
reviewer report and the scoring -- not real extraction accuracy.
"""
import sys

from phase0.eval.extraction_metrics import evaluate
from phase0.eval.gold import GOLD, load
from .cascade import Cascade, Fact
from .outputs import report, to_prediction
from .partition import partition

PUB = 'WO2011138751A2'
SAMPLE_PAGES = [
    (125, 'Intermediate 12\nStep 1: to a solution of the aminopyridine ...'),
    (127, 'Example 1\n... LCMS m/z 370.22 [M+H]+\nExample 6\n...'),
    (128, 'Example 7\n... LCMS 371 [M+H]+'),
    (180, 'The compounds of the invention inhibit ALK. IC50 values were determined as described.'),
    (186, 'Table 3\nExample  WT Ki (nM)  L1196M Ki (nM)  Cell IC50 (nM)\n1  2.90  18.0  144\n6  0.62  2.75\n7  0.536  3.16  33.0'),
]


def _fact(kind, example, field, value, level, assay=None, page=None):
    return Fact(kind, example, field, value, level, f'{level} 模拟来源', PUB, f'Example {example}',
                assay, page, f'{field} @ PDF p.{page}' if page else None)


def simulated_levels(gold):
    compounds, observations = gold['compounds'], gold['observations']

    def l1_structured(publication, wanted):
        out = []
        for c in compounds:
            smiles = c['smiles'].replace('(F)', '') if c['example'] == '6' else c['smiles']  # deliberate error
            out += [_fact('compound', c['example'], 'smiles', smiles, 'L1', page=c['structure_pdf_page']),
                    _fact('compound', c['example'], 'role', 'example', 'L1')]
        out += [_fact('observation', o['example'], 'status', o['status'], 'L1', o['assay_id'], o['table_pdf_page'])
                for o in observations]
        return out

    def l2_full_text(publication, wanted):
        out = [_fact('compound', c['example'], 'reported_mass', str(c['reported_mass']), 'L2') for c in compounds]
        asked = {(slot[1], slot[2], f) for slot, f in wanted or []}
        for o in observations:
            out += [_fact('observation', o['example'], f, o[f], 'L2', o['assay_id'], o['table_pdf_page'])
                    for f in ('relation', 'value', 'unit') if (o['example'], o['assay_id'], f) in asked]
        return out

    def l3_pdf(publication, wanted):
        raise RuntimeError('L3 不应被调用：必填字段已齐全')

    return [('L1 结构化记录', l1_structured), ('L2 专利全文', l2_full_text), ('L3 PDF 图表识别', l3_pdf)]


def run_demo(out=print):
    gold = load(GOLD / f'{PUB}.json')
    pages = partition(SAMPLE_PAGES)
    result = Cascade(PUB, simulated_levels(gold)).run()
    out('注意：抽取器为模拟（数据取自金标准 v0，Example 6 故意漏掉氟原子）；页面文字为示例。\n')
    out(report(result, pages))
    scored = evaluate(gold, to_prediction(result))
    c = scored['counts']
    out(f"评测：错误 {c['errors']}（其中未标记 {c['unflagged_errors']}），编造数值 {c['fabricated_values']}，"
        f"可比性误判 {c['false_comparable']}，漏抽 {c['missed']}")
    return scored


def main(argv=None):
    scored = run_demo()
    return 0 if scored['counts']['unflagged_errors'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
