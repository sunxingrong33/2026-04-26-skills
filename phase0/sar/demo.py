"""Reproduce the offline evidence explorer and frozen-rule validation."""
from pathlib import Path

from .validate import DEFAULT_CASES, run
from .report import ROOT, build


def main():
    result = run(DEFAULT_CASES, ROOT / "artifacts" / "validation")
    for case in result["cases"]:
        print(f"{case['case_id']}: {case['n_candidates']} candidates; "
              f"keyword proxy {len(case['keyword_matches_only'])}/{case['n_objectives']}; "
              "human validation pending")
    print(build(ROOT / "artifacts" / "sar-explorer.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
