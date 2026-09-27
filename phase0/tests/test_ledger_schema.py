"""Typed ledger: lossless migration and the evidence contract enforced by the schema."""
import copy
import json
import pytest
from pydantic import ValidationError

from phase0.ledger import schema
from phase0.ledger.migrate import (build, canonical_sha256, dump, load, to_legacy, value_mismatches,
                                   verify)
from phase0.ledger.schema import Ledger, Observation
from phase0.sar.evidence_ledger import DATA, build_ledger

LEDGER = build()


def as_dict():
    return json.loads(dump(LEDGER))


def obs(d, oid):
    return next(o for o in d['observations'] if o['id'] == oid)


def test_migration_is_lossless_by_round_trip():
    result, _ = verify()
    assert result['lossless']
    assert result['legacy_sha256'] == result['roundtrip_sha256']
    assert to_legacy(load(dump(LEDGER))) == build_ledger()


def test_counts_match_the_documented_ledger():
    s = verify()[0]['summary']
    assert s['documents'] == {'paper': 2, 'patent': 2}
    assert s['compounds'] == 37 and s['observations'] == 320 and s['assays'] == 108
    assert s['observation_status'] == {'measured': 311, 'not_reported': 7, 'not_tested': 2}
    assert s['relations'] == {'=': 285, '<': 26}


def test_nothing_is_confirmed_and_unannotated_roles_are_gaps():
    assert {o.review.record_status for o in LEDGER.observations} == {'proposed'}
    paper = [c for c in LEDGER.compounds if not c.id.startswith('WO')]
    assert {c.role for c in paper} == {'unspecified'}
    assert all('compound_role' in c.review.gaps for c in paper)
    assert {c.role for c in LEDGER.compounds if c.id.startswith('WO')} == {'example'}


def test_patent_assays_with_the_same_name_stay_separate():
    same = [a for a in LEDGER.assays if a.source_assay_id == 'ALK_WT_Ki']
    assert sorted(a.id for a in same) == ['WO2011138751A2:ALK_WT_Ki', 'WO2013132376A1:ALK_WT_Ki']
    # Identical protocol text is not evidence of the same run; each patent keeps its own assay.
    assert len({a.protocol for a in same}) == 1
    assert len({a.protocol_locator for a in same}) == 2


def test_missing_semantics_and_qualifiers_survive():
    blanks = [o for o in LEDGER.observations if o.compound_id == 'WO2011138751A2:example:6' and o.status != 'measured']
    assert [o.status for o in blanks] == ['not_tested', 'not_tested']
    assert all(o.value is None and o.relation is None and o.missing_reason for o in blanks)
    late = [o for o in LEDGER.observations if o.assay_id == 'WO2013132376A1:ALK_WT_Ki']
    assert [(o.relation, o.value) for o in late] == [('<', 0.2)] * 3


def test_typed_value_drift_from_raw_is_detected():
    tampered = copy.deepcopy(LEDGER)
    tampered.observations[0].value = tampered.observations[0].value * 10
    assert value_mismatches(tampered)[0]['id'] == tampered.observations[0].id


def test_dropping_a_field_breaks_the_round_trip_hash():
    tampered = copy.deepcopy(LEDGER)
    tampered.compounds[0].label = 'renamed'
    assert canonical_sha256(to_legacy(tampered)) != canonical_sha256(build_ledger())


def test_inconsistent_assay_records_refuse_to_migrate(tmp_path):
    import csv
    import shutil
    shutil.copytree(DATA / 'curated', tmp_path / 'curated')
    shutil.copytree(DATA / 'patent_evidence', tmp_path / 'patent_evidence')
    path = tmp_path / 'curated' / 'lorlatinib' / 'observations.csv'
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert build(tmp_path).observations
    rows.append({**rows[0], 'activity_id': '99999999', 'assay_description': 'A different description'})
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match='assay .*不一致，拒绝迁移'):
        build(tmp_path)


