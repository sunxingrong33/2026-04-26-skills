"""Tests for the anti-hallucination audit.

Hallucination rate is the product's survival constraint, so these tests treat a
fabricated citation surviving the audit as the worst possible failure.
"""

import json

from phase0.sar.narrate import Evidence, FactsBlock, audit_response


def _facts():
    return FactsBlock(
        program_name="test",
        header_lines=["【代际】Gen1 → Gen2"],
        evidence=[
            Evidence("E1", "structure", "大环: false -> true, n=1"),
            Evidence("E2", "property", "氢键给体数: 中位 2 → 1 (Δ -1), n=1"),
        ],
        rule_lines=[],
        caveats=[],
    )


def _resp(hypotheses, **kw):
    payload = {"headline": "h", "hypotheses": hypotheses,
               "what_we_cannot_tell": "", "insufficient_evidence": False}
    payload.update(kw)
    return json.dumps(payload, ensure_ascii=False)


def test_valid_citations_are_kept():
    r = audit_response(_resp([
        {"claim": "提示做了大环化", "confidence": "high", "evidence_refs": ["E1"]}]), _facts())
    assert r.ok
    assert len(r.parsed["hypotheses"]) == 1


def test_fabricated_citation_is_dropped():
    r = audit_response(_resp([
        {"claim": "提示优化了脑渗透", "confidence": "high", "evidence_refs": ["E7"]}]), _facts())
    assert r.parsed["hypotheses"] == []
    assert len(r.dropped) == 1
    assert "E7" in r.dropped[0]["_drop_reason"]


def test_partially_fabricated_citation_drops_whole_hypothesis():
    r = audit_response(_resp([
        {"claim": "x", "confidence": "high", "evidence_refs": ["E1", "E9"]}]), _facts())
    assert r.parsed["hypotheses"] == []


def test_uncited_hypothesis_is_dropped():
    r = audit_response(_resp([
        {"claim": "他们显然在解决耐药问题", "confidence": "high", "evidence_refs": []}]), _facts())
    assert r.parsed["hypotheses"] == []
    assert "没有任何证据引用" in r.dropped[0]["_drop_reason"]


def test_citations_inline_in_claim_are_recognised():
    r = audit_response(_resp([
        {"claim": "大环化 [E1] 提示构象锁定", "confidence": "medium", "evidence_refs": []}]),
        _facts())
    assert len(r.parsed["hypotheses"]) == 1
    assert r.parsed["hypotheses"][0]["evidence_refs"] == ["E1"]


def test_invented_number_warns_by_default():
    r = audit_response(_resp([
        {"claim": "cLogP 下降了 2.24 个单位", "confidence": "high", "evidence_refs": ["E2"]}]),
        _facts())
    assert len(r.parsed["hypotheses"]) == 1
    assert any("2.24" in w for w in r.warnings)


def test_invented_number_drops_under_strict_mode():
    r = audit_response(_resp([
        {"claim": "cLogP 下降了 2.24 个单位", "confidence": "high", "evidence_refs": ["E2"]}]),
        _facts(), strict_numbers=True)
    assert r.parsed["hypotheses"] == []


def test_number_quoted_from_facts_is_accepted():
    r = audit_response(_resp([
        {"claim": "氢键给体数从 2 降到 1", "confidence": "high", "evidence_refs": ["E2"]}]),
        _facts(), strict_numbers=True)
    assert len(r.parsed["hypotheses"]) == 1


def test_malformed_json_is_a_hard_failure():
    r = audit_response("抱歉，我无法回答", _facts())
    assert not r.ok
    assert r.parsed is None


def test_markdown_fenced_json_is_tolerated():
    raw = "```json\n" + _resp([
        {"claim": "x", "confidence": "low", "evidence_refs": ["E1"]}]) + "\n```"
    r = audit_response(raw, _facts())
    assert r.ok and len(r.parsed["hypotheses"]) == 1


def test_empty_output_without_insufficient_flag_warns():
    r = audit_response(_resp([]), _facts())
    assert any("证据不足" in w for w in r.warnings)


def test_declared_insufficient_evidence_is_clean():
    r = audit_response(_resp([], insufficient_evidence=True), _facts())
    assert r.ok and not r.warnings


def test_facts_block_never_leaks_uncomputed_values():
    """Everything in FACTS must come from the evidence list, not free text."""
    facts = _facts()
    rendered = facts.render()
    assert "[E1]" in rendered and "[E2]" in rendered
    assert "[E3]" not in rendered


def _pfizer_gen3_4():
    from phase0.sar.cli import DEFAULT_DATA
    from phase0.sar.deltas import compute_program_deltas
    from phase0.sar.narrate import build_facts
    from phase0.sar.rules import evaluate, load_rules
    from phase0.sar.schema import load_dataset

    ds = load_dataset(DEFAULT_DATA)
    _, deltas = compute_program_deltas(ds, "pfizer-alk", run_mcs=False)
    d = deltas[-1]
    return build_facts("p", d, evaluate(d, load_rules()))


def test_facts_include_functional_group_changes():
    """Regression: an allowlist in build_facts hid every feature no rule used.

    The ether -> amide linker swap and the added nitrile are the most informative
    facts about this transition and were absent from the prompt entirely.
    """
    text = _pfizer_gen3_4().render()
    assert "醚键数" in text
    assert "酰胺数" in text
    assert "腈基数" in text


def test_facts_block_stays_bounded():
    facts = _pfizer_gen3_4()
    props = [e for e in facts.evidence if e.kind == "property"]
    assert len(props) <= 20
