"""End-to-end tests over the shipped seed data."""

import pytest

from phase0.sar.cli import DEFAULT_DATA
from phase0.sar.deltas import compute_program_deltas
from phase0.sar.narrate import build_facts
from phase0.sar.rules import evaluate, load_rules
from phase0.sar.schema import load_dataset


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DEFAULT_DATA)


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def test_seed_data_loads_and_passes_formula_gate(dataset):
    assert len(dataset.compounds) == 5
    assert len(dataset.programs) == 2


def test_unverified_provenance_is_surfaced(dataset):
    """The seed data is model-recalled, so it must never look verified."""
    assert dataset.unverified_structures()
    assert dataset.warnings


def test_pfizer_program_generations_ordered(dataset):
    summaries, deltas = compute_program_deltas(dataset, "pfizer-alk", run_mcs=False)
    assert [s.generation for s in summaries] == [1, 2]
    assert len(deltas) == 1
    assert deltas[0].years_elapsed and deltas[0].years_elapsed > 7


def test_landscape_generation_two_aggregates_three_compounds(dataset):
    summaries, _ = compute_program_deltas(dataset, "alk-landscape", run_mcs=False)
    gen2 = next(s for s in summaries if s.generation == 2)
    assert gen2.n_compounds == 3
    assert gen2.activity_n == 3
    # median of 0.2 / 0.62 / 1.9
    assert gen2.activity_median_nm == 0.62


def test_same_assay_comparison_is_marked_comparable(dataset):
    _, deltas = compute_program_deltas(dataset, "pfizer-alk", run_mcs=False)
    assert deltas[0].activity_comparable is True


def test_cross_assay_comparison_is_flagged(dataset):
    _, deltas = compute_program_deltas(dataset, "alk-landscape", run_mcs=False)
    assert any(not d.activity_comparable for d in deltas)


def test_macrocyclisation_hypothesis_fires_on_pfizer_program(dataset, rules):
    _, deltas = compute_program_deltas(dataset, "pfizer-alk", run_mcs=False)
    hits = {h.rule_id for h in evaluate(deltas[0], rules)}
    assert "macrocyclization" in hits
    assert "pgp_efflux_mitigation" in hits


def test_core_hop_not_claimed_between_crizotinib_and_lorlatinib(dataset, rules):
    _, deltas = compute_program_deltas(dataset, "pfizer-alk", run_mcs=False)
    hits = {h.rule_id for h in evaluate(deltas[0], rules)}
    assert "core_hopping" not in hits


def test_every_hypothesis_confidence_is_bounded(dataset, rules):
    for pid in dataset.programs:
        _, deltas = compute_program_deltas(dataset, pid, run_mcs=False)
        for d in deltas:
            for h in evaluate(d, rules):
                assert 0 < h.confidence <= 1.0


def test_facts_block_evidence_ids_are_unique_and_sequential(dataset, rules):
    _, deltas = compute_program_deltas(dataset, "pfizer-alk", run_mcs=False)
    facts = build_facts("p", deltas[0], evaluate(deltas[0], rules))
    ids = [e.eid for e in facts.evidence]
    assert ids == [f"E{i+1}" for i in range(len(ids))]


def test_facts_block_carries_low_support_caveat(dataset, rules):
    _, deltas = compute_program_deltas(dataset, "pfizer-alk", run_mcs=False)
    facts = build_facts("p", deltas[0], evaluate(deltas[0], rules))
    assert any("1 个化合物" in c for c in facts.caveats)


def test_every_rule_hit_maps_to_at_least_one_evidence_id(dataset, rules):
    """A rule hit the narrative layer cannot cite is a hallucination waiting to happen."""
    for pid in dataset.programs:
        _, deltas = compute_program_deltas(dataset, pid, run_mcs=False)
        for d in deltas:
            hyps = evaluate(d, rules)
            facts = build_facts("p", d, hyps)
            for line in facts.rule_lines:
                assert "(无直接证据编号)" not in line, line
