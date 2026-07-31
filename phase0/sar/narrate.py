"""Explanation layer: turn computed facts into cited prose, then audit the prose.

Two hard rules, both enforced in code rather than trusted to the prompt:

1. The LLM receives only the FACTS block. Every number in that block was computed
   by features.py / deltas.py / rules.py.
2. Every claim the LLM emits must carry [Ex] evidence references, and
   ``audit_response`` drops any hypothesis whose references do not resolve to a
   real evidence item. A hallucinated citation cannot survive to the UI.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .deltas import GenerationDelta
from .rules import Hypothesis

EVIDENCE_RE = re.compile(r"\[E(\d+)\]")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = """你是一名分析竞品专利的药物化学家。你的任务是把已经算好的结构与属性变化，翻译成研发意图的推断。

【硬约束 —— 违反任何一条，整条输出作废】
1. 只能基于 FACTS 中给出的事实作出陈述。禁止引入 FACTS 之外的任何事实性信息，
   包括你记忆中关于这些化合物、专利或公司的知识。
2. 每一条推断必须标注 [E1][E2] 形式的证据编号，编号必须来自 FACTS 中实际出现的编号。
3. 推断性语言必须使用"提示/可能/推测/倾向于"等措辞，不得使用确定性表述。
4. 不得输出 FACTS 中没有出现过的数字。需要引用数值时，原样引用。
5. 若证据不足以支撑任何推断，hypotheses 输出空数组，并把 insufficient_evidence 设为 true。
6. 规则命中（RULE HITS）是候选假说，不是结论。你可以采纳、合并、降级或否定它们，
   但采纳时必须引用支撑该规则的结构证据编号。

