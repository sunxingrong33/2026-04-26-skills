"""Goal-driven multi-property SAR over matched pairs (iteration I2.7, part 1).

A *goal* is a set of properties. Each property names the ledger assays that
measure it (suggested by rules, confirmed by the user; custom properties are
built from any in-scope assay), a desired direction (``lower`` / ``higher``,
``range`` with a target interval such as efflux ratio <= 2.5, or ``none`` for
reference-only properties) and a noise threshold. Thresholds have defaults per endpoint type; every default is
reported as *pending chemist review* until the user sets it.

For each matched pair and each property the measurements are compared assay
by assay with the existing rules: same assay, one exact value each, same
unit. Qualified values, duplicates and missing records are never turned into
numbers. A change inside the threshold is "unchanged", not a win or a loss.
Nothing here combines properties into a score: they stay side by side.

Evidence grades follow fixed, documented rules (``GRADE_RULES``), not model
judgement. The output is discussion material, not a conclusion.
"""
import math
from collections import defaultdict

from .mmp import MAX_CHANGE, find_pairs, site, transform
from .units import normalise

PENDING = 'default_pending_chemist_review'
DIRECTIONS = ('lower', 'higher', 'range', 'none')
SITE_RADIUS = 3  # bonds from the attachment point that define a site
DELTA_ENDPOINTS = ('logd', 'logp', 'pka')  # already logarithmic: compare differences, not ratios
DEFAULT_FOLD = 2.0
DEFAULT_DELTA = 0.5

TEMPLATES = {
    'cell_potency_efflux': {
        'label': '改善细胞活性，同时控制外排',
        'properties': [
            {'id': 'cell_potency', 'label': '细胞活性', 'direction': 'lower',
             'endpoints': ['ic50', 'ec50', 'gi50'], 'any': ['cell', '细胞']},
            {'id': 'efflux', 'label': '外排比', 'direction': 'lower', 'endpoints': ['ratio'], 'any': ['efflux', '外排']},
            {'id': 'enzyme_potency', 'label': '酶活性（参考）', 'direction': 'lower',
             'endpoints': ['ki', 'kd', 'ic50'], 'none': ['cell', '细胞']},
            {'id': 'logd', 'label': 'LogD（仅参考）', 'direction': 'none', 'endpoints': ['logd']},
        ],
    },
    'potency_metabolic_stability': {
        'label': '保持活性，同时改善代谢稳定性',
        'properties': [
            {'id': 'cell_potency', 'label': '细胞活性', 'direction': 'lower',
             'endpoints': ['ic50', 'ec50', 'gi50'], 'any': ['cell', '细胞']},
            {'id': 'clearance', 'label': '微粒体清除率', 'direction': 'lower', 'endpoints': ['cl'],
             'any': ['microsom', '微粒体']},
            {'id': 'enzyme_potency', 'label': '酶活性（参考）', 'direction': 'lower',
             'endpoints': ['ki', 'kd', 'ic50'], 'none': ['cell', '细胞']},
        ],
    },
}

GRADE_RULES = [
    ('none', '无可比数据', '没有任何分子对在该性质上有可比较的测量'),
    ('conflicting', '结论矛盾', '同一替换中既有有利也有不利的分子对'),
    ('inconsistent', '不一致', '部分分子对有变化、部分在阈值内未变'),
    ('strong', '较强', '所有可比分子对结果相同，且来自至少 2 个独立文档'),
    ('moderate', '中等', '所有可比分子对结果相同，来自单一文档中的至少 2 个分子对'),
    ('weak', '较弱', '只有 1 个可比较的分子对'),
]


def _text(assay):
    return f'{assay.endpoint} {assay.protocol or ""}'.lower()


def _endpoint_match(assay, endpoints):
    """Exact endpoint, or a descriptive label ending in it; ratios of potencies are not potencies."""
    e = assay.endpoint.lower().strip()
    words = e.replace('/', ' ').split()
    if e in endpoints:
        return True
    return bool(words) and words[-1] in endpoints and 'ratio' not in words


