"""Tiered extraction control: partitioning, identity checks, escalation, conflicts, confidence, outputs."""
import copy

import pytest

from phase0.eval.extraction_metrics import evaluate
from phase0.eval.gold import GOLD, load
from phase0.extract.cascade import Cascade, Fact
from phase0.extract.outputs import report, to_ledger_items, to_prediction
from phase0.extract.partition import pages_for, partition
from phase0.ledger.migrate import build
from phase0.ledger.store import LedgerStore

PUB = 'WO2011138751A2'
GOLD_DOC = load(GOLD / f'{PUB}.json')
NAMES = {c['example']: f'name-of-{c["example"]}' for c in GOLD_DOC['compounds']}
RESOLVE = {f'name-of-{c["example"]}': c['smiles'] for c in GOLD_DOC['compounds']}


def fact(kind, example, field, value, level, assay_id=None, page=None, **seen):
    return Fact(kind, example, field, value, level, f'{level}-source',
                seen.get('pub', PUB), seen.get('ex', f'Example {example}'), assay_id, page, f'{field} @ p{page}')


def level_structures(overrides=None):
    """L1: structured records give structures and roles only."""
    overrides = overrides or {}
    def extract(publication, wanted):
        out = []
        for c in GOLD_DOC['compounds']:
            smiles = overrides.get(c['example'], c['smiles'])
            out += [fact('compound', c['example'], 'smiles', smiles, 'L1', page=c['structure_pdf_page']),
                    fact('compound', c['example'], 'role', 'example', 'L1')]
        for o in GOLD_DOC['observations']:
            out.append(fact('observation', o['example'], 'status', o['status'], 'L1', o['assay_id'],
                            page=o['table_pdf_page']))
        return out
    return extract


def level_text(value_overrides=None, with_names=False):
    """L2: full text gives reported masses, names and the table values that were asked for."""
    value_overrides = value_overrides or {}
    def extract(publication, wanted):
        out = [fact('compound', c['example'], 'reported_mass', str(c['reported_mass']), 'L2')
               for c in GOLD_DOC['compounds']]
        if with_names:
            out += [fact('compound', c['example'], 'name', NAMES[c['example']], 'L2') for c in GOLD_DOC['compounds']]
        asked = {(slot[1], slot[2], f) for slot, f in wanted or []}
        for o in GOLD_DOC['observations']:
            for f in ('relation', 'value', 'unit'):
                if (o['example'], o['assay_id'], f) in asked:
                    v = value_overrides.get((o['example'], o['assay_id'], f), o[f])
                    out.append(fact('observation', o['example'], f, v, 'L2', o['assay_id'], o['table_pdf_page']))
        return out
    return extract


def must_not_run(publication, wanted):
    raise AssertionError('L3 should not be called once nothing required is missing')


def run(levels, **kw):
    return Cascade(PUB, levels, name_to_smiles=RESOLVE.get, **kw).run()


# -- partitioning ------------------------------------------------------------

PAGES = [
    (9, 'Intermediate 12\nStep 1: to a solution of ...'),
    (10, 'Example 6\n(R)-3-[1-(5-fluoro...)]\nLCMS m/z 357 [M+H]+'),
    (11, 'Example 7\n...\nExample 8\n...'),
    (15, 'The compounds inhibit ALK. IC50 values were measured.'),
    (20, 'Table 1. Biological activity\nExample  ALK Ki (nM)  Cell IC50 (nM)\n1  2.90  144\n6  0.62\n7  0.536  33.0'),
    (21, 'Table 1 (continued)\n8  < 0.200  6.41\n9  1.93  97.1'),
]


def test_partition_finds_regions_with_evidence_and_ignores_passing_mentions():
    result = partition(PAGES)
    regions = [(r['region'], r['start'], r['end']) for r in result['ranges']]
    assert regions == [('intermediate', 9, 9), ('example', 10, 11), ('activity_table', 20, 21)]
    assert pages_for(result, 'activity_table') == [20, 21]
    example = next(r for r in result['ranges'] if r['region'] == 'example')
    assert example['identifiers'] == ['6', '7', '8']
    assert any('Table 1' in e for e in next(r for r in result['ranges'] if r['region'] == 'activity_table')['evidence'])
    assert next(p for p in result['pages'] if p['page'] == 15)['region'] == 'other'