【输出格式】
只输出一个 JSON 对象，不要有其他文字，不要用 markdown 代码块包裹。结构：
{
  "headline": "一句话概括这一代改动在解什么问题（20-40字）",
  "hypotheses": [
    {
      "claim": "推断内容，一到两句",
      "confidence": "high" | "medium" | "low",
      "evidence_refs": ["E1", "E3"]
    }
  ],
  "what_we_cannot_tell": "基于现有证据明确无法判断的事项，一到两句；没有则空字符串",
  "insufficient_evidence": false
}"""


@dataclass
class Evidence:
    eid: str
    kind: str
    text: str
    citation: str = ""
    # Feature keys this item is evidence for. Set explicitly rather than parsed
    # back out of ``text`` -- label-matching silently left activity-driven rules
    # with no citable evidence.
    features: tuple[str, ...] = ()

    def render(self) -> str:
        cite = f"  <来源: {self.citation}>" if self.citation else ""
        return f"[{self.eid}] {self.text}{cite}"


@dataclass
class FactsBlock:
    program_name: str
    header_lines: list[str]
    evidence: list[Evidence]
    rule_lines: list[str]
    caveats: list[str]

    @property
    def index(self) -> dict[str, Evidence]:
        return {e.eid: e for e in self.evidence}

    def render(self) -> str:
        parts = [f"【程序】{self.program_name}", ""]
        parts += self.header_lines + [""]
        parts.append("【FACTS】")
        parts += [e.render() for e in self.evidence]
        if self.rule_lines:
            parts += ["", "【RULE HITS（确定性规则引擎产出的候选假说）】"]
            parts += self.rule_lines
        if self.caveats:
            parts += ["", "【数据质量警告 —— 必须在推断中体现为不确定性】"]
            parts += [f"- {c}" for c in self.caveats]
        return "\n".join(parts)


def _fmt(v: float) -> str:
    return f"{v:g}"


# Retained for reference only: build_facts now considers every feature that
# moved, so there is no allowlist to maintain.
CONTEXT_FEATURES = (
    "mw",
    "clogp",
    "tpsa",
    "fsp3",
    "hbd",
    "hba",
    "rotb",
    "aromatic_rings",
    "f_count",
    "halogen_count",
    "strong_basic_amine_count",
    "benzylic_h_count",
    "rotb",
    "ring_count",
)

MIN_RELATIVE_CHANGE = 0.05
# Cap on property evidence items so the FACTS block stays readable. Rule-backed
# facts always make the cut; the rest compete on relative magnitude.
MAX_PROPERTY_FACTS = 20


def build_facts(
    program_name: str,
    delta: GenerationDelta,
    hypotheses: list[Hypothesis],
    data_caveats: list[str] | None = None,
) -> FactsBlock:
    ev: list[Evidence] = []
    counter = 0

    def add(
        kind: str, text: str, citation: str = "", features: tuple[str, ...] = ()
    ) -> Evidence:
        nonlocal counter
        counter += 1
        item = Evidence(f"E{counter}", kind, text, citation, features)
        ev.append(item)
        return item

    a, b = delta.summary_from, delta.summary_to
    # n_support is min(n_from, n_to) -- the conservative claim strength. Printing
    # it alone next to a median computed over several compounds reads as "this
    # generation had one compound", so show the per-generation counts too.
    n_txt = f"n={a.n_compounds}→{b.n_compounds}, 支撑 n={delta.n_support}"

    header = [
        f"【代际】Gen{delta.gen_from} → Gen{delta.gen_to}",
        f"Gen{delta.gen_from}: {a.n_compounds} 个化合物, 专利 {a.patent_label}, "
        f"最早优先权日 {a.earliest_priority or '未知'}",
        f"Gen{delta.gen_to}: {b.n_compounds} 个化合物, 专利 {b.patent_label}, "
        f"最早优先权日 {b.earliest_priority or '未知'}",
    ]
    if delta.years_elapsed is not None:
        header.append(f"两代优先权日相隔 {delta.years_elapsed} 年")

    # --- compound-level identity evidence (gives the model citable anchors) ---
    for summary, gen in ((a, delta.gen_from), (b, delta.gen_to)):
        for m in summary.members:
            add(
                "compound",
                f"Gen{gen} 化合物 {m.compound_id}",
                f"{m.citation}",
            )

    # --- structural / boolean transitions ---
    for name, fd in delta.features.items():
        if fd.kind != "boolean":
            continue
        if fd.transition:
            add(
                "structure",
                f"{fd.label}: {fd.transition} "
                f"(Gen{delta.gen_from} {fd.val_from:.0%} → Gen{delta.gen_to} {fd.val_to:.0%} 的化合物命中), "
                f"{n_txt}",
                features=(name,),
            )

    if delta.alignment is not None:
        al = delta.alignment
        add(
            "scaffold",
            f"环系比对: {al.describe()}；环系 Jaccard {al.ring_jaccard:.2f}"
            f"（判定：{al.label}，阈值 {0.55}）",
            features=("ring_jaccard", "ring_containment"),
        )
        for note in al.notes:
            add("scaffold_caveat", f"骨架判定提示: {note}")

    # --- numeric deltas ---
    # Iterate over everything that actually moved, not over a fixed allowlist.
    # The allowlist version silently dropped any feature no rule referenced, so
    # when functional-group counts were added the ether->amide linker swap --
    # the most informative fact about that transition -- never reached the model
    # even though the terminal printed it. A feature the analysis layer computes
    # but the FACTS block hides is worse than one that was never computed.
    rule_features = {f for h in hypotheses for f in h.features_used}
    scored: list[tuple[float, str, object]] = []
    for name, fd in delta.features.items():
        if fd.kind == "boolean":
            continue
        moved = abs(fd.delta) >= max(abs(fd.val_from) * MIN_RELATIVE_CHANGE, 1e-9)
        if name not in rule_features and not moved:
            continue
        # rule-referenced facts sort first, then by relative magnitude
        rank = float("inf") if name in rule_features else abs(fd.delta) / (
            abs(fd.val_from) or 1
        )
        scored.append((rank, name, fd))

    scored.sort(key=lambda t: t[0], reverse=True)
    for _, name, fd in scored[:MAX_PROPERTY_FACTS]:
        add(
            "property",
            f"{fd.label}: 中位 {_fmt(fd.val_from)} → {_fmt(fd.val_to)} "
            f"(Δ {fd.delta:+g}), {n_txt}",
            features=(name,),
        )

    # --- activity ---
    if a.activity_median_nm is not None and b.activity_median_nm is not None:
        assay_txt = ""
        if a.activity_assays or b.activity_assays:
            assay_txt = (
                f"; assay: {'/'.join(a.activity_assays) or '未标注'}"
                f" vs {'/'.join(b.activity_assays) or '未标注'}"
            )
        fold = f"，约 {1/delta.activity_ratio:.1f} 倍提升" if delta.activity_ratio else ""
        add(
            "activity",
            f"中位活性: {_fmt(a.activity_median_nm)} nM (n={a.activity_n}) → "
            f"{_fmt(b.activity_median_nm)} nM (n={b.activity_n})"
            f"，比值 {delta.activity_ratio}{fold}{assay_txt}",
            features=("activity_ratio",),
        )
    elif delta.activity_note:
        add("activity", f"活性数据情况: {delta.activity_note}")

    # --- additional measurements (mutant potency, efflux, brain exposure, ...) ---
    for mtype, md in delta.measures.items():
        unit = f" {md.unit}" if md.unit else ""
        fold = ""
        if md.ratio:
            fold = (
                f"，约 {1/md.ratio:.1f} 倍下降"
                if md.ratio < 1
                else f"，约 {md.ratio:.1f} 倍上升"
            )
        caveat = f"；{md.note}" if md.note else ""
        add(
            "measurement",
            f"{mtype}: 中位 {_fmt(md.val_from)}{unit} → {_fmt(md.val_to)}{unit} "
            f"(Δ {md.delta:+g}{unit}){fold}, n={md.n_support}{caveat}",
            features=(f"measure:{mtype}",),
        )

    # --- rule hits ---
    rule_lines: list[str] = []
    ev_by_feature: dict[str, list[str]] = {}
    for item in ev:
        for key in item.features:
            ev_by_feature.setdefault(key, []).append(item.eid)

    for h in hypotheses:
        refs: list[str] = []
        for mc in h.matched:
            refs += ev_by_feature.get(mc.feature, [])
        ref_txt = "".join(f"[{r}]" for r in dict.fromkeys(refs)) or "(无直接证据编号)"
        detail = "; ".join(mc.text for mc in h.matched)
        penal = f" | 置信度调整: {'; '.join(h.penalties)}" if h.penalties else ""
        rule_lines.append(
            f"- {h.rule_id} ({h.name}) 置信度 {h.confidence} {ref_txt}\n"
            f"    触发条件: {detail}{penal}\n"
            f"    规则内置说明: {h.narrative}"
        )

    caveats = list(data_caveats or [])
    if not delta.activity_comparable and delta.activity_note:
        caveats.append(delta.activity_note)
    if delta.n_support <= 1:
        caveats.append(
            f"每代仅 {delta.n_support} 个化合物支撑，属性中位值等同于单点值，"
            "不能代表整代的 SAR 趋势"
        )

    return FactsBlock(program_name, header, ev, rule_lines, caveats)


@dataclass
class AuditResult:
    ok: bool
    parsed: dict[str, Any] | None
    dropped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def audit_response(raw: str, facts: FactsBlock, strict_numbers: bool = False) -> AuditResult:
    """Post-hoc validation. This is the step that actually stops hallucination.

    - unparseable JSON -> hard fail (caller retries)
    - a hypothesis citing a nonexistent [Ex] -> that hypothesis is dropped
    - a hypothesis with no citations at all -> dropped
    - a number not present in FACTS -> warning, or dropped under strict_numbers
    """
    errors: list[str] = []
    warnings: list[str] = []

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return AuditResult(False, None, errors=[f"输出不是合法 JSON: {exc}"])

    index = facts.index
    facts_text = facts.render()
    facts_numbers = set(NUMBER_RE.findall(facts_text))

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for hyp in parsed.get("hypotheses", []) or []:
        claim = str(hyp.get("claim", ""))
        refs = [str(r).strip().upper().lstrip("[").rstrip("]") for r in hyp.get("evidence_refs", [])]
        refs += [f"E{n}" for n in EVIDENCE_RE.findall(claim)]
        refs = list(dict.fromkeys(refs))

        bad = [r for r in refs if r not in index]
        if bad:
            hyp["_drop_reason"] = f"引用了不存在的证据编号: {', '.join(bad)}"
            dropped.append(hyp)
            continue
        if not refs:
            hyp["_drop_reason"] = "没有任何证据引用"
            dropped.append(hyp)
            continue

        stray = [n for n in NUMBER_RE.findall(claim) if n not in facts_numbers]
        if stray:
            msg = f"claim 中出现 FACTS 未包含的数字 {stray}: {claim[:40]}..."
            if strict_numbers:
                hyp["_drop_reason"] = msg
                dropped.append(hyp)
                continue
            warnings.append(msg)

        hyp["evidence_refs"] = refs
        kept.append(hyp)

    parsed["hypotheses"] = kept

    headline = str(parsed.get("headline", ""))
    stray_head = [n for n in NUMBER_RE.findall(headline) if n not in facts_numbers]
    if stray_head:
        warnings.append(f"headline 中出现 FACTS 未包含的数字 {stray_head}")

    if not kept and not parsed.get("insufficient_evidence"):
        warnings.append("所有假说都被审计丢弃，但模型未声明证据不足")

    return AuditResult(True, parsed, dropped, errors, warnings)


def call_claude(
    facts: FactsBlock,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2000,
    api_key: str | None = None,
) -> str:
    import anthropic

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "未设置 ANTHROPIC_API_KEY。用 --dry-run 只生成 prompt，不调用 API。"
        )
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": facts.render()}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def narrate(
    facts: FactsBlock,
    model: str = DEFAULT_MODEL,
    retries: int = 2,
    strict_numbers: bool = False,
    api_key: str | None = None,
) -> AuditResult:
    last: AuditResult | None = None
    for _ in range(retries + 1):
        raw = call_claude(facts, model=model, api_key=api_key)
        result = audit_response(raw, facts, strict_numbers=strict_numbers)
        if result.ok:
            return result
        last = result
    return last or AuditResult(False, None, errors=["未能获得任何输出"])
