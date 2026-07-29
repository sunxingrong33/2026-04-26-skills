"""Deterministic rule engine: structure delta -> candidate hypothesis.

Nothing here calls an LLM. A hypothesis is only ever produced by a rule whose
conditions were satisfied by numbers computed in features.py/deltas.py, and each
hypothesis records exactly which conditions fired so the narrative layer can
cite them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .deltas import GenerationDelta

DEFAULT_RULES_PATH = Path(__file__).with_name("rules.yaml")

_OP_RE = re.compile(r"^\s*(<=|>=|<|>|==|!=)\s*([+-]?\d*\.?\d+)\s*$")
_TRANSITION_RE = re.compile(r"^\s*(true|false)\s*->\s*(true|false)\s*$", re.I)


class RuleError(ValueError):
    pass


@dataclass
class MatchedCondition:
    feature: str
    label: str
    operator: str
    val_from: float | None
    val_to: float | None
    observed: float
    text: str


@dataclass
class Hypothesis:
    rule_id: str
    name: str
    narrative: str
    confidence: float
    confidence_base: float
    matched: list[MatchedCondition]
    n_support: int
    penalties: list[str] = field(default_factory=list)

    @property
    def features_used(self) -> list[str]:
        return [m.feature for m in self.matched]


def load_rules(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else DEFAULT_RULES_PATH
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict) or "rules" not in doc:
        raise RuleError(f"{path} 不是合法规则库：缺少顶层 'rules'")
    seen: set[str] = set()
    for rule in doc["rules"]:
        for key in ("id", "name", "conditions", "confidence_base"):
            if key not in rule:
                raise RuleError(f"规则缺少必需字段 {key}: {rule.get('id', rule)}")
        if rule["id"] in seen:
            raise RuleError(f"规则 id 重复: {rule['id']}")
        seen.add(rule["id"])
        if not 0.0 < float(rule["confidence_base"]) <= 1.0:
            raise RuleError(f"{rule['id']}: confidence_base 必须落在 (0, 1]")
    return doc


def _compare(value: float, expr: str) -> bool:
    m = _OP_RE.match(expr)
    if not m:
        raise RuleError(f"无法解析的比较表达式: {expr!r}")
    op, raw = m.group(1), float(m.group(2))
    return {
        "<=": value <= raw,
        ">=": value >= raw,
        "<": value < raw,
        ">": value > raw,
        "==": value == raw,
        "!=": value != raw,
    }[op]


def _pseudo_features(delta: GenerationDelta) -> dict[str, dict[str, float | None]]:
    """Values that are not RDKit descriptors but behave like features in rules."""
    out: dict[str, dict[str, float | None]] = {}
    if delta.alignment is not None:
        out["ring_jaccard"] = {
            "value_to": delta.alignment.ring_jaccard,
            "value_from": delta.alignment.ring_jaccard,
            "delta": 0.0,
            "label": "环系 Jaccard",
        }
        out["ring_containment"] = {
            "value_to": delta.alignment.ring_containment,
            "value_from": delta.alignment.ring_containment,
            "delta": 0.0,
            "label": "环系保留率",
        }
    if delta.activity_ratio is not None:
        out["activity_ratio"] = {
            "value_to": delta.activity_ratio,
            "value_from": 1.0,
            "delta": delta.activity_ratio - 1.0,
            "ratio": delta.activity_ratio,
            "label": "活性变化倍数",
        }
    years = delta.years_elapsed
    if years is not None:
        out["years_elapsed"] = {
            "value_to": years,
            "value_from": years,
            "delta": 0.0,
            "label": "相隔年数",
        }
    return out


def _support_factor(n_support: int, table: dict[str, float]) -> float:
    thresholds = sorted((int(k), float(v)) for k, v in table.items())
    factor = thresholds[0][1] if thresholds else 1.0
    for threshold, value in thresholds:
        if n_support >= threshold:
            factor = value
    return factor


def _eval_condition(
    cond: dict[str, Any], delta: GenerationDelta, pseudo: dict[str, dict[str, Any]]
) -> MatchedCondition | None:
    feature = cond["feature"]
    fd = delta.delta_of(feature)
    ps = pseudo.get(feature)
    if fd is None and ps is None:
        return None

    label = fd.label if fd else ps.get("label", feature)
    val_from = fd.val_from if fd else ps.get("value_from")
    val_to = fd.val_to if fd else ps.get("value_to")

    def _mk(operator: str, observed: float, text: str) -> MatchedCondition:
        return MatchedCondition(
            feature=feature,
            label=label,
            operator=operator,
            val_from=val_from,
            val_to=val_to,
            observed=observed,
            text=text,
        )

    if "transition" in cond:
        if fd is None or fd.kind != "boolean":
            return None
        m = _TRANSITION_RE.match(cond["transition"])
        if not m:
            raise RuleError(f"无法解析的 transition: {cond['transition']!r}")
        want_from = m.group(1).lower() == "true"
        want_to = m.group(2).lower() == "true"
        got_from = fd.val_from >= 0.5
        got_to = fd.val_to >= 0.5
        if got_from == want_from and got_to == want_to:
            return _mk(
                "transition",
                fd.val_to,
                f"{label}: {str(got_from).lower()} -> {str(got_to).lower()}"
                f" (本代 {fd.val_to:.0%} 的化合物命中)",
            )
        return None

    if "delta" in cond:
        d = fd.delta if fd else ps.get("delta")
        if d is None or not _compare(d, cond["delta"]):
            return None
        return _mk(
            "delta",
            d,
            f"{label}: {val_from:g} -> {val_to:g} (Δ {d:+g})",
        )

    if "abs_delta" in cond:
        d = fd.delta if fd else ps.get("delta")
        if d is None or not _compare(abs(d), cond["abs_delta"]):
            return None
        return _mk("abs_delta", abs(d), f"{label}: {val_from:g} -> {val_to:g} (|Δ| {abs(d):g})")

    if "value_to" in cond:
        v = val_to
        if v is None or not _compare(v, cond["value_to"]):
            return None
        return _mk("value_to", v, f"{label}: 本代 {v:g}")

    if "value_from" in cond:
        v = val_from
        if v is None or not _compare(v, cond["value_from"]):
            return None
        return _mk("value_from", v, f"{label}: 上代 {v:g}")

    if "ratio" in cond:
        v = ps.get("ratio") if ps else None
        if v is None or not _compare(v, cond["ratio"]):
            return None
        fold = 1 / v if v else float("inf")
        return _mk("ratio", v, f"{label}: {v:g} 倍 (约提升 {fold:.1f} 倍)")

    raise RuleError(f"条件缺少可识别算子: {cond}")


def evaluate(
    delta: GenerationDelta, rules_doc: dict[str, Any] | None = None
) -> list[Hypothesis]:
    doc = rules_doc or load_rules()
    meta = doc.get("meta", {})
    support_table = meta.get("support_factors", {"1": 1.0})
    cross_assay_penalty = float(meta.get("cross_assay_penalty", 1.0))

    pseudo = _pseudo_features(delta)
    hypotheses: list[Hypothesis] = []

    for rule in doc["rules"]:
        combine = rule.get("combine", "all")
        matched: list[MatchedCondition] = []
        n_eval = 0
        for cond in rule["conditions"]:
            n_eval += 1
            hit = _eval_condition(cond, delta, pseudo)
            if hit is not None:
                matched.append(hit)

        if combine == "all":
            ok = len(matched) == n_eval
        elif combine == "any":
            ok = len(matched) > 0
        else:
            raise RuleError(f"{rule['id']}: 未知 combine 值 {combine!r}")
        if not ok:
            continue

        if delta.n_support < int(rule.get("min_support", 1)):
            continue

        penalties: list[str] = []
        conf = float(rule["confidence_base"])

        factor = _support_factor(delta.n_support, support_table)
        if factor < 1.0:
            penalties.append(f"支持化合物数 n={delta.n_support}，置信度乘子 {factor:g}")
        conf *= factor

        if rule.get("uses_activity") and not delta.activity_comparable:
            penalties.append(f"跨 assay 活性比较，置信度乘子 {cross_assay_penalty:g}")
            conf *= cross_assay_penalty

        hypotheses.append(
            Hypothesis(
                rule_id=rule["id"],
                name=rule["name"],
                narrative=" ".join(rule["narrative"].split()),
                confidence=round(min(conf, 1.0), 3),
                confidence_base=float(rule["confidence_base"]),
                matched=matched,
                n_support=delta.n_support,
                penalties=penalties,
            )
        )

    hypotheses.sort(key=lambda h: h.confidence, reverse=True)
    return hypotheses
