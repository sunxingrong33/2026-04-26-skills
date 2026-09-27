"""Field-level extraction metrics against a gold standard.

Besides per-field accuracy, three safety counts are reported because they are
what hurts a medicinal chemist, not the average:

* unflagged errors  -- a wrong field the extractor presented as high confidence
                       (no ``confidence`` means high: silence is not a flag);
* fabricated values -- a number where the source says not tested / blank;
* comparability misjudgments -- pairs whose B/A ratio would be computed under
  the prediction but not under the gold, or the reverse. The rule mirrors
  ``patent_evidence.compare_measurements`` within one publication: same assay,
  both exact ``=`` values, same normalised unit, positive reference value.

    python -m phase0.eval.extraction_metrics --gold phase0/data/gold --pred predictions/
"""
import argparse
import json
import math
import sys
from itertools import combinations
from pathlib import Path

from rdkit import Chem, rdBase

from phase0.sar.units import normalise
from .gold import GOLD, load, validate

REL_TOL = 1e-6


def inchikey(smiles):
    _ = rdBase.BlockLogs()
    mol = Chem.MolFromSmiles(smiles or '')
    return Chem.MolToInchiKey(mol) if mol else None


def flagged(item):
    return item.get('confidence', 'high') != 'high' or bool(item.get('flags'))


def _norm(o):
    try:
        return normalise(float(o['value']), o['unit'])
    except (TypeError, ValueError, KeyError):
        return None


def value_matches(g, p):
    if g.get('relation') != p.get('relation'):
        return False
    if g['relation'] == 'grade':
        return str(g.get('grade')).strip() == str(p.get('grade')).strip()
    a, b = _norm(g), _norm(p)
    return bool(a and b) and a[1] == b[1] and math.isclose(a[0], b[0], rel_tol=REL_TOL, abs_tol=1e-12)


def _computable(obs, x, y, assay):
    m, n = obs.get((x, assay)), obs.get((y, assay))
    if not m or not n or m.get('status') != 'measured' or n.get('status') != 'measured':
        return False
    if m.get('relation') != '=' or n.get('relation') != '=':
        return False
    a, b = _norm(m), _norm(n)
    return bool(a and b) and a[1] == b[1] and a[0] > 0


class _Rate:
    def __init__(self):
        self.hit = self.total = 0

    def add(self, ok):
        self.total += 1
        self.hit += bool(ok)

    def value(self):
        return round(self.hit / self.total, 4) if self.total else None


def evaluate(gold, pred):
    """Compare one prediction document with its gold document."""
    validate(gold)
    validate(pred)
    if gold['publication'] != pred['publication']:
        raise ValueError(f"公开号不一致：{gold['publication']} / {pred['publication']}")
    errors, missed = [], []

    def error(kind, item, example, field, g, p, assay=None):
        errors.append({'kind': kind, 'example': example, 'assay_id': assay, 'field': field,
                       'gold': g, 'pred': p, 'flagged': flagged(item)})

    g_cpd = {str(c['example']): c for c in gold.get('compounds', [])}
    p_cpd = {str(c['example']): c for c in pred.get('compounds', [])}
    rates = {k: _Rate() for k in ('compound_recall', 'compound_precision', 'structure_exact',
                                  'structure_connectivity', 'role', 'structure_page',
                                  'measurement_exact', 'missing_semantics', 'table_page')}
    for ex in g_cpd:
        rates['compound_recall'].add(ex in p_cpd)
        if ex not in p_cpd:
            missed.append({'kind': 'missed_compound', 'example': ex})
    for ex, p in p_cpd.items():
        rates['compound_precision'].add(ex in g_cpd)
        if ex not in g_cpd:
            error('spurious_compound', p, ex, 'example', None, ex)
            continue
        g = g_cpd[ex]
        gk, pk = inchikey(g.get('smiles')), inchikey(p.get('smiles'))
        full = gk is not None and gk == pk
        rates['structure_exact'].add(full)
        rates['structure_connectivity'].add(gk is not None and pk is not None and gk[:14] == pk[:14])
        if not full:
            error('structure', p, ex, 'smiles', g.get('smiles'), p.get('smiles'))
        rates['role'].add(g.get('role') == p.get('role'))
        if g.get('role') != p.get('role'):
            error('role', p, ex, 'role', g.get('role'), p.get('role'))
        if g.get('structure_pdf_page') is not None:
            ok = g['structure_pdf_page'] == p.get('structure_pdf_page')
            rates['structure_page'].add(ok)
            if not ok:
                error('locator', p, ex, 'structure_pdf_page', g['structure_pdf_page'], p.get('structure_pdf_page'))

    key = lambda o: (str(o['example']), o['assay_id'])
    g_obs = {key(o): o for o in gold.get('observations', [])}
    p_obs = {key(o): o for o in pred.get('observations', [])}
    fabricated = 0
    for k, g in g_obs.items():
        p = p_obs.get(k)
        measured = g['status'] == 'measured'
        if p is None:
            (rates['measurement_exact'] if measured else rates['missing_semantics']).add(False)
            missed.append({'kind': 'missed_measurement' if measured else 'missed_missing_record',
                           'example': k[0], 'assay_id': k[1]})
            continue
        if measured:
            ok = p['status'] == 'measured' and value_matches(g, p)
            rates['measurement_exact'].add(ok)
            if not ok:
                kind = 'dropped_qualifier' if g['relation'] not in ('=', 'grade') and p.get('relation') == '=' else 'value'
                shown = lambda o: o.get('raw') or f"{o.get('relation') or ''}{o.get('grade') or o.get('value')} {o.get('unit') or ''}".strip()
                error(kind, p, k[0], 'value', shown(g), shown(p) if p['status'] == 'measured' else p['status'], k[1])
        else:
            ok = p['status'] == g['status']
            rates['missing_semantics'].add(ok)
            if p['status'] == 'measured':
                fabricated += 1
                error('fabricated_value', p, k[0], 'status', g['status'], p.get('raw') or p.get('value') or p.get('grade'), k[1])
            elif not ok:
                error('missing_semantics', p, k[0], 'status', g['status'], p['status'], k[1])
        if g.get('table_pdf_page') is not None:
            ok = g['table_pdf_page'] == p.get('table_pdf_page')
            rates['table_page'].add(ok)
            if not ok:
                error('locator', p, k[0], 'table_pdf_page', g['table_pdf_page'], p.get('table_pdf_page'), k[1])
    for k, p in p_obs.items():
        if k not in g_obs:
            error('spurious_observation', p, k[0], 'observation', None, p.get('raw') or p.get('value'), k[1])
            if p['status'] == 'measured':
                fabricated += 1

    comparability = []
    examples = sorted({k[0] for k in g_obs} | set(g_cpd))
    for assay in sorted({k[1] for k in g_obs}):
        for x, y in combinations(examples, 2):
            g_ok, p_ok = _computable(g_obs, x, y, assay), _computable(p_obs, x, y, assay)
            if g_ok != p_ok:
                comparability.append({'kind': 'false_comparable' if p_ok else 'missed_comparable',
                                      'pair': [x, y], 'assay_id': assay})

    unflagged = [e for e in errors if not e['flagged']]
    return {
        'publication': gold['publication'],
        'gold_status': gold.get('gold_status'),
        'rates': {k: {'value': r.value(), 'hit': r.hit, 'total': r.total} for k, r in rates.items()},
        'counts': {
            'errors': len(errors),
            'unflagged_errors': len(unflagged),
            'fabricated_values': fabricated,
            'false_comparable': sum(c['kind'] == 'false_comparable' for c in comparability),
            'missed_comparable': sum(c['kind'] == 'missed_comparable' for c in comparability),
            'missed': len(missed),
        },
        'errors': errors,
        'missed': missed,
        'comparability': comparability,
    }