# -- cascade control -----------------------------------------------------------

def test_only_missing_fields_escalate_and_later_levels_are_skipped():
    result = run([('L1', level_structures()), ('L2', level_text()), ('L3', must_not_run)])
    l1, l2, l3 = result.log
    assert l1['requested'] == 'all' and l1['missing_after'] == 30
    assert l2['requested'] == 30 and l2['missing_after'] == 0
    assert l3 == {'level': 'L3', 'action': 'not_called', 'reason': '必填字段已齐全'}


def test_facts_with_wrong_identity_are_rejected_not_rekeyed():
    def l1(publication, wanted):
        return [fact('compound', '6', 'smiles', 'CCO', 'L1', pub='WO2013132376A1'),
                fact('compound', '6', 'smiles', 'CCO', 'L1', ex='Example 7'),
                fact('compound', '6', 'role', 'product', 'L1'),
                fact('observation', '6', 'value', float('nan'), 'L1', 'ALK_WT_Ki')]
    result = run([('L1', l1)])
    reasons = [r['reason'] for r in result.rejected]
    assert reasons[0].startswith('公开号不符') and reasons[1].startswith('实施例编号不符')
    assert reasons[2].startswith('角色无效') and reasons[3] == '数值不是有限数'
    assert result.slots == {}


def test_extractor_failure_is_logged_and_the_cascade_continues():
    def broken(publication, wanted):
        raise TimeoutError('OCR service timed out')
    result = run([('L1', level_structures()), ('L2', broken), ('L3', level_text())])
    assert result.log[1]['action'] == 'failed' and 'TimeoutError' in result.log[1]['error']
    assert result.log[2]['action'] == 'called' and result.log[2]['missing_after'] == 0


def test_disagreeing_levels_become_a_conflict_not_a_choice():
    def l3(publication, wanted):
        return [fact('observation', '1', 'value', 29.0, 'L3', 'ALK_WT_Ki', 186)]
    result = run([('L1', level_structures()), ('L2', level_text()), ('L3', l3)])
    # L3 is not called when L2 completes everything; force it by making L2 miss that value.
    assert result.log[2]['action'] == 'not_called'
    missing_one = level_text(value_overrides={('1', 'ALK_WT_Ki', 'value'): None})
    def l2(publication, wanted):
        return [f for f in missing_one(publication, wanted) if f.value is not None]
    def l3b(publication, wanted):
        return [fact('observation', '1', 'value', 2.9, 'L3', 'ALK_WT_Ki', 186),
                fact('observation', '1', 'unit', 'µM', 'L3', 'ALK_WT_Ki', 186)]
    result = run([('L1', level_structures()), ('L2', l2), ('L3', l3b)])
    a = result.assessments[('observation', '1', 'ALK_WT_Ki')]
    assert a['confidence'] == 'low' and 'conflict:unit' in a['flags']
    assert result.slots[('observation', '1', 'ALK_WT_Ki')]['unit']['value'] == 'nM'


def test_confidence_needs_independent_agreement():
    medium = run([('L1', level_structures()), ('L2', level_text())])
    assert {medium.assessments[('compound', e, None)]['confidence'] for e in ('1', '6', '7')} == {'medium'}
    high = run([('L1', level_structures()), ('L2', level_text(with_names=True))])
    a = high.assessments[('compound', '6', None)]
    assert a['confidence'] == 'high' and a['checks'] == {'mass': 'consistent', 'name': 'agree'}


def test_mass_mismatch_lowers_confidence_even_when_the_name_agrees_elsewhere():
    wrong = {'6': next(c['smiles'] for c in GOLD_DOC['compounds'] if c['example'] == '6').replace('(F)', '')}
    result = run([('L1', level_structures(wrong)), ('L2', level_text(with_names=True))])
    a = result.assessments[('compound', '6', None)]
    assert a['confidence'] == 'low' and {'mass_mismatch', 'name_disagrees'} <= set(a['flags'])


