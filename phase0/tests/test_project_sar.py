"""Goal-driven multi-property SAR (I2.7 part 1): pairs, mapping, per-assay rules, grades."""
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from rdkit import Chem

from phase0.ledger.migrate import build
from phase0.sar import project_sar as ps
from phase0.sar.mmp import H, find_pairs, fragments

LEDGER = build()
LABEL = {c.id: c.label.split(' ·')[0] for c in LEDGER.compounds}
N_METHYL = 'C[*:1]>>[H][*:1]'


def goal(template='cell_potency_efflux', focus='ALK', **override):
    s = ps.suggest(LEDGER, template, focus=focus)
    props = [{'id': p['id'], 'label': p['label'], 'direction': p['direction'],
              'assay_ids': [h['assay_id'] for h in p['suggested']], 'threshold': p['threshold']}
             for p in s['properties'] if p['suggested']]
    for p in props:
        p.update(override.get(p['id'], {}))
    return {'label': s['label'], 'properties': props}


def pair_row(result, a, b):
    for t in result['transforms']:
        for r in t['pairs']:
            if (LABEL[r['a']], LABEL[r['b']]) == (a, b):
                return t, r
    raise AssertionError(f'{a} -> {b} not found')


# --- matched pairs -----------------------------------------------------------

def test_pairs_include_hydrogen_replacements_found_by_rdkit():
    pairs = find_pairs(LEDGER.compounds)
    named = {(LABEL[p['a']], LABEL[p['b']], f"{p['from']}>>{p['to']}") for p in pairs}
    assert ('6f', '6e', N_METHYL) in named                      # amide N-methyl removed
    assert ('Example 7', 'Example 6', N_METHYL) in named        # pyrazole N-methyl removed (early patent)
    for p in pairs:  # the two structures really differ only by the stated parts
        a = next(c for c in LEDGER.compounds if c.id == p['a'])
        b = next(c for c in LEDGER.compounds if c.id == p['b'])
        assert Chem.MolToInchiKey(Chem.MolFromSmiles(a.smiles)) != Chem.MolToInchiKey(Chem.MolFromSmiles(b.smiles))
        assert (p['key'], p['from']) in fragments(a.smiles) and (p['key'], p['to']) in fragments(b.smiles)


def test_variable_part_limit_and_identical_structures():
    same = SimpleNamespace(id='x', smiles='c1ccccc1F'), SimpleNamespace(id='y', smiles='Fc1ccccc1')
    assert find_pairs(same) == []  # same molecule written twice is not a pair
    fh = SimpleNamespace(id='f', smiles='CCOc1ccc(F)cc1'), SimpleNamespace(id='h', smiles='CCOc1ccccc1')
    assert [(p['from'], p['to']) for p in find_pairs(fh)] == [('F[*:1]', H)]
    big = SimpleNamespace(id='big', smiles='c1ccccc1CCCCCCCCCCCC'), SimpleNamespace(id='small', smiles='c1ccccc1C')
    assert find_pairs(big, max_change=3) == []


# --- goal mapping ------------------------------------------------------------

def test_mapping_suggestions_separate_cell_enzyme_efflux_and_skip_ratios():
    s = ps.suggest(LEDGER, 'cell_potency_efflux', focus='ALK')
    by = {p['id']: {h['assay_id'] for h in p['suggested']} for p in s['properties']}
    assert {'CHEMBL3286195:CHEMBL3293163', 'CHEMBL3286195:CHEMBL3293164'} <= by['cell_potency']
    assert by['efflux'] == {'CHEMBL3286195:CHEMBL3293391', 'CHEMBL3286195:CHEMBL3293392'}
    assert {'CHEMBL3286195:CHEMBL3293161', 'CHEMBL3286195:CHEMBL3293162'} <= by['enzyme_potency']
    assert not by['cell_potency'] & by['enzyme_potency']
    endpoints = {a.id: a.endpoint for a in LEDGER.assays}
    assert not any('ratio' in endpoints[a].lower() for a in by['cell_potency'] | by['enzyme_potency'])
    assert 'CHEMBL3286195:CHEMBL3293393' not in by['enzyme_potency']  # TRKB, an off-target
    assert all(p['threshold']['source'] == ps.PENDING for p in s['properties'])
    assert 'CHEMBL3286195:CHEMBL3293393' in {u['assay_id'] for u in s['unmapped']}