def default_threshold(endpoint):
    e = endpoint.lower()
    if any(k in e for k in DELTA_ENDPOINTS):
        return {'kind': 'delta', 'value': DEFAULT_DELTA, 'source': PENDING}
    return {'kind': 'fold', 'value': DEFAULT_FOLD, 'source': PENDING}


def suggest(ledger, template_id, documents=None, focus=None):
    """Suggested assay mapping for a template, plus the in-scope assays no property claimed.

    ``focus`` (e.g. a target name such as ``ALK``) keeps potency suggestions to assays whose
    description mentions it, so off-target selectivity assays are not offered as potency.
    """
    focus = (focus or '').strip().lower() or None
    if template_id not in TEMPLATES:
        raise ValueError(f'未知的目标模板：{template_id}')
    template = TEMPLATES[template_id]
    measured = defaultdict(int)
    for o in ledger.observations:
        if o.status == 'measured':
            measured[o.assay_id] += 1
    assays = [a for a in ledger.assays if a.review.record_status != 'rejected'
              and (not documents or a.document_id in documents) and measured[a.id]]
    claimed, properties = set(), []
    for spec in template['properties']:
        hits = []
        for a in assays:
            text = _text(a)
            if not _endpoint_match(a, spec['endpoints']):
                continue
            if spec.get('any') and not any(k in text for k in spec['any']):
                continue
            if spec.get('none') and any(k in text for k in spec['none']):
                continue
            if focus and spec.get('focus', True) and spec['direction'] != 'none' and spec['id'] != 'efflux' \
                    and focus not in text:
                continue
            hits.append({'assay_id': a.id, 'document_id': a.document_id, 'endpoint': a.endpoint,
                         'description': a.protocol, 'measured': measured[a.id],
                         'reason': f"终点 {a.endpoint}" + (f"；描述含 {'/'.join(spec['any'])}" if spec.get('any') else '')})
            claimed.add(a.id)
        hits.sort(key=lambda h: (-h['measured'], h['assay_id']))
        properties.append({'id': spec['id'], 'label': spec['label'], 'direction': spec['direction'],
                           'suggested': hits,
                           'threshold': default_threshold(hits[0]['endpoint'] if hits else spec['endpoints'][0])})
    unmapped = [{'assay_id': a.id, 'endpoint': a.endpoint, 'description': a.protocol, 'measured': measured[a.id]}
                for a in assays if a.id not in claimed]
    catalogue = [{'assay_id': a.id, 'document_id': a.document_id, 'endpoint': a.endpoint, 'description': a.protocol,
                  'unit': a.unit, 'measured': measured[a.id]} for a in assays]
    return {'template': template_id, 'label': template['label'], 'focus': focus, 'properties': properties,
            'unmapped': unmapped, 'assays': catalogue,
            'notice': '映射由规则按终点类型和实验描述建议，必须由用户确认；阈值为默认值，待化学家确认。'}


def check_goal(goal, ledger):
    """Validate a confirmed goal; returns a normalised copy."""
    assays = {a.id: a for a in ledger.assays}
    props = goal.get('properties') if isinstance(goal, dict) else None
    if not isinstance(props, list) or not props:
        raise ValueError('目标至少需要一个性质。')
    out, seen = [], set()
    for p in props:
        pid, label = p.get('id'), p.get('label') or p.get('id')
        if not isinstance(pid, str) or not pid or pid in seen:
            raise ValueError('每个性质需要唯一的 id。')
        seen.add(pid)
        if p.get('direction') not in DIRECTIONS:
            raise ValueError(f'{label}：方向必须是 lower、higher、range 或 none。')
        if not isinstance(label, str) or len(label) > 40:
            raise ValueError('性质名称不超过 40 个字符。')
        ids = p.get('assay_ids')
        if not isinstance(ids, list) or not ids or any(i not in assays for i in ids):
            raise ValueError(f'{label}：请确认至少一个台账中存在的实验。')
        t = p.get('threshold') or default_threshold(assays[ids[0]].endpoint)
        if t.get('kind') not in ('fold', 'delta') or not isinstance(t.get('value'), (int, float)):
            raise ValueError(f'{label}：阈值格式无效。')
        if (t['kind'] == 'fold' and not t['value'] > 1) or (t['kind'] == 'delta' and not t['value'] > 0):
            raise ValueError(f'{label}：倍数阈值须大于 1，差值阈值须大于 0。')
        item = {'id': pid, 'label': label, 'direction': p['direction'], 'assay_ids': list(dict.fromkeys(ids)),
                'threshold': {'kind': t['kind'], 'value': float(t['value']), 'source': t.get('source', 'user')}}
        if p['direction'] == 'range':
            item['range'] = _check_range(p.get('range'), label, t['kind'])
        out.append(item)
    return {'label': goal.get('label') or '自定义目标', 'properties': out}


