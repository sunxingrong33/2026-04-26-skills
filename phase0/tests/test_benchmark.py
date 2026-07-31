"""Tests for the §5 scoring harness.

The benchmark is what turns "does this feel useful" into a number, so its own
failure modes matter: a metric that flatters the tool is worse than no metric.
"""

import json

import pytest

from phase0.sar.benchmark import (
    _claim_text,
    load_benchmark,
    score_transition,
)
from phase0.sar.cli import DEFAULT_DATA
from phase0.sar.narrate import Evidence, FactsBlock

CHALLENGES = [
    {"id": "cns", "name": "脑渗透", "keywords": ["脑", "渗透", "P-gp"]},
    {"id": "resistance", "name": "耐药", "keywords": ["耐药", "突变"]},
]


def _facts():
    return FactsBlock(
        program_name="t",
        header_lines=[],
        evidence=[
            Evidence("E1", "property", "氢键给体数: 中位 2 → 1 (Δ -1)"),
            Evidence("E2", "structure", "大环: false -> true"),
        ],
        rule_lines=[],
        caveats=[],
    )


def _narrative(hyps, **kw):
    d = {"headline": "", "hypotheses": hyps, "what_we_cannot_tell": "",
         "insufficient_evidence": False}
    d.update(kw)
    return d


def test_disclaimer_does_not_count_towards_recall():
    """Regression: 'cannot tell whether it was about 渗透' scored as a hit."""
    n = _narrative([], what_we_cannot_tell="无法区分动机是效价还是渗透性")
    assert "渗透" not in _claim_text(n)
    s = score_transition("p", 1, 2, n, _facts(), CHALLENGES)
    assert s.matched_challenges == set()


def test_assertion_counts_towards_recall():
    n = _narrative([{"claim": "提示在优化脑渗透", "evidence_refs": ["E1"]}])
    s = score_transition("p", 1, 2, n, _facts(), CHALLENGES)
    assert s.matched_challenges == {"cns"}


def test_headline_counts_towards_recall():
    n = _narrative([], headline="这一代在解决耐药问题")
    s = score_transition("p", 1, 2, n, _facts(), CHALLENGES)
    assert s.matched_challenges == {"resistance"}


def test_citation_accuracy_penalises_fabricated_refs():
    n = _narrative([
        {"claim": "a", "evidence_refs": ["E1"]},
        {"claim": "b", "evidence_refs": ["E9"]},
    ])
    s = score_transition("p", 1, 2, n, _facts(), CHALLENGES)
    assert s.n_refs == 2 and s.n_bad_refs == 1
    assert s.citation_accuracy == 0.5


def test_stray_numbers_are_counted():
    n = _narrative([{"claim": "下降了 7.77 个单位", "evidence_refs": ["E1"]}])
    s = score_transition("p", 1, 2, n, _facts(), CHALLENGES)
    assert s.n_stray_numbers == 1


def test_numbers_quoted_from_facts_are_not_stray():
    n = _narrative([{"claim": "氢键给体数从 2 降到 1", "evidence_refs": ["E1"]}])
    s = score_transition("p", 1, 2, n, _facts(), CHALLENGES)
    assert s.n_stray_numbers == 0


def test_unverified_ground_truth_refuses_to_score_recall():
    """An unverified challenge list must not yield a usable-looking number."""
    from phase0.sar.benchmark import ProgramScore

    ps = ProgramScore("p", verified_ground_truth=False, challenges=CHALLENGES)
    ps.transitions.append(score_transition(
        "p", 1, 2, _narrative([{"claim": "脑渗透", "evidence_refs": ["E1"]}]),
        _facts(), CHALLENGES))
    assert ps.recall is None
    ps.verified_ground_truth = True
    assert ps.recall == 0.5


def test_shipped_benchmark_ground_truth_is_marked_unverified():
    bench = load_benchmark(DEFAULT_DATA / "benchmark.yaml")
    for prog in bench["programs"]:
        assert prog["verified"] is False, (
            f"{prog['program_id']} 标成了已核实 —— ground truth 必须逐条对照原文核对后才能置 true"
        )


def test_benchmark_targets_match_the_plan():
    t = load_benchmark(DEFAULT_DATA / "benchmark.yaml")["targets"]
    assert t["recall"] == 0.60
    assert t["hallucination_rate"] == 0.10
    assert t["citation_accuracy"] == 0.95
