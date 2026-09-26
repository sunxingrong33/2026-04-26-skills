"""Phase 0 runner.

    python -m phase0.sar.cli --program pfizer-alk --dry-run

Runs the whole deterministic chain, prints a terminal timeline, and either
writes the FACTS prompt to disk (--dry-run) or calls Claude and audits what
comes back.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

from .deltas import GenerationDelta, compute_program_deltas
from .narrate import DEFAULT_MODEL, build_facts, narrate
from .rules import Hypothesis, evaluate, load_rules
from .schema import Dataset, load_dataset

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = REPO_ROOT / "phase0" / "data"
DEFAULT_OUT = REPO_ROOT / "phase0" / "out"

BAR = "=" * 78
SUB = "-" * 78


def _print_provenance_banner(dataset: Dataset) -> None:
    if not dataset.warnings:
        return
    print(BAR)
    print("!! 数据可信度警告 —— 在给化学家看之前必须处理 !!")
    for w in dataset.warnings:
        print(f"   - {w}")
    unver = dataset.unverified_structures()
    if unver:
        print("   未核实结构:")
        for c in unver:
            print(f"     · {c.compound_id:14s} {c.features.formula:18s} {c.structure_source}")
    print(BAR)
    print()


def _print_timeline(dataset: Dataset, program_id: str, summaries, deltas) -> None:
    prog = dataset.programs[program_id]
    print(BAR)
    print(f"程序: {prog.program_name}")
    print(f"申请人: {prog.assignee}    靶点: {prog.target}    类型: {prog.kind}")
    if prog.note:
        print(f"备注: {prog.note}")
    print(BAR)

    for s in summaries:
        names = ", ".join(
            dataset.compounds[m.compound_id].compound_name for m in s.members
        )
        print(f"\n■ Gen{s.generation}  优先权 {s.earliest_priority or '未知'}  n={s.n_compounds}")
        print(f"  化合物: {names}")
        print(f"  专利:   {s.patent_label}")
        keys = ("mw", "clogp", "tpsa", "hbd", "fsp3", "rotb", "f_count",
                "strong_basic_amine_count", "max_ring_size")
        stats = "  ".join(f"{k}={s.numeric[k]:g}" for k in keys if k in s.numeric)
        print(f"  属性中位: {stats}")
        flags = [k for k, v in s.boolean_fraction.items() if v > 0]
        if flags:
            print(f"  标志:   {', '.join(f'{k}={s.boolean_fraction[k]:.0%}' for k in flags)}")
        if s.activity_median_nm is not None:
            print(
                f"  活性中位: {s.activity_median_nm:g} nM (n={s.activity_n}, "
                f"assay: {'/'.join(s.activity_assays) or '未标注'})"
            )
        for mtype, agg in s.measures.items():
            unit = f" {agg.unit}" if agg.unit else ""
            mark = "" if agg.all_verified else "  [未核实]"
            print(f"  {mtype}: 中位 {agg.median:g}{unit} (n={agg.n}){mark}")


def _print_delta(delta: GenerationDelta, hypotheses: list[Hypothesis]) -> None:
    print()
    print(SUB)
    print(f"▶ Gen{delta.gen_from} → Gen{delta.gen_to}"
          + (f"   （相隔 {delta.years_elapsed} 年）" if delta.years_elapsed is not None else "")
          + f"   n_support={delta.n_support}")
    print(SUB)

    if delta.alignment:
        al = delta.alignment
        print(f"  骨架判定: {al.label}  环系 Jaccard {al.ring_jaccard:.2f}")
        print(f"            {al.describe()}")
        if al.mcs_num_atoms:
            print(f"            (参考: MCS {al.mcs_num_atoms} 重原子, 覆盖率 {al.mcs_coverage:.2f}，仅描述性)")
        for note in al.notes:
            print(f"            ! {note}")

    moved = [
        fd for fd in delta.features.values()
        if fd.kind == "numeric" and abs(fd.delta) > 1e-9
    ]
    moved.sort(key=lambda fd: abs(fd.delta) / (abs(fd.val_from) or 1), reverse=True)
    print("  属性变化 (中位):")
    for fd in moved[:12]:
        print(f"    {fd.label:20s} {fd.val_from:>8g} → {fd.val_to:<8g} Δ {fd.delta:+g}")

    for fd in delta.features.values():
        if fd.kind == "boolean" and fd.transition:
            print(f"    {fd.label:20s} {fd.transition}")

    if delta.activity_ratio is not None:
        flag = "" if delta.activity_comparable else "  [跨 assay，仅供趋势参考]"
        print(f"  活性: 后/前比值 {delta.activity_ratio:g}{flag}")
    elif delta.activity_note:
        print(f"  活性: {delta.activity_note}")

    for mtype, md in delta.measures.items():
        unit = f" {md.unit}" if md.unit else ""
        flag = "" if md.comparable else "  [assay 不同，仅趋势]"
        print(
            f"  {mtype}: {md.val_from:g}{unit} → {md.val_to:g}{unit} "
            f"(Δ {md.delta:+g}{unit}, n={md.n_support}){flag}"
        )

    print(f"  规则命中: {len(hypotheses)} 条")
    for h in hypotheses:
        print(f"    [{h.confidence:.2f}] {h.rule_id} — {h.name}")
        for mc in h.matched:
            print(f"        · {mc.text}")
        for p in h.penalties:
            print(f"        ! {p}")


def _rel(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise (--out may point anywhere)."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _serialize(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(_serialize(v) for v in obj)
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def run(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.data)
    rules_doc = load_rules(args.rules)

    _print_provenance_banner(dataset)

    program_ids = [args.program] if args.program else list(dataset.programs)
    for pid in program_ids:
        if pid not in dataset.programs:
            print(f"未知 program_id: {pid}. 可选: {', '.join(dataset.programs)}", file=sys.stderr)
            return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = False

    for pid in program_ids:
        summaries, deltas = compute_program_deltas(dataset, pid, run_mcs=not args.no_mcs)
        _print_timeline(dataset, pid, summaries, deltas)

        program = dataset.programs[pid]
        bundle: dict[str, Any] = {
            "program": _serialize(program),
            "data_warnings": dataset.warnings,
            "generations": [_serialize(s) for s in summaries],
            "transitions": [],
        }

        for delta in deltas:
            hyps = evaluate(delta, rules_doc)
            _print_delta(delta, hyps)

            caveats = list(dataset.warnings)
            if program.kind == "cross_assignee":
                caveats.append(
                    "本『程序』跨申请人聚合，代际之间不代表同一支团队的连续决策，"
                    "只能读作领域层面的演化"
                )
            facts = build_facts(program.program_name, delta, hyps, caveats)

            pair = f"{pid}_gen{delta.gen_from}-{delta.gen_to}"
            prompt_path = out_dir / f"{pair}.prompt.txt"
            prompt_path.write_text(facts.render(), encoding="utf-8")
            (out_dir / f"{pair}.facts.json").write_text(
                json.dumps(_serialize(facts), ensure_ascii=False, indent=2), encoding="utf-8")

            entry: dict[str, Any] = {
                "gen_from": delta.gen_from,
                "gen_to": delta.gen_to,
                "n_support": delta.n_support,
                "alignment": _serialize(delta.alignment),
                "activity_ratio": delta.activity_ratio,
                "activity_comparable": delta.activity_comparable,
                "activity_note": delta.activity_note,
                "feature_deltas": _serialize(delta.features),
                "measure_deltas": _serialize(delta.measures),
                "rule_hits": _serialize(hyps),
                "facts_prompt_path": _rel(prompt_path),
                "evidence": [_serialize(e) for e in facts.evidence],
            }

            if args.dry_run:
                print(f"  [dry-run] FACTS 已写入 {_rel(prompt_path)}")
            else:
                audit = narrate(facts, model=args.model, strict_numbers=args.strict_numbers)
                entry["narrative"] = audit.parsed
                entry["audit"] = {
                    "ok": audit.ok,
                    "dropped": audit.dropped,
                    "errors": audit.errors,
                    "warnings": audit.warnings,
                }
                _print_narrative(audit)
                failed = failed or not audit.ok
                if audit.ok:
                    (out_dir / f"{pair}.narrative.json").write_text(
                        json.dumps(audit.parsed, ensure_ascii=False, indent=2), encoding="utf-8")

            bundle["transitions"].append(entry)

        bundle_path = out_dir / f"{pid}.json"
        bundle_path.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结构化结果已写入 {_rel(bundle_path)}")

    return 1 if failed else 0


def _print_narrative(audit) -> None:
    print()
    if not audit.ok or audit.parsed is None:
        print("  叙述层失败:")
        for e in audit.errors:
            print(f"    ! {e}")
        return
    p = audit.parsed
    print(f"  ▷ {p.get('headline', '')}")
    for h in p.get("hypotheses", []):
        refs = "".join(f"[{r}]" for r in h.get("evidence_refs", []))
        print(f"    · ({h.get('confidence')}) {h.get('claim')} {refs}")
    if p.get("what_we_cannot_tell"):
        print(f"    无法判断: {p['what_we_cannot_tell']}")
    for d in audit.dropped:
        print(f"    ✂ 已丢弃: {d.get('claim','')[:60]}... 原因: {d.get('_drop_reason')}")
    for w in audit.warnings:
        print(f"    ! {w}")


def main(argv: list[str] | None = None) -> int:
    # Windows redirected streams may default to GBK, which cannot encode the
    # report's symbols. Emit a stable UTF-8 stream for terminals and saved logs.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="竞对 SAR 演化时间线 — 阶段 0 验证管道")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="CSV 数据目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    ap.add_argument("--program", help="只跑一个 program_id，默认全跑")
    ap.add_argument("--rules", help="规则库路径，默认 sar/rules.yaml")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"叙述层模型，默认 {DEFAULT_MODEL}")
    ap.add_argument("--dry-run", action="store_true", help="只生成 FACTS prompt，不调用 API")
    ap.add_argument("--strict-numbers", action="store_true", default=True,
                    help="叙述中出现 FACTS 之外的数字时直接丢弃该假说，而不是仅警告")
    ap.add_argument("--no-mcs", action="store_true", help="跳过 MCS 骨架对齐")
    args = ap.parse_args(argv)

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("未设置 ANTHROPIC_API_KEY，自动切换到 --dry-run。", file=sys.stderr)
        args.dry_run = True

    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