def _check_range(r, label, kind):
    """Target interval {low, high, unit}; either bound may be open. Units must be convertible."""
    if not isinstance(r, dict):
        raise ValueError(f'{label}：目标区间需要下限和 / 或上限。')
    low, high, unit = r.get('low'), r.get('high'), r.get('unit') or None
    for v in (low, high):
        if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v)):
            raise ValueError(f'{label}：区间边界必须是数值。')
    if low is None and high is None:
        raise ValueError(f'{label}：目标区间至少需要一个边界。')
    if low is not None and high is not None and low > high:
        raise ValueError(f'{label}：区间下限不能大于上限。')
    if kind == 'fold' and any(v is not None and v <= 0 for v in (low, high)):
        raise ValueError(f'{label}：按倍数比较的性质，区间边界须为正数。')
    if unit is not None and (not isinstance(unit, str) or len(unit) > 20):
        raise ValueError(f'{label}：单位无效。')
    return {'low': low, 'high': high, 'unit': unit}


def _distance(value, unit, r, kind):
    """How far a value lies outside the target interval (0 inside); log scale for fold-type properties."""
    lo, hi = (normalise(v, r['unit'])[0] if (v is not None and r['unit']) else v for v in (r['low'], r['high']))
    if lo is not None and value < lo:
        gap = (math.log10(lo) - math.log10(value)) if kind == 'fold' else lo - value
    elif hi is not None and value > hi:
        gap = (math.log10(value) - math.log10(hi)) if kind == 'fold' else value - hi
    else:
        gap = 0.0
    return gap


def _values(obs):
    return [o for o in obs if o.status == 'measured']


def compare(a_obs, b_obs, prop):
    """Outcome of one assay for one pair: comparable with fold/delta and class, or why not."""
    if not a_obs or not b_obs:
        who = 'A、B 均' if not a_obs and not b_obs else ('A' if not a_obs else 'B')
        return {'status': 'missing', 'lacking': who, 'note': f'{who}在该实验中没有记录'}
    a, b = _values(a_obs), _values(b_obs)
    if not a or not b:
        lacking = 'A、B 均' if not a and not b else ('A' if not a else 'B')
        states = sorted({o.status for o in (a_obs if not a else b_obs)})
        return {'status': 'missing', 'lacking': lacking, 'note': f'{lacking}未测得数值（{"、".join(states)}）'}
    if len(a) > 1 or len(b) > 1:
        return {'status': 'not_comparable', 'note': '存在重复观测，未自动取平均'}
    a, b = a[0], b[0]
    if a.relation != '=' or b.relation != '=':
        return {'status': 'not_comparable', 'note': '含限定值（如 <、>），不计算变化'}
    if a.value is None or b.value is None:
        return {'status': 'not_comparable', 'note': '数值缺失'}
    if (a.unit is None) != (b.unit is None):
        return {'status': 'not_comparable', 'note': f'单位不一致（{a.unit} / {b.unit}）'}
    x, u = normalise(a.value, a.unit) if a.unit else (a.value, None)
    y, v = normalise(b.value, b.unit) if b.unit else (b.value, None)
    if u != v:
        return {'status': 'not_comparable', 'note': f'单位不一致（{a.unit} / {b.unit}）'}
    t = prop['threshold']
    base = {'a': {'id': a.id, 'value': a.value, 'unit': a.unit}, 'b': {'id': b.id, 'value': b.value, 'unit': b.unit}}
    if t['kind'] == 'fold':
        if x <= 0 or y <= 0:
            return {'status': 'not_comparable', 'note': '非正数值，不计算倍数', **base}
        change = y / x
        moved = abs(math.log10(change)) >= math.log10(t['value'])
        down = change < 1
        base['ratio_b_over_a'] = round(change, 4)
    else:
        change = y - x
        moved = abs(change) >= t['value']
        down = change < 0
        base['delta_b_minus_a'] = round(change, 4)
    if prop['direction'] == 'range':
        r = prop['range']
        range_unit = normalise(1.0, r['unit'])[1] if r['unit'] else None
        if range_unit != u:
            return {'status': 'not_comparable', 'note': f"目标区间单位（{r['unit'] or '无'}）与测量单位（{a.unit or '无'}）不一致", **base}
        da, db = _distance(x, u, r, t['kind']), _distance(y, u, r, t['kind'])
        base.update(a_in_range=da == 0, b_in_range=db == 0)
        if not moved or da == db:
            outcome = 'unchanged'
        else:
            outcome = 'favorable' if db < da else 'unfavorable'
    elif not moved:
        outcome = 'unchanged'
    elif prop['direction'] == 'none':
        outcome = 'changed'
    else:
        outcome = 'favorable' if down == (prop['direction'] == 'lower') else 'unfavorable'
    return {'status': 'comparable', 'outcome': outcome, **base}