def aggregate(reports):
    """Pool counts across publications; rates are recomputed from totals."""
    rates = {}
    for r in reports:
        for k, v in r['rates'].items():
            t = rates.setdefault(k, {'hit': 0, 'total': 0})
            t['hit'] += v['hit']
            t['total'] += v['total']
    for t in rates.values():
        t['value'] = round(t['hit'] / t['total'], 4) if t['total'] else None
    counts = {}
    for r in reports:
        for k, v in r['counts'].items():
            counts[k] = counts.get(k, 0) + v
    return {'publications': [r['publication'] for r in reports], 'rates': rates, 'counts': counts,
            'provisional_gold': sorted({r['gold_status'] for r in reports if r['gold_status'] != 'independent_review_complete'})}


LABELS = {
    'compound_recall': '化合物召回率', 'compound_precision': '化合物精确率',
    'structure_exact': '结构完全一致（InChIKey）', 'structure_connectivity': '结构连接一致（忽略立体）',
    'role': '化合物角色', 'structure_page': '结构页码',
    'measurement_exact': '数值 + 限定符 + 单位完全正确', 'missing_semantics': '缺失语义正确', 'table_page': '活性表页码',
}
COUNT_LABELS = {
    'unflagged_errors': '未标记错误数', 'fabricated_values': '编造数值数',
    'false_comparable': '可比性误判（错误地可算倍数）', 'missed_comparable': '可比性漏判（应可算未算）',
    'errors': '错误总数（含已标记）', 'missed': '漏抽项',
}


def render(summary):
    lines = ['抽取评测：' + '、'.join(summary['publications'])]
    if summary.get('provisional_gold'):
        lines.append('注意：金标准尚未完成独立复核（' + '、'.join(summary['provisional_gold']) + '），结果仅供参考。')
    lines.append('')
    for k, label in COUNT_LABELS.items():
        lines.append(f"  {label}: {summary['counts'].get(k, 0)}")
    lines.append('')
    for k, label in LABELS.items():
        r = summary['rates'].get(k)
        if r and r['total']:
            lines.append(f"  {label}: {r['value']:.2%}（{r['hit']}/{r['total']}）")
    return '\n'.join(lines)


def _pairs(gold, pred):
    gold, pred = Path(gold), Path(pred)
    if gold.is_file():
        return [(gold, pred)]
    return [(g, pred / g.name) for g in sorted(gold.glob('*.json'))]


def main(argv=None):
    ap = argparse.ArgumentParser(description='按字段评测抽取结果')
    ap.add_argument('--gold', default=str(GOLD), help='金标准文件或目录')
    ap.add_argument('--pred', required=True, help='预测文件或目录（目录中按公开号同名匹配）')
    ap.add_argument('--json', help='另存完整 JSON 报告')
    ap.add_argument('--max-unflagged', type=int, help='未标记错误数超过该值时返回非零')
    args = ap.parse_args(argv)
    reports = []
    for g, p in _pairs(args.gold, args.pred):
        gold = load(g)
        pred = load(p) if p.exists() else {'schema_version': gold['schema_version'],
                                           'publication': gold['publication'],
                                           'compounds': [], 'observations': []}
        reports.append(evaluate(gold, pred))
    if not reports:
        print('没有找到金标准文件')
        return 2
    summary = aggregate(reports)
    print(render(summary))
    if args.json:
        Path(args.json).write_text(json.dumps({'summary': summary, 'reports': reports},
                                              ensure_ascii=False, indent=1), encoding='utf8')
    if args.max_unflagged is not None and summary['counts']['unflagged_errors'] > args.max_unflagged:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