def test_untested_cells_complete_without_a_number():
    result = run([('L1', level_structures()), ('L2', level_text())])
    a = result.assessments[('observation', '6', 'cell_WT_IC50')]
    assert a['missing'] == [] and 'value' not in result.slots[('observation', '6', 'cell_WT_IC50')]


# -- outputs and scoring -------------------------------------------------------

def test_prediction_scores_perfectly_against_gold_when_extractors_are_right():
    prediction = to_prediction(run([('L1', level_structures()), ('L2', level_text(with_names=True))]))
    report_ = evaluate(GOLD_DOC, prediction)
    assert report_['counts']['errors'] == 0 and report_['counts']['missed'] == 0
    assert all(r['value'] == 1 for r in report_['rates'].values() if r['total'])


def test_single_source_value_error_is_counted_but_flagged():
    wrong = level_text(value_overrides={('7', 'ALK_WT_Ki', 'value'): 5.36}, with_names=True)
    report_ = evaluate(GOLD_DOC, to_prediction(run([('L1', level_structures()), ('L2', wrong)])))
    assert report_['counts']['errors'] == 1 and report_['counts']['unflagged_errors'] == 0


def test_correlated_wrong_structures_can_still_be_high_confidence():
    # Two sources agreeing on the same wrong regioisomer (e.g. one copied from the other) pass the
    # agreement rule, and the mass check cannot see isomers. The evaluator must still count it.
    from rdkit import Chem
    from rdkit.Chem.rdMolDescriptors import CalcMolFormula
    ex7 = next(c['smiles'] for c in GOLD_DOC['compounds'] if c['example'] == '7')
    moved = ex7.replace('c1cc(F)ccc1OC', 'c1ccc(F)cc1OC')  # fluorine moved to another ring position
    a_mol, b_mol = Chem.MolFromSmiles(ex7), Chem.MolFromSmiles(moved)
    assert CalcMolFormula(a_mol) == CalcMolFormula(b_mol)
    assert Chem.MolToInchiKey(a_mol) != Chem.MolToInchiKey(b_mol)
    def l2(publication, wanted):
        return level_text()(publication, wanted) + [fact('compound', '7', 'smiles', moved, 'L2')]
    result = run([('L1', level_structures({'7': moved})), ('L2', l2)])
    a = result.assessments[('compound', '7', None)]
    assert a['checks']['mass'] == 'consistent' and a['confidence'] == 'high'
    assert evaluate(GOLD_DOC, to_prediction(result))['counts']['unflagged_errors'] == 1


def test_ledger_items_are_proposed_and_idempotent_with_curated_records(tmp_path):
    store = LedgerStore(tmp_path / 'ledger.sqlite')
    store.import_ledger(build(), 'test')
    result = run([('L1', level_structures()), ('L2', level_text(with_names=True))])
    mapped = {'ALK_WT_Ki': f'{PUB}:ALK_WT_Ki', 'ALK_L1196M_Ki': f'{PUB}:ALK_L1196M_Ki'}
    items, refused = to_ledger_items(result, PUB, mapped)
    assert {r['reason'] for r in refused} == {'实验未对应到台账中的实验定义'}
    out = store.propose_batch(items, 'extractor-test', note='cascade test')
    assert [k for k, _ in out['already_present']] == ['compounds'] * 3  # curated examples keep their records
    added = [i for k, i in out['added'] if k == 'observations']
    assert len(added) == 6
    stored = next(o for o in store.load().observations if o.id == f'{PUB}:extract:1:ALK_WT_Ki')
    assert stored.review.record_status == 'proposed' and 'extraction_confidence:medium' in stored.review.gaps
    assert stored.raw['extraction']['value'][0]['level'] == 'L2'


def test_report_names_levels_conflicts_and_review_items():
    wrong = {'6': next(c['smiles'] for c in GOLD_DOC['compounds'] if c['example'] == '6').replace('(F)', '')}
    result = run([('L1', level_structures(wrong)), ('L2', level_text()), ('L3', must_not_run)])
    text = report(result, partition(PAGES))
    assert '## 页面分区' in text and 'activity_table：PDF 第 20–21 页' in text
    assert '| L3 | 未调用' in text and 'Example 6：low（mass_mismatch）' in text
    assert '所有记录仍需独立复核' in text