@pytest.mark.parametrize('patch, message', [
    ({'direction': 'up'}, '方向'),
    ({'assay_ids': ['nope']}, '实验'),
    ({'threshold': {'kind': 'fold', 'value': 1.0}}, '倍数阈值'),
    ({'threshold': {'kind': 'delta', 'value': 0}}, '差值阈值'),
])
def test_goal_is_validated(patch, message):
    g = goal()
    g['properties'][0].update(patch)
    with pytest.raises(ValueError, match=message):
        ps.check_goal(g, LEDGER)


# --- the acceptance case: 6f -> 6e -------------------------------------------

def test_6f_to_6e_enzyme_favorable_efflux_unfavorable_cell_missing():
    r = ps.analyse(LEDGER, goal())
    t, row = pair_row(r, '6f', '6e')
    p = row['properties']
    assert t['transform'] == N_METHYL
    assert p['enzyme_potency']['result'] == 'favorable'
    assert p['enzyme_potency']['assays']['CHEMBL3286195:CHEMBL3293161']['ratio_b_over_a'] == 0.1273
    assert p['efflux']['result'] == 'unfavorable'
    assert p['efflux']['assays']['CHEMBL3286195:CHEMBL3293391']['ratio_b_over_a'] == 2.2368
    assert p['logd']['result'] == 'unchanged'
    assert p['cell_potency']['result'] == 'missing'
    gap = next(g for g in t['gaps'] if g['a'] == row['a'] and g['property_id'] == 'cell_potency')
    assert gap['lacking'] == ['A'] and 'CHEMBL3286195:CHEMBL3293163' in gap['assays']
    assert '默认阈值' in r['notice'] and r['pending_defaults']


def test_threshold_decides_unchanged_and_user_value_is_recorded():
    # efflux B/A = 2.24: past a 2-fold threshold, inside a 3-fold one
    r = ps.analyse(LEDGER, goal(efflux={'threshold': {'kind': 'fold', 'value': 3.0, 'source': 'user'}}))
    _, row = pair_row(r, '6f', '6e')
    assert row['properties']['efflux']['result'] == 'unchanged'
    assert '外排比' not in r['pending_defaults']


def test_other_documents_assays_are_not_counted_as_missing():
    r = ps.analyse(LEDGER, goal())
    _, row = pair_row(r, 'Example 7', 'Example 6')
    assert row['properties']['efflux']['result'] == 'no_assay'
    assert set(row['properties']['enzyme_potency']['assays']) <= {a.id for a in LEDGER.assays
                                                                   if a.document_id == 'WO2011138751A2'}
    cell = row['properties']['cell_potency']
    assert cell['result'] == 'missing' and all('not_tested' in x['note'] for x in cell['assays'].values())


def obs(value, relation='=', unit='nM', status='measured'):
    return SimpleNamespace(id=f'o{value}', status=status, relation=relation, value=value, unit=unit)


PROP = {'direction': 'lower', 'threshold': {'kind': 'fold', 'value': 2.0}}


@pytest.mark.parametrize('a, b, status, note', [
    ([obs(10, '<')], [obs(5)], 'not_comparable', '限定值'),
    ([obs(10), obs(12)], [obs(5)], 'not_comparable', '重复观测'),
    ([obs(10)], [obs(5, unit=None)], 'not_comparable', '单位'),
    ([], [obs(5)], 'missing', 'A在'),
    ([obs(None, relation=None, unit=None, status='not_tested')], [obs(5)], 'missing', 'not_tested'),
])
def test_values_that_are_never_turned_into_numbers(a, b, status, note):
    r = ps.compare(a, b, PROP)
    assert r['status'] == status and note in r['note'] and 'ratio_b_over_a' not in r


def test_units_are_normalised_and_direction_respected():
    r = ps.compare([obs(1, unit='uM')], [obs(100)], PROP)
    assert r['ratio_b_over_a'] == 0.1 and r['outcome'] == 'favorable'
    r = ps.compare([obs(1, unit='uM')], [obs(100)], {**PROP, 'direction': 'higher'})
    assert r['outcome'] == 'unfavorable'


def test_grades_follow_the_written_rules():
    def rows(*cells):
        return [{'document': d, 'properties': {'p': {'result': res}}} for d, res in cells]
    assert ps.grade(rows(('D1', 'missing')), 'p') == 'none'
    assert ps.grade(rows(('D1', 'favorable'), ('D1', 'unfavorable')), 'p') == 'conflicting'
    assert ps.grade(rows(('D1', 'favorable'), ('D2', 'unchanged')), 'p') == 'inconsistent'
    assert ps.grade(rows(('D1', 'favorable'), ('D2', 'favorable')), 'p') == 'strong'
    assert ps.grade(rows(('D1', 'favorable'), ('D1', 'favorable')), 'p') == 'moderate'
    assert ps.grade(rows(('D1', 'favorable')), 'p') == 'weak'
    assert {g['grade'] for g in ps.analyse(LEDGER, goal())['grade_rules']} == {k for k, _, _ in ps.GRADE_RULES}


