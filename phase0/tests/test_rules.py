import pytest
import yaml

from phase0.sar.deltas import FeatureDelta, GenerationDelta, GenerationSummary
from phase0.sar.rules import RuleError, evaluate, load_rules


def _summary(gen, n=1):
    return GenerationSummary(
        program_id="p", generation=gen, members=[], numeric={}, boolean_fraction={},
        earliest_priority=None, patents=[], activity_median_nm=None,
        activity_assays=[], activity_n=0, n_compounds=n,
    )


def _delta(features, n_support=1, alignment=None, ratio=None, comparable=True):
    return GenerationDelta(
        program_id="p", gen_from=1, gen_to=2,
        summary_from=_summary(1, n_support), summary_to=_summary(2, n_support),
        features=features, alignment=alignment, activity_ratio=ratio,
        activity_comparable=comparable, n_support=n_support,
    )


def _num(name, a, b, n=1):
    return FeatureDelta(name, name, a, b, round(b - a, 3), n)


def _bool(name, a, b, n=1):
    return FeatureDelta(name, name, a, b, round(b - a, 3), n, kind="boolean")


# --- rule library integrity ------------------------------------------------

def test_rule_library_loads_and_is_wellformed():
    doc = load_rules()
    assert doc["rules"]
    ids = [r["id"] for r in doc["rules"]]
    assert len(ids) == len(set(ids))
    for rule in doc["rules"]:
        assert rule["conditions"], f"{rule['id']} has no conditions"
        assert 0 < rule["confidence_base"] <= 1
        assert rule.get("combine", "all") in ("all", "any")


def test_every_rule_has_a_narrative():
    """A rule with no narrative would force the LLM to invent the explanation."""
    for rule in load_rules()["rules"]:
        assert rule.get("narrative", "").strip(), f"{rule['id']} missing narrative"


def test_duplicate_rule_ids_rejected(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump({"rules": [
        {"id": "x", "name": "x", "conditions": [{"feature": "mw", "delta": ">= 0"}],
         "confidence_base": 0.5, "narrative": "n"},
        {"id": "x", "name": "y", "conditions": [{"feature": "mw", "delta": ">= 0"}],
         "confidence_base": 0.5, "narrative": "n"},
    ]}), encoding="utf-8")
    with pytest.raises(RuleError, match="重复"):
        load_rules(p)


# --- condition operators ---------------------------------------------------

def test_all_combine_requires_every_condition():
    doc = {"meta": {}, "rules": [{
        "id": "t", "name": "t", "combine": "all", "confidence_base": 1.0,
        "narrative": "n",
        "conditions": [{"feature": "mw", "delta": "<= -30"},
                       {"feature": "clogp", "delta": "<= -0.5"}]}]}
    assert evaluate(_delta({"mw": _num("mw", 450, 400), "clogp": _num("clogp", 5.0, 4.0)}), doc)
    # second condition fails -> no hit
    assert not evaluate(_delta({"mw": _num("mw", 450, 400), "clogp": _num("clogp", 5.0, 5.0)}), doc)


def test_any_combine_needs_only_one():
    doc = {"meta": {}, "rules": [{
        "id": "t", "name": "t", "combine": "any", "confidence_base": 1.0,
        "narrative": "n",
        "conditions": [{"feature": "f_count", "delta": ">= +1"},
                       {"feature": "benzylic_h_count", "delta": "<= -1"}]}]}
    hits = evaluate(_delta({"f_count": _num("f_count", 1, 4),
                           "benzylic_h_count": _num("benzylic_h_count", 3, 3)}), doc)
    assert len(hits) == 1
    assert len(hits[0].matched) == 1


def test_boolean_transition_direction_matters():
    doc = {"meta": {}, "rules": [{
        "id": "t", "name": "t", "confidence_base": 1.0, "narrative": "n",
        "conditions": [{"feature": "has_macrocycle", "transition": "false -> true"}]}]}
    assert evaluate(_delta({"has_macrocycle": _bool("has_macrocycle", 0.0, 1.0)}), doc)
    assert not evaluate(_delta({"has_macrocycle": _bool("has_macrocycle", 1.0, 0.0)}), doc)
    assert not evaluate(_delta({"has_macrocycle": _bool("has_macrocycle", 0.0, 0.0)}), doc)


def test_missing_feature_never_fires_a_rule():
    """A rule must not fire on absent data -- silence beats a false positive."""
    doc = {"meta": {}, "rules": [{
        "id": "t", "name": "t", "confidence_base": 1.0, "narrative": "n",
        "conditions": [{"feature": "basic_pka", "delta": "<= -1.5"}]}]}
    assert evaluate(_delta({"mw": _num("mw", 450, 400)}), doc) == []


def test_unknown_operator_raises():
    doc = {"meta": {}, "rules": [{
        "id": "t", "name": "t", "confidence_base": 1.0, "narrative": "n",
        "conditions": [{"feature": "mw", "wobble": ">= 1"}]}]}
    with pytest.raises(RuleError, match="可识别算子"):
        evaluate(_delta({"mw": _num("mw", 450, 400)}), doc)


# --- confidence -------------------------------------------------------------

def test_single_compound_generations_are_penalised():
    doc = {"meta": {"support_factors": {"1": 0.8, "5": 1.0}},
           "rules": [{"id": "t", "name": "t", "confidence_base": 1.0, "narrative": "n",
                      "conditions": [{"feature": "mw", "delta": "<= -30"}]}]}
    weak = evaluate(_delta({"mw": _num("mw", 450, 400)}, n_support=1), doc)[0]
    strong = evaluate(_delta({"mw": _num("mw", 450, 400, 6)}, n_support=6), doc)[0]
    assert weak.confidence == 0.8
    assert strong.confidence == 1.0
    assert weak.penalties and not strong.penalties


def test_cross_assay_activity_is_discounted():
    doc = {"meta": {"support_factors": {"1": 1.0}, "cross_assay_penalty": 0.75},
           "rules": [{"id": "t", "name": "t", "confidence_base": 0.8, "narrative": "n",
                      "uses_activity": True,
                      "conditions": [{"feature": "activity_ratio", "ratio": "<= 0.2"}]}]}
    same = evaluate(_delta({}, ratio=0.05, comparable=True), doc)[0]
    cross = evaluate(_delta({}, ratio=0.05, comparable=False), doc)[0]
    assert cross.confidence < same.confidence
    assert any("assay" in p for p in cross.penalties)


def test_hypotheses_sorted_by_confidence():
    doc = {"meta": {}, "rules": [
        {"id": "lo", "name": "lo", "confidence_base": 0.4, "narrative": "n",
         "conditions": [{"feature": "mw", "delta": "<= 0"}]},
        {"id": "hi", "name": "hi", "confidence_base": 0.9, "narrative": "n",
         "conditions": [{"feature": "mw", "delta": "<= 0"}]}]}
    hits = evaluate(_delta({"mw": _num("mw", 450, 400)}), doc)
    assert [h.rule_id for h in hits] == ["hi", "lo"]