def _merge(per_assay):
    outcomes = {r['outcome'] for r in per_assay.values() if r['status'] == 'comparable'}
    if not outcomes:
        return 'not_comparable' if any(r['status'] == 'not_comparable' for r in per_assay.values()) else 'missing'
    if len(outcomes) == 1:
        return outcomes.pop()
    return 'mixed'


def grade(pair_results, prop_id):
    """Evidence grade for one property over the pairs of one transformation (see GRADE_RULES)."""
    comparable = [r for r in pair_results
                  if r['properties'][prop_id]['result'] not in ('missing', 'not_comparable', 'no_assay')]
    if not comparable:
        return 'none'
    outcomes = {r['properties'][prop_id]['result'] for r in comparable}
    if {'favorable', 'unfavorable'} <= outcomes or 'mixed' in outcomes:
        return 'conflicting'
    if len(outcomes) > 1:
        return 'inconsistent'
    docs = {r['document'] for r in comparable}
    if len(docs) >= 2:
        return 'strong'
    return 'moderate' if len(comparable) >= 2 else 'weak'


def _groups(pairs, field, goal):
    """Per-group property counts, evidence grades and missing goal properties."""
    grouped = defaultdict(list)
    for r in pairs:
        grouped[r[field]].append(r)
    out = []
    for name, rows in grouped.items():
        summary = {}
        for prop in goal['properties']:
            counts = defaultdict(int)
            for r in rows:
                counts[r['properties'][prop['id']]['result']] += 1
            g = grade(rows, prop['id'])
            summary[prop['id']] = {'counts': dict(counts), 'grade': g,
                                   'grade_label': {k: v for k, v, _ in GRADE_RULES}[g]}
        gaps = []
        for r in rows:
            for prop in goal['properties']:
                cell = r['properties'][prop['id']]
                if prop['direction'] == 'none' or cell['result'] != 'missing':
                    continue
                one_side = sorted({x['lacking'] for x in cell['assays'].values() if x['lacking'] in ('A', 'B')})
                gaps.append({'pair': f"{r['a_label']} → {r['b_label']}", 'a': r['a'], 'b': r['b'],
                             'property': prop['label'], 'property_id': prop['id'],
                             'lacking': one_side or ['A、B 均'],
                             'assays': [aid for aid, x in cell['assays'].items() if x['lacking'] in ('A', 'B')]})
        out.append({'group': name, 'pairs': rows, 'documents': sorted({r['document'] for r in rows}),
                    'summary': summary, 'gaps': gaps})
    out.sort(key=lambda t: (-len(t['pairs']), t['group']))
    return out


