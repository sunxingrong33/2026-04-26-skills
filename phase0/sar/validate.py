"""Frozen-rule validation, immutable FACTS, and explicit human adjudication.

The offline run evaluates deterministic rule candidates, not LLM performance.
Program-objective keywords are a diagnostic proxy only. Scientific recall and
unsupported-claim rate remain null until the corresponding human sheets are
fully reviewed. Re-running preserves matching reviews and archives stale sheets.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

from .deltas import compute_program_deltas
from .ingest import write_csv
from .narrate import audit_response, build_facts
from .rules import DEFAULT_RULES_PATH, evaluate, load_rules
from .schema import Dataset, load_dataset

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "data" / "validation_cases.json"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def pair_dataset(ds, pid, from_id, to_id):
    members = ds.members_of(pid)
    a = next(m for m in members if m.compound_id == from_id)
    b = next(m for m in members if m.compound_id == to_id)
    return Dataset(ds.programs, ds.compounds, [replace(a, generation=1), replace(b, generation=2)],
                   ds.warnings + ["这是选定分子对照，不证明直接的历史优化步骤。"], ds.measurements)


def candidates(facts, hyps):
    rows = []
    for h in hyps:
        refs = [e.eid for e in facts.evidence if set(h.features_used) & set(e.features)]
        rows.append({"claim": f"结构变化提示候选方向：{h.name}；尚未证实其研发意图。",
                     "confidence": "low", "evidence_refs": refs, "rule_id": h.rule_id})
    raw = json.dumps({"headline": "候选假说", "hypotheses": rows,
                      "what_we_cannot_tell": "", "insufficient_evidence": not rows}, ensure_ascii=False)
    result = audit_response(raw, facts, strict_numbers=True)
    if not result.ok:
        raise ValueError(result.errors)
    return result.parsed


def read_review(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if len({r["review_id"] for r in rows}) != len(rows):
        raise ValueError(f"Duplicate review IDs: {path}")
    return {r["review_id"]: r for r in rows}


def review_metric(rows, reviews, value_field, positive):
    assessed, passed = 0, 0
    for row in rows:
        r = reviews.get(row["review_id"], {})
        value = r.get(value_field, "")
        if value not in ("yes", "no") or not r.get("reviewer", "").strip() or not r.get("rationale", "").strip():
            continue
        assessed += 1
        passed += value == positive
    complete = bool(rows) and assessed == len(rows)
    return {"reviewed": assessed, "total": len(rows), "complete": complete,
            "value": passed / len(rows) if complete else None}


def run(cases_path: Path, out: Path):
    config = json.loads(cases_path.read_text(encoding="utf-8"))
    rules = load_rules()
    rules_hash = hashlib.sha256(DEFAULT_RULES_PATH.read_text(encoding="utf-8").encode()).hexdigest()
    baseline = cases_path.with_name("validation_baseline.json")
    baseline_data = json.loads(baseline.read_text(encoding="utf-8"))
    if rules_hash != baseline_data["rules_sha256"]:
        raise ValueError("规则已改变：此留出集不能继续宣称未用于调参，请建立新版本验证协议。")
    out.mkdir(parents=True, exist_ok=True)
    reports, claim_rows, challenge_rows = [], [], []
    split_ids = {"development": set(), "holdout": set()}
    for case in config["cases"]:
        data_path = cases_path.parent / case["data"]
        ds = load_dataset(data_path)
        split_ids[case["split"]].update(ds.compounds)
        pair = pair_dataset(ds, case["program_id"], case["from"], case["to"])
        _, deltas = compute_program_deltas(pair, case["program_id"], run_mcs=False)
        delta = deltas[0]
        hyps = evaluate(delta, rules)
        facts = build_facts(ds.programs[case["program_id"]].program_name, delta, hyps, pair.warnings)
        narrative = candidates(facts, hyps)
        snapshot = {"case": case, "rules_sha256": rules_hash, "facts": asdict(facts),
                    "data_sha256": {p.name: hashlib.sha256(p.read_text(encoding="utf-8").encode()).hexdigest()
                                    for p in sorted(data_path.glob("*.csv"))}}
        run_id = digest(snapshot)
        snapshot["run_id"] = run_id
        (out / f"{case['id']}.facts.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / f"{case['id']}.narrative.json").write_text(json.dumps(narrative, ensure_ascii=False, indent=2), encoding="utf-8")
        claims = narrative["hypotheses"]
        for h in claims:
            claim_rows.append(dict(review_id=digest([run_id, h]), case_id=case["id"], split=case["split"],
                claim=h["claim"], rule_id=h["rule_id"], evidence_refs=";".join(h["evidence_refs"]),
                evidence_text=" | ".join(facts.index[r].text for r in h["evidence_refs"]),
                source_urls=" | ".join(sorted({u for r in h["evidence_refs"] for u in facts.index[r].source_urls})),
                supported="", reviewer="", rationale=""))
        for c in case["challenges"]:
            challenge_rows.append(dict(review_id=digest([run_id, c]), case_id=case["id"],
                challenge_id=c["id"], objective=c["name"], source_url=case["source_url"],
                locator=case["locator"], candidate_claims=" | ".join(h["claim"] for h in claims),
                recovered="", reviewer="", rationale=""))
        proxy = [c["id"] for c in case["challenges"] if any(
            word in h["claim"] for h in claims for word in c["keywords"])]
        reports.append({"case_id": case["id"], "split": case["split"], "n_candidates": len(claims),
                        "keyword_matches_only": proxy, "n_objectives": len(case["challenges"]), "run_id": run_id})
    if split_ids["development"] & split_ids["holdout"]:
        raise ValueError("Compound leakage between development and holdout")
    for filename, rows in (("claims_review.csv", claim_rows), ("objectives_review.csv", challenge_rows)):
        path = out / filename
        previous = read_review(path)
        if previous and set(previous) != {r["review_id"] for r in rows}:
            archive = path.with_name(path.stem + "." + digest(previous)[:12] + ".archived.csv")
            archive.write_bytes(path.read_bytes())
        fields = ("supported", "reviewer", "rationale") if filename.startswith("claims") else ("recovered", "reviewer", "rationale")
        for row in rows:
            if row["review_id"] in previous:
                for field in fields:
                    row[field] = previous[row["review_id"]].get(field, "")
        if rows:
            write_csv(path, list(rows[0]), rows)
    cr, gr = read_review(out / "claims_review.csv"), read_review(out / "objectives_review.csv")
    for report in reports:
        pid = report["case_id"]
        report["human_unsupported_claim_rate"] = review_metric([r for r in claim_rows if r["case_id"] == pid], cr, "supported", "no")
        report["human_objective_recall"] = review_metric([r for r in challenge_rows if r["case_id"] == pid], gr, "recovered", "yes")
    result = {"mode": "deterministic_rule_candidates_no_LLM", "rules_sha256": rules_hash,
              "scope": config["scope"], "cases": reports,
              "release_ready": False, "release_note": "需要独立化学家复核及更大规模外部验证；自动关键词命中不作为验收。"}
    (out / "validation_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    ap.add_argument("--out", type=Path, default=Path("artifacts/validation"))
    args = ap.parse_args(argv)
    print(json.dumps(run(args.cases, args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