def test_no_composite_score_anywhere():
    text = json.dumps(ps.analyse(LEDGER, goal()), ensure_ascii=False)
    assert 'score' not in text and '综合分' not in text.replace('不合成综合分数', '')


# --- API ---------------------------------------------------------------------

def test_http_api_modes_and_validation(tmp_path):
    from phase0.sar.serve import create_server
    server = create_server(0, tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/api/project-sar'

    def post(body):
        return json.load(urlopen(Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})))
    try:
        assert 'cell_potency_efflux' in {t['id'] for t in post({'mode': 'templates'})['templates']}
        s = post({'mode': 'suggest', 'template': 'cell_potency_efflux', 'focus': 'ALK'})
        assert s['properties'][1]['id'] == 'efflux'
        r = post({'mode': 'analyse', 'goal': goal(), 'documents': ['CHEMBL3286195'], 'max_change': 12})
        assert r['scope']['documents'] == ['CHEMBL3286195'] and r['pair_count'] > 0
        for bad in ({'mode': 'analyse', 'goal': goal(), 'max_change': 99},
                    {'mode': 'suggest', 'template': 'nope'},
                    {'mode': 'suggest', 'template': 'cell_potency_efflux', 'documents': ['WO000']},
                    {'mode': 'other'}):
            with pytest.raises(HTTPError) as exc:
                post(bad)
            assert exc.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# --- custom properties, target range, sites ----------------------------------

def test_custom_property_from_any_assay():
    s = ps.suggest(LEDGER, 'cell_potency_efflux', focus='ALK')
    assert 'CHEMBL3286195:CHEMBL3293390' in {a['assay_id'] for a in s['assays']}  # HLM clearance is offered
    g = goal()
    g['properties'].append({'id': 'custom_1', 'label': '微粒体清除率', 'direction': 'lower',
                            'assay_ids': ['CHEMBL3286195:CHEMBL3293390'],
                            'threshold': {'kind': 'fold', 'value': 2.0, 'source': 'user'}})
    _, row = pair_row(ps.analyse(LEDGER, g), '6f', '6e')
    cl = row['properties']['custom_1']
    assert cl['result'] == 'favorable'  # 58 -> 28 mL/min/kg, just past 2-fold
    assert cl['assays']['CHEMBL3286195:CHEMBL3293390']['ratio_b_over_a'] == 0.4828


def test_target_range_moving_away_is_unfavorable_inside_is_unchanged():
    g = goal(efflux={'direction': 'range', 'range': {'low': None, 'high': 2.5, 'unit': None}},
             logd={'direction': 'range', 'range': {'low': 1, 'high': 3, 'unit': None}})
    _, row = pair_row(ps.analyse(LEDGER, g), '6f', '6e')
    efflux = row['properties']['efflux']['assays']['CHEMBL3286195:CHEMBL3293391']
    assert efflux['outcome'] == 'unfavorable' and not efflux['a_in_range'] and not efflux['b_in_range']
    logd = row['properties']['logd']['assays']['CHEMBL3286195:CHEMBL3293165']
    assert logd['outcome'] == 'unchanged' and logd['a_in_range'] and logd['b_in_range']


@pytest.mark.parametrize('a, b, rng, outcome', [
    (obs(10), obs(3), {'low': None, 'high': 5, 'unit': 'nM'}, 'favorable'),       # moves into the range
    (obs(10), obs(3), {'low': None, 'high': 0.005, 'unit': 'uM'}, 'favorable'),   # same bound in uM
    (obs(3), obs(1), {'low': 2, 'high': 5, 'unit': 'nM'}, 'unfavorable'),         # leaves the range downwards
    (obs(3), obs(4), {'low': 2, 'high': 5, 'unit': 'nM'}, 'unchanged'),           # within noise, both inside
])
def test_range_outcomes(a, b, rng, outcome):
    prop = {'direction': 'range', 'range': rng, 'threshold': {'kind': 'fold', 'value': 2.0}}
    assert ps.compare([a], [b], prop)['outcome'] == outcome


def test_range_unit_must_match_the_measurement():
    prop = {'direction': 'range', 'range': {'low': None, 'high': 5, 'unit': None},
            'threshold': {'kind': 'fold', 'value': 2.0}}
    r = ps.compare([obs(10)], [obs(3)], prop)
    assert r['status'] == 'not_comparable' and '单位' in r['note']


