"""Legacy benchmark: evidence ID validity, flagged-claim rate and keyword proxy.

These checks are not scientific citation accuracy, hallucination rate or recall.
Human adjudication is required; use validate.py for frozen-rule review sheets.
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
    n_flagged_hypotheses: int = 0

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
        bad = sum(t.n_flagged_hypotheses for t in self.transitions)
        return round(bad / total, 4)


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
    parts = []
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
    n_refs = n_bad = n_stray = n_flagged = 0
    eligible_claims = []
    for h in narrative.get("hypotheses", []) or []:
        claim = str(h.get("claim", ""))
        refs = [str(r).strip().upper().strip("[]") for r in h.get("evidence_refs", [])]
        refs += [f"E{n}" for n in EVIDENCE_RE.findall(claim)]
        refs = list(dict.fromkeys(refs))
        n_refs += len(refs)
        bad = sum(1 for r in refs if r not in index)
        facts_numbers = set(NUMBER_RE.findall(" ".join(index[r].text for r in refs if r in index)))
        stray = sum(1 for n in NUMBER_RE.findall(EVIDENCE_RE.sub("", claim)) if n not in facts_numbers)
        n_bad += bad
        n_stray += stray
        n_flagged += int(bool(bad or stray or not refs))
        if refs and not bad and not stray and not re.search(r"无法|不能判断|证据不足|未能|不能确定", claim):
            eligible_claims.append(claim)

    text = "\n".join(eligible_claims)
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
        n_flagged_hypotheses=n_flagged,
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
        snapshot = path.with_name(path.name.replace(".narrative.json", ".facts.json"))
        if snapshot.exists():
            from .narrate import Evidence, FactsBlock
            saved = json.loads(snapshot.read_text(encoding="utf-8"))
            saved["evidence"] = [Evidence(**e) for e in saved["evidence"]]
            facts = FactsBlock(**saved)
        else:
            print(f"警告 {path.name}: 无原始 FACTS 快照；旧样例按当前数据重建，证据编号可能变化")
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
            print(f"  Evidence ID validity   {ca:.3f}（仅编号有效率，不代表来源真实或结论有据）")

        hb = ps.automated_hallucination_lower_bound
        if hb is not None:
            print(f"  自动标记假说比例        {hb:.3f}（每条最多计一次；不是人工幻觉率）")
            print("    只检查引用与数字格式；结论是否有据需人工判定。")

        rc = ps.recall
        if rc is None:
            print("  Recall                 无法计分 —— ground truth 未核实 "
                  "(benchmark.yaml 中 verified: false)")
            print(f"    已匹配挑战: {sorted(ps.matched) or '无'} / 共 {len(ps.challenges)} 条")
        else:
            flag = "✓" if rc >= targets.get("recall", 0.6) else "✗"
            print(f"  关键词召回代理指标      {rc:.3f}  {flag} "
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
