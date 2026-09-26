"""Adversarial cases found during the project audit; no network or LLM calls."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from phase0.sar.schema import load_dataset, Measurement
from phase0.sar.deltas import compute_program_deltas
from phase0.sar.narrate import audit_response, Evidence, FactsBlock, narrate
from phase0.sar.benchmark import score_transition, ProgramScore


def dataset():
    ds = load_dataset(Path(__file__).parents[1] / "data")
    members = ds.members_of("pfizer-alk")
    for m in members:
        m.activity_value_nm = 100 if m.generation == 1 else 1
        m.activity_assay = "A" if m.generation == 1 else "B"
        m.activity_type = "IC50"
    a = next(m.compound_id for m in members if m.generation == 1)
    b = next(m.compound_id for m in members if m.generation == 3)
    return ds, a, b


def test_other_measurement_does_not_change_activity_comparability():
    ds, a, b = dataset()
    ds.measurements = [Measurement(a, "efflux", 8, "", "MDCK", "src"),
                       Measurement(b, "efflux", 1, "", "MDCK", "src")]
    _, deltas = compute_program_deltas(ds, "pfizer-alk", run_mcs=False)
    assert deltas[0].activity_comparable is False
    assert deltas[0].activity_ratio is None
    assert deltas[0].measures["efflux"].comparable is True


def test_cross_generation_concentrations_are_converted():
    ds, a, b = dataset()
    ds.measurements = [Measurement(a, "ic50", 1000, "nM", "A", "src"),
                       Measurement(b, "ic50", 1, "μM", "A", "src")]
    _, deltas = compute_program_deltas(ds, "pfizer-alk", run_mcs=False)
    md = deltas[0].measures["ic50"]
    assert md.ratio == 1 and md.delta == 0 and md.unit == "nM"


def test_incompatible_cross_generation_units_rejected():
    ds, a, b = dataset()
    ds.measurements = [Measurement(a, "x", 1, "mg/L", "A", "src"),
                       Measurement(b, "x", 1, "nM", "A", "src")]
    with pytest.raises(ValueError, match="跨代混用了单位"):
        compute_program_deltas(ds, "pfizer-alk", run_mcs=False)


def test_partial_assay_overlap_not_comparable():
    ds, a, b = dataset()
    ds.measurements = [Measurement(a, "x", 1, "", "A", "src"),
                       Measurement(a, "x", 2, "", "B", "src"),
                       Measurement(b, "x", 1, "", "A", "src")]
    _, deltas = compute_program_deltas(ds, "pfizer-alk", run_mcs=False)
    assert not deltas[0].measures["x"].comparable
    assert deltas[0].measures["x"].ratio is None


def facts():
    return FactsBlock("t", [], [Evidence("E1", "property", "value 1"),
                               Evidence("E2", "property", "unrelated 999")], [], [])


def response(hyps):
    return json.dumps({"headline": "Improves 999 times", "hypotheses": hyps,
                       "what_we_cannot_tell": "", "insufficient_evidence": False})


def test_dropped_hypotheses_cannot_leave_claim_in_headline():
    r = audit_response(response([{"claim": "x", "confidence": "high", "evidence_refs": ["E999"]}]), facts(), True)
    assert r.ok and r.parsed["insufficient_evidence"]
    assert "999" not in r.parsed["headline"]


def test_number_must_belong_to_cited_evidence():
    r = audit_response(response([{"claim": "improves 999 times", "confidence": "high", "evidence_refs": ["E1"]}]), facts(), True)
    assert r.parsed["hypotheses"] == []


@pytest.mark.parametrize("payload", [[], None, 12, {}, {"hypotheses": [None]},
    {"headline": "x", "hypotheses": [{"claim": "x", "confidence": "high", "evidence_refs": "E1"}],
     "what_we_cannot_tell": "", "insufficient_evidence": False}])
def test_invalid_output_schema_is_retryable(payload):
    assert not audit_response(json.dumps(payload), facts()).ok


def test_model_schema_failure_retries(monkeypatch):
    outputs = iter(["[]", response([{"claim": "value 1", "confidence": "low", "evidence_refs": ["E1"]}])])
    monkeypatch.setattr("phase0.sar.narrate.call_claude", lambda *a, **kw: next(outputs))
    result = narrate(facts(), strict_numbers=True)
    assert result.ok and len(result.parsed["hypotheses"]) == 1


def test_bad_hypothesis_counted_once_and_uncited_is_counted():
    hyps = [{"claim": "bad 777", "evidence_refs": ["E7", "E8", "E9"]}]
    hyps += [{"claim": "value 1", "evidence_refs": ["E1"]}] * 9
    ts = score_transition("p", 1, 2, {"hypotheses": hyps}, facts(), [])
    ps = ProgramScore("p", True, [], [ts])
    assert ps.automated_hallucination_lower_bound == 0.1
    assert score_transition("p", 1, 2, {"hypotheses": [{"claim": "x"}]}, facts(), []).n_flagged_hypotheses == 1


def test_negated_or_uncited_claim_not_counted_as_recall():
    for claim, refs in [("无法判断脑渗透", ["E1"]), ("脑渗透", ["E9"])]:
        ts = score_transition("p", 1, 2, {"hypotheses": [{"claim": claim, "evidence_refs": refs}]}, facts(), [{"id": "cns", "keywords": ["脑"]}])
        assert not ts.matched_challenges