@pytest.mark.parametrize('rng, message', [
    (None, '区间'), ({'low': None, 'high': None}, '至少需要一个边界'), ({'low': 5, 'high': 1}, '下限'),
    ({'low': 0, 'high': 1}, '正数'), ({'low': 'x', 'high': 1}, '数值'),
])
def test_range_is_validated(rng, message):
    g = goal(efflux={'direction': 'range', 'range': rng})
    with pytest.raises(ValueError, match=message):
        ps.check_goal(g, LEDGER)


def test_sites_group_the_same_position_across_documents():
    r = ps.analyse(LEDGER, goal())
    sites = {s['site']: s for s in r['sites']}
    assert sites['cC(=O)N(C)[*:1]']['transforms'] == [N_METHYL]  # the amide N of 6f / 6e
    aryl = sites['ccc([*:1])cn']  # 5-position of the 2-aminopyridine
    named = {(LABEL[p['a']], LABEL[p['b']]) for p in aryl['pairs']}
    assert {('Example 1', 'Example 7'), ('6a', '6d')} <= named
    assert set(aryl['documents']) == {'CHEMBL3286195', 'WO2011138751A2'}
    assert sum(len(s['pairs']) for s in r['sites']) == r['pair_count']


# --- part 2: coverage, follow-ups, categories, report, tools ------------------

def test_followups_rank_6f_cell_potency_first_with_reasons():
    f = ps.analyse(LEDGER, goal())['followups']
    top = f['items'][0]
    assert LABEL[top['compound_id']] == '6f' and top['property_id'] == 'cell_potency'
    assert {p['pair'] for p in top['pairs']} == {'6f → 6d', '6f → 6e'} and top['promising'] == 2
    assert top['reason'].startswith('补测后可判断 2 个分子对的细胞活性')
    keys = [(-len(i['pairs']), -i['promising'], -i['assay_measured']) for i in f['items']]
    assert keys == sorted(keys) and '排序规则' in f['rule']
    assert all(i['property_id'] != 'logd' for i in f['items'])  # reference-only properties never ask for data


def test_categories_follow_goal_properties_only():
    r = ps.analyse(LEDGER, goal())
    cats = {t['transform']: t['category'] for t in r['transforms']}
    assert cats[N_METHYL]['key'] == 'tradeoff'
    assert cats[N_METHYL]['favorable'] == ['酶活性'] and cats[N_METHYL]['unfavorable'] == ['外排比']
    assert {c['key'] for c in cats.values()} <= set(ps.CATEGORY)
    for t in r['transforms']:
        assert t['synthesis_items'][-1] == '以上仅为待评估项，不是合成可行性判断'
    small = next(t for t in r['transforms'] if t['transform'] == 'CO[*:1]>>C[*:1]')
    assert not any('含有该片段' in x for x in small['synthesis_items'])  # a methyl is everywhere


def test_coverage_counts_measured_compounds_per_property():
    cov = {c['property_id']: c for c in ps.analyse(LEDGER, goal())['coverage']}
    assert cov['efflux']['documents'] == ['CHEMBL3286195']
    assert 0 < cov['efflux']['compounds_measured'] < cov['efflux']['compounds_total'] == 37


def test_report_carries_sources_for_every_number():
    md = ps.handle({'mode': 'report', 'goal': goal()}, LEDGER)['markdown']
    for heading in ('## 1. 目标与实验映射', '## 2. 范围与覆盖', '## 3. 候选方向', '## 4. 取舍', '## 7. 补测建议', '## 8. 规则'):
        assert heading in md
    line = next(x for x in md.splitlines() if '外排比：不利' in x and '7.6 → 17' in x)
    assert '观测 ' in line and 'CHEMBL3293391' in line
    assert '默认值，待化学家确认' in md and '不合成综合分数' in md


def test_project_tools_and_replay(tmp_path):
    from phase0.tools import chem_tools  # noqa: F401
    from phase0.tools.core import Run, replay
    run = Run(runs_dir=tmp_path / 'runs', cache_dir=tmp_path / 'cache')
    s = run.call('project_goal_suggest', {'template': 'cell_potency_efflux', 'focus': 'ALK'})
    assert s['summary'].startswith('改善细胞活性，同时控制外排：')
    r = run.call('project_sar_analyse', {'goal': goal(), 'include_report': True})
    assert '18 个分子对' in r['summary'] and '# SAR 讨论材料' in r['data']['markdown']
    assert r['preview'][0]['property_id'] == 'cell_potency'
    assert replay(run.dir)['faithful']