def analyse(ledger, goal, documents=None, max_change=MAX_CHANGE):
    """Matched pairs in scope, each property compared per assay, summarised per transformation."""
    goal = check_goal(goal, ledger)
    comps = [c for c in ledger.compounds if c.review.record_status != 'rejected'
             and (not documents or c.document_id in documents)]
    names = {c.id: c.label for c in comps}
    assay_doc = {a.id: a.document_id for a in ledger.assays}
    docs = {c.id: c.document_id for c in comps}
    obs = defaultdict(list)
    for o in ledger.observations:
        if o.review.record_status != 'rejected':
            obs[(o.compound_id, o.assay_id)].append(o)
    pairs = []
    for p in find_pairs(comps, max_change):
        row = {'a': p['a'], 'b': p['b'], 'a_label': names[p['a']], 'b_label': names[p['b']],
               'document': docs[p['a']] if docs[p['a']] == docs[p['b']] else f"{docs[p['a']]} / {docs[p['b']]}",
               'transform': transform(p), 'key': p['key'], 'site': site(p['key'], SITE_RADIUS),
               'change_heavy_atoms': p['change'], 'properties': {}}
        same_doc = docs[p['a']] == docs[p['b']]
        for prop in goal['properties']:
            # Only the pair's own document can hold comparable measurements; other documents' assays are not
            # "missing" for this pair, they are simply a different experiment.
            local = [aid for aid in prop['assay_ids'] if same_doc and assay_doc.get(aid) == docs[p['a']]]
            per_assay = {aid: compare(obs[(p['a'], aid)], obs[(p['b'], aid)], prop) for aid in local}
            if not same_doc:
                result = 'not_comparable'
            elif not local:
                result = 'no_assay'
            else:
                result = _merge(per_assay)
            row['properties'][prop['id']] = {'result': result, 'assays': per_assay}
        pairs.append(row)
    transforms = _groups(pairs, 'transform', goal)
    for g in transforms:
        g['transform'] = g.pop('group')
        g['sites'] = sorted({r['key'] for r in g['pairs']})  # full constant parts
    sites = _groups(pairs, 'site', goal)
    for g in sites:
        g['site'] = g.pop('group')
        g['transforms'] = sorted({r['transform'] for r in g['pairs']})
    defaults = [p['label'] for p in goal['properties'] if p['threshold']['source'] == PENDING]
    return {'goal': goal, 'scope': {'documents': sorted(documents) if documents else '全部', 'compounds': len(comps),
                                    'max_change_heavy_atoms': max_change},
            'pair_count': len(pairs), 'transforms': transforms, 'sites': sites, 'site_radius': SITE_RADIUS,
            'grade_rules': [{'grade': k, 'label': v, 'rule': d} for k, v, d in GRADE_RULES],
            'pending_defaults': defaults,
            'notice': ('讨论材料，不是结论。分子对由 MMP 单切自动找出；各性质按实验逐项比较，限定值、重复观测和缺失不计算；'
                       '阈值内记为“未变”；不合成综合分数。' + (f"以下性质使用默认阈值，待化学家确认：{'、'.join(defaults)}。" if defaults else ''))}


def handle(request, ledger=None):
    """Request dispatcher for the page and tools: templates / suggest / analyse."""
    from phase0.ledger.access import current_ledger, usable
    if not isinstance(request, dict):
        raise ValueError('请求格式无效。')
    ledger = usable(ledger if ledger is not None else current_ledger())
    known = sorted({c.document_id for c in ledger.compounds})
    documents = request.get('documents') or None
    if documents is not None and (not isinstance(documents, list) or any(d not in known for d in documents)):
        raise ValueError('范围中包含台账里没有的文档。')
    mode = request.get('mode')
    if mode == 'templates':
        return {'templates': [{'id': k, 'label': v['label'], 'properties': [p['label'] for p in v['properties']]}
                              for k, v in TEMPLATES.items()], 'documents': known}
    if mode == 'suggest':
        focus = request.get('focus')
        if focus is not None and (not isinstance(focus, str) or len(focus) > 40):
            raise ValueError('靶点关键词不超过 40 个字符。')
        return {**suggest(ledger, request.get('template'), documents, focus), 'documents': known}
    if mode == 'analyse':
        max_change = request.get('max_change', MAX_CHANGE)
        if type(max_change) is not int or not 1 <= max_change <= 20:
            raise ValueError('可变部分的重原子数上限须为 1–20 的整数。')
        return analyse(ledger, request.get('goal'), documents, max_change)
    raise ValueError('mode 必须是 templates、suggest 或 analyse。')
