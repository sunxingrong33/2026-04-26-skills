"""§5 评测：Recall / Hallucination rate / Citation accuracy。

    python -m phase0.sar.benchmark --narratives phase0/examples

三个指标的可自动化程度差别很大，这个模块对此不打马虎眼：

* **Citation accuracy** 完全可自动化。引用的 ``[Ex]`` 是否真实存在于 FACTS，
  是个确定性判断。
* **Hallucination rate** 只能自动化一部分。程序能抓到伪造引用和 FACTS 里没有的
  数字，抓不到"引用真实证据但推出无据结论"。所以自动值是**下界**，
  必须配合人工复核；本模块会导出复核工作表。
* **Recall** 依赖 ground truth。ground truth 未经核实时，本模块**拒绝给分**，
  而不是给一个看起来能用的数字。

这三条的排序不是偶然：方案 §5 说 hallucination 是生死线，而它恰恰是最不能
自动测的那个。任何声称"幻觉率 0.03"的自动化数字都应该被怀疑。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .narrate import EVIDENCE_RE, NUMBER_RE

NARRATIVE_RE = re.compile(r"^(?P<program>.+)_gen(?P<a>\d+)-(?P<b>\d+)\.narrative\.json$")


@dataclass
class TransitionScore:
    program_id: str
    gen_from: int
    gen_to: int
    n_hypotheses: int
    n_refs: int
    n_bad_refs: int
    n_stray_numbers: int
    matched_challenges: set[str] = field(default_factory=set)

    @property
    def citation_accuracy(self) -> float | None:
        if not self.n_refs:
            return None
        return round(1 - self.n_bad_refs / self.n_refs, 4)


@dataclass
class ProgramScore:
    program_id: str
    verified_ground_truth: bool
    challenges: list[dict[str, Any]]
    transitions: list[TransitionScore] = field(default_factory=list)

    @property
    def matched(self) -> set[str]:
        out: set[str] = set()
        for t in self.transitions:
            out |= t.matched_challenges
        return out

    @property
    def recall(self) -> float | None:
        """None when the ground truth has not been verified against source text."""
        if not self.verified_ground_truth or not self.challenges:
            return None
        return round(len(self.matched) / len(self.challenges), 4)

    @property
    def citation_accuracy(self) -> float | None:
        refs = sum(t.n_refs for t in self.transitions)
        bad = sum(t.n_bad_refs for t in self.transitions)
        if not refs:
            return None
        return round(1 - bad / refs, 4)

    @property
    def automated_hallucination_lower_bound(self) -> float | None:
        total = sum(t.n_hypotheses for t in self.transitions)
        if not total:
            return None
        bad = sum(1 for t in self.transitions for _ in range(t.n_bad_refs + t.n_stray_numbers))
        return round(min(bad / total, 1.0), 4)


def load_benchmark(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _claim_text(narrative: dict[str, Any]) -> str:
    """Text that counts towards recall: assertions only.

    ``what_we_cannot_tell`` is deliberately excluded. "无法区分动机是效价还是渗透性"
    contains the keyword 渗透 but is the tool declining to identify the challenge --
    counting it as a hit inflates recall with the tool's own disclaimers, which is
    the self-fulfilling failure mode the benchmark exists to avoid.
    """
    parts = [str(narrative.get("headline", ""))]
    for h in narrative.get("hypotheses", []) or []:
        parts.append(str(h.get("claim", "")))
    return "\n".join(parts)


def score_transition(
    program_id: str,
    gen_from: int,
    gen_to: int,
    narrative: dict[str, Any],
    facts,
    challenges: list[dict[str, Any]],
) -> TransitionScore:
    index = facts.index
    facts_numbers = set(NUMBER_RE.findall(facts.render()))

    n_refs = n_bad = n_stray = 0
    for h in narrative.get("hypotheses", []) or []:
        claim = str(h.get("claim", ""))
        refs = [str(r).strip().upper() for r in h.get("evidence_refs", [])]
        refs += [f"E{n}" for n in EVIDENCE_RE.findall(claim)]
        refs = list(dict.fromkeys(refs))
        n_refs += len(refs)
        n_bad += sum(1 for r in refs if r not in index)
        n_stray += sum(1 for n in NUMBER_RE.findall(claim) if n not in facts_numbers)

    text = _claim_text(narrative)
    matched = {
        c["id"]
        for c in challenges
        if any(kw in text for kw in c.get("keywords", []))
    }

    return TransitionScore(
        program_id=program_id,
        gen_from=gen_from,
        gen_to=gen_to,
        n_hypotheses=len(narrative.get("hypotheses", []) or []),
        n_refs=n_refs,
        n_bad_refs=n_bad,
        n_stray_numbers=n_stray,
        matched_challenges=matched,
    )


def _facts_for(dataset, rules_doc, program_id: str, gen_from: int, gen_to: int):
    from .deltas import compute_program_deltas
    from .narrate import build_facts
    from .rules import evaluate

    program = dataset.programs[program_id]
    _, deltas = compute_program_deltas(dataset, program_id, run_mcs=False)
    for d in deltas:
        if d.gen_from == gen_from and d.gen_to == gen_to:
            caveats = list(dataset.warnings)
            if program.kind == "cross_assignee":
                caveats.append(
                    "本『程序』跨申请人聚合，代际之间不代表同一支团队的连续决策，"
                    "只能读作领域层面的演化"
                )
            return build_facts(
                program.program_name, d, evaluate(d, rules_doc), caveats
            )
    return None


def run(narratives_dir: Path, data_dir: Path, review_out: Path | None) -> int:
    from .rules import load_rules
    from .schema import load_dataset

    bench = load_benchmark(data_dir / "benchmark.yaml")
    dataset = load_dataset(data_dir)
    rules_doc = load_rules()

    by_program = {p["program_id"]: p for p in bench["programs"]}
    scores: dict[str, ProgramScore] = {}
    review_rows: list[dict[str, str]] = []

    for path in sorted(narratives_dir.glob("*.narrative.json")):
        m = NARRATIVE_RE.match(path.name)
        if not m:
            continue
        pid = m.group("program")
        if pid not in by_program:
            print(f"跳过 {path.name}: benchmark.yaml 里没有 {pid}")
            continue
        gen_from, gen_to = int(m.group("a")), int(m.group("b"))
        facts = _facts_for(dataset, rules_doc, pid, gen_from, gen_to)
        if facts is None:
            print(f"跳过 {path.name}: 数据里没有 Gen{gen_from}→Gen{gen_to}")
            continue

        narrative = json.loads(path.read_text(encoding="utf-8"))
        cfg = by_program[pid]
        ts = score_transition(
            pid, gen_from, gen_to, narrative, facts, cfg.get("challenges", [])
        )
        scores.setdefault(
            pid,
            ProgramScore(pid, bool(cfg.get("verified")), cfg.get("challenges", [])),
        ).transitions.append(ts)

        for h in narrative.get("hypotheses", []) or []:
            review_rows.append(
                {
                    "program_id": pid,
                    "transition": f"Gen{gen_from}->Gen{gen_to}",
                    "claim": str(h.get("claim", "")),
                    "confidence": str(h.get("confidence", "")),
                    "evidence_refs": ",".join(h.get("evidence_refs", [])),
                    "evidence_text": " | ".join(
                        facts.index[r].text
                        for r in h.get("evidence_refs", [])
                        if r in facts.index
                    ),
                    "人工判定_有据": "",
                    "备注": "",
                }
            )

    targets = bench.get("targets", {})
    bar = "=" * 78
    print(bar)
    print("§5 评测结果")
    print(bar)

    for pid, ps in sorted(scores.items()):
        print(f"\n■ {pid}   转换 {len(ps.transitions)} 个")
        ca = ps.citation_accuracy
        if ca is not None:
            flag = "✓" if ca >= targets.get("citation_accuracy", 0.95) else "✗"
            print(f"  Citation accuracy      {ca:.3f}  {flag} (目标 ≥ {targets.get('citation_accuracy')})")

        hb = ps.automated_hallucination_lower_bound
        if hb is not None:
            print(f"  Hallucination 自动下界  {hb:.3f}     (目标 ≤ {targets.get('hallucination_rate')})")
            print("    ⚠ 这是下界，只覆盖伪造引用与凭空数字。"
                  "『引用真实证据但结论无据』只能人工判，见复核工作表。")

        rc = ps.recall
        if rc is None:
            print("  Recall                 无法计分 —— ground truth 未核实 "
                  "(benchmark.yaml 中 verified: false)")
            print(f"    已匹配挑战: {sorted(ps.matched) or '无'} / 共 {len(ps.challenges)} 条")
        else:
            flag = "✓" if rc >= targets.get("recall", 0.6) else "✗"
            print(f"  Recall                 {rc:.3f}  {flag} "
                  f"({sorted(ps.matched)} / {len(ps.challenges)})")

    if review_out and review_rows:
        review_out.parent.mkdir(parents=True, exist_ok=True)
        with review_out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(review_rows[0]))
            w.writeheader()
            w.writerows(review_rows)
        print(f"\n人工复核工作表已写入 {review_out}  ({len(review_rows)} 条待判)")
        print("  填 `人工判定_有据` 列（是/否），再算真实 hallucination rate。")

    print()
    print(bar)
    return 0


def main(argv: list[str] | None = None) -> int:
    from .cli import DEFAULT_DATA, REPO_ROOT

    ap = argparse.ArgumentParser(description="§5 评测集打分")
    ap.add_argument("--narratives", default=str(REPO_ROOT / "phase0" / "examples"))
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--review-out", default=str(REPO_ROOT / "phase0" / "out" / "review.csv"))
    args = ap.parse_args(argv)
    return run(Path(args.narratives), Path(args.data), Path(args.review_out))


if __name__ == "__main__":
    raise SystemExit(main())