@pytest.mark.parametrize('mutate, message', [
    (lambda o: o.update(value=1.0), '不能带数值'),
    (lambda o: o.update(relation='='), '不能带数值、等级或限定符'),
])
def test_missing_records_cannot_carry_numbers(mutate, message):
    d = as_dict()
    mutate(obs(d, 'WO2011138751A2:example:6:2'))
    with pytest.raises(ValidationError, match=message):
        Ledger.model_validate(d)


def test_measured_values_need_relation_and_finite_number():
    d = as_dict()
    o = next(x for x in d['observations'] if x['status'] == 'measured')
    for patch, message in (({'relation': None}, '缺少限定符'), ({'value': None}, '缺少数值'),
                           ({'value': float('nan')}, '有限数值')):
        bad = {**o, **patch}
        with pytest.raises(ValidationError, match=message):
            Observation.model_validate(bad)


def test_graded_values_need_an_assay_definition():
    d = as_dict()
    d['assays'].append({'id': 'WO2011138751A2:AMY3R_grade', 'document_id': 'WO2011138751A2',
                        'source_assay_id': 'AMY3R_grade', 'endpoint': 'EC50 grade', 'unit': None,
                        'grade_definitions': {'A': 'EC50 < 10 nM', 'B': '10–100 nM'}})
    base = copy.deepcopy(obs(d, 'WO2011138751A2:example:1:0'))
    graded = {**base, 'id': 'graded', 'assay_id': 'WO2011138751A2:AMY3R_grade', 'relation': 'grade',
              'grade': 'A', 'value': None, 'unit': None}
    d['observations'].append(graded)
    assert Ledger.model_validate(d)
    graded['grade'] = 'C'
    with pytest.raises(ValidationError, match='等级 C 未在'):
        Ledger.model_validate(d)
    graded.update(grade='A', value=5.0)
    with pytest.raises(ValidationError, match='不能同时给出连续数值'):
        Ledger.model_validate(d)


def test_emax_is_an_ordinary_endpoint_with_its_own_assay():
    d = as_dict()
    d['assays'].append({'id': 'WO2011138751A2:AMY3R_Emax', 'document_id': 'WO2011138751A2',
                        'source_assay_id': 'AMY3R_Emax', 'endpoint': 'Emax', 'unit': '%',
                        'variant': '10-point'})
    base = obs(d, 'WO2011138751A2:example:1:0')
    d['observations'].append({**base, 'id': 'emax', 'assay_id': 'WO2011138751A2:AMY3R_Emax',
                              'relation': '=', 'value': 98.0, 'unit': '%'})
    assert Ledger.model_validate(d).assays[-1].variant == '10-point'


@pytest.mark.parametrize('mutate, message', [
    (lambda d: d['observations'].append(copy.deepcopy(d['observations'][0])), 'observations 编号重复'),
    (lambda d: d['observations'][0].update(compound_id='nope'), '不存在的化合物'),
    (lambda d: d['observations'][0].update(assay_id='nope'), '不存在的 assay'),
    (lambda d: d['compounds'][0].update(document_id='nope'), '不存在的文档'),
    (lambda d: obs(d, 'WO2011138751A2:example:1:0').update(assay_id='WO2013132376A1:ALK_WT_Ki'), '属于另一文档'),
    (lambda d: d['compounds'][0].update(role='product'), 'role'),
    (lambda d: d['compounds'][0].update(colour='blue'), 'Extra inputs'),
])
def test_integrity_violations_are_rejected(mutate, message):
    d = as_dict()
    mutate(d)
    with pytest.raises(ValidationError, match=message):
        Ledger.model_validate(d)


def test_confirmation_requires_a_named_reviewer():
    d = as_dict()
    d['observations'][0]['review']['record_status'] = 'confirmed'
    with pytest.raises(ValidationError, match='复核人'):
        Ledger.model_validate(d)
    d['observations'][0]['review']['reviewer'] = 'reviewer-name'
    assert Ledger.model_validate(d)


def test_committed_json_schema_is_current():
    assert schema.main(['--check']) == 0
