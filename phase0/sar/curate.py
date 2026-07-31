"""Data-entry helper for hand-curated rows.

    python -m phase0.sar.curate --check            # 校验 + 报告缺什么
    python -m phase0.sar.curate --fill-formula     # 回写 expected_formula

The point of this module is that adding a generation should be typing SMILES and
nothing else. It derives what can be derived (molecular formula, MW, ring count,
macrocycle flag), and it is loud about what only a human can supply (whether the
structure is actually the compound the paper drew, and what the example number
is).

It deliberately does NOT mark anything as verified. Provenance is a human
assertion; a script cannot upgrade ``unverified`` to ``pubchem``.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors

from .features import ALLOWED_ELEMENTS, StructureError, compute_features
from .schema import CHECKABLE_PROVENANCE

PLACEHOLDERS = {"待补", "TODO", "todo", "?", "??", "-", "n/a", "N/A", ""}

def _is_blank(v: str) -> bool:
    return (v or "").strip() in PLACEHOLDERS


def exotic_elements(mol: Chem.Mol) -> list[str]:
    """Elements outside the small-molecule whitelist, e.g. a [Nh] typo for [nH]."""
    return sorted(
        {a.GetSymbol() for a in mol.GetAtoms() if a.GetSymbol() not in ALLOWED_ELEMENTS}
    )


def core_ring_report(data_dir: Path) -> list[str]:
    """Flag compounds missing their programme's consensus core ring systems.

    Motivation: a discovery paper's SI numbers its *synthetic* intermediates
    (protected esters, halide coupling precursors) alongside the tested analogues,
    and it is very easy to curate the wrong ones. Those building blocks lack the
    pharmacophore -- here, the aminopyridine hinge binder -- while still sharing
    the peripheral rings, so a plain ring-overlap test does not catch them.

    Comparing against the consensus core of the programme's *already verified*
    compounds does. This is advisory, not fatal: a genuine core hop looks the
    same, and telling those apart is a human judgement.
    """
    from .align import GENERIC_RINGS, consensus_ring_set, ring_systems

    compounds: dict[str, str] = {}
    with (data_dir / "compounds.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cid = row.get("compound_id", "").strip()
            smi = row.get("smiles", "").strip()
            if cid and not _is_blank(smi):
                compounds[cid] = smi

    members: dict[str, list[str]] = {}
    with (data_dir / "program_members.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pid, cid = row.get("program_id", "").strip(), row.get("compound_id", "").strip()
            if pid and cid:
                members.setdefault(pid, []).append(cid)

    todos: list[str] = []
    for pid, cids in sorted(members.items()):
        mols = {}
        for cid in cids:
            if cid in compounds:
                m = Chem.MolFromSmiles(compounds[cid])
                if m is not None:
                    mols[cid] = m
        if len(mols) < 2:
            continue

        core = consensus_ring_set(list(mols.values())) - GENERIC_RINGS
        if not core:
            continue
        print(f"  {pid}  核心环系: {'、'.join(sorted(core))}")
        for cid, m in mols.items():
            missing = core - ring_systems(m)
            if missing:
                print(
                    f"    ! {cid:14s} 缺少核心环系: {'、'.join(sorted(missing))}"
                    "  —— 可能录成了合成中间体，也可能是真实骨架跃迁，需人工判断"
                )
                todos.append(f"{cid}: 缺少 {pid} 的核心环系 {'、'.join(sorted(missing))}")
    return todos


def check(data_dir: Path) -> int:
    """Row-by-row report. Returns the number of hard problems found."""
    compounds_path = data_dir / "compounds.csv"
    members_path = data_dir / "program_members.csv"

    problems = 0
    todos: list[str] = []

    print("=" * 78)
    print("结构校验")
    print("=" * 78)

    known: set[str] = set()
    with compounds_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cid = row.get("compound_id", "").strip()
            if not cid:
                continue
            known.add(cid)
            smiles = row.get("smiles", "").strip()
            declared = row.get("expected_formula", "").strip()

            # Not an error: a skeleton row waiting to be filled. The loader skips
            # these, so a half-curated dataset still runs.
            if _is_blank(smiles):
                hint = row.get("note", "").strip()
                print(f"  … {cid:22s} 待填 SMILES" + (f"  ({hint})" if hint else ""))
                todos.append(f"{cid}: 填 SMILES —— 只有这一列必须由人提供")
                continue

            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                print(f"  ✗ {cid:22s} SMILES 无法解析")
                problems += 1
                continue

            exotic = exotic_elements(mol)
            if exotic:
                print(
                    f"  ✗ {cid:22s} 含非常规元素 {exotic} —— "
                    "多半是 SMILES 打错。如 [Nh] 被当成鉨/113号元素；N-H 在环内芳香氮写 [nH]，"
                    "环外胺氮直接写 N"
                )
                problems += 1
                continue

            formula = rdMolDescriptors.CalcMolFormula(mol)
            mw = Descriptors.MolWt(mol)

            if _is_blank(declared):
                print(
                    f"  ? {cid:22s} {formula:20s} MW {mw:7.2f}  "
                    f"expected_formula 未填（--fill-formula 可回写）"
                )
                todos.append(f"{cid}: 填 expected_formula = {formula}")
            elif declared != formula:
                print(
                    f"  ✗ {cid:22s} 分子式不一致: SMILES={formula} 声明={declared}"
                )
                problems += 1
                continue
            else:
                feats = compute_features(smiles, declared)
                flags = []
                if feats.boolean["has_macrocycle"]:
                    flags.append(f"大环({int(feats.numeric['max_ring_size'])}元)")
                if feats.boolean["has_warhead"]:
                    flags.append("共价弹头:" + "/".join(feats.warhead_types))
                if feats.numeric["chiral_centers_total"] > feats.numeric[
                    "chiral_centers_defined"
                ]:
                    flags.append("有未定义手性中心")
                if feats.synthetic_handles:
                    flags.append("⚠ " + "/".join(feats.synthetic_handles))
                    todos.append(
                        f"{cid}: 含 {'/'.join(feats.synthetic_handles)}，"
                        "确认是受测类似物而非合成砌块"
                    )
                extra = ("  " + ", ".join(flags)) if flags else ""
                print(f"  ✓ {cid:22s} {formula:20s} MW {mw:7.2f}{extra}")

            if row.get("structure_provenance", "").strip() not in CHECKABLE_PROVENANCE:
                todos.append(f"{cid}: 核对结构后把 structure_provenance 改掉")

    print()
    print("=" * 78)
    print("引用完整性")
    print("=" * 78)

    with members_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    for row in rows:
        pid = row.get("program_id", "").strip()
        cid = row.get("compound_id", "").strip()
        if not pid or not cid:
            continue
        tag = f"{pid}/{cid}"
        if cid not in known:
            print(f"  ✗ {tag:34s} compounds.csv 里没有这个 compound_id")
            problems += 1
            continue
        missing = [
            col
            for col in ("patent_number", "priority_date", "example_ref", "source_url")
            if _is_blank(row.get(col, ""))
        ]
        if missing:
            print(f"  ? {tag:34s} 待补: {', '.join(missing)}")
            todos.append(f"{tag}: 补 {', '.join(missing)}")
        else:
            print(f"  ✓ {tag:34s} {row['patent_number']} {row['example_ref']}")

    # generation continuity -- a gap usually means a renumbering was forgotten
    print()
    print("=" * 78)
    print("代际连续性")
    print("=" * 78)
    by_prog: dict[str, list[int]] = {}
    for row in rows:
        pid = row.get("program_id", "").strip()
        if pid and row.get("generation", "").strip().isdigit():
            by_prog.setdefault(pid, []).append(int(row["generation"]))
    for pid, gens in sorted(by_prog.items()):
        uniq = sorted(set(gens))
        counts = {g: gens.count(g) for g in uniq}
        gap = [g for g in range(min(uniq), max(uniq)) if g not in set(uniq)]
        line = "  ".join(f"Gen{g}(n={counts[g]})" for g in uniq)
        print(f"  {pid:20s} {line}")
        if gap:
            print(f"    ! 代际编号有空档: {gap} —— 插入中间代后是否忘了重新编号？")
            todos.append(f"{pid}: 代际编号空档 {gap}")
        singles = [g for g in uniq if counts[g] == 1]
        if singles:
            print(
                f"    ! Gen{singles} 只有 1 个化合物，中位值等同单点值，"
                "置信度会被打折"
            )

    print()
    print("=" * 78)
    print("程序核心环系一致性")
    print("=" * 78)
    todos += core_ring_report(data_dir)

    print()
    print("=" * 78)
    if problems:
        print(f"硬问题 {problems} 个 —— 管道会拒绝加载，必须先修")
    else:
        print("硬问题 0 个 —— 管道可以加载")
    if todos:
        print(f"\n待办 {len(todos)} 项:")
        for t in todos:
            print(f"  - {t}")
    print("=" * 78)
    return problems


def fill_formula(data_dir: Path) -> int:
    """Write back expected_formula for rows that left it blank."""
    path = data_dir / "compounds.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = list(reader)

    filled = 0
    for row in rows:
        if not _is_blank(row.get("expected_formula", "")):
            continue
        smiles = row.get("smiles", "").strip()
        if _is_blank(smiles):
            continue
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            continue
        row["expected_formula"] = rdMolDescriptors.CalcMolFormula(mol)
        filled += 1
        print(f"  {row['compound_id']}: expected_formula = {row['expected_formula']}")

    if filled:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(f"回写 {filled} 行。")
    print(
        "注意: 回写的分子式来自你给的 SMILES，所以它只能保证"
        "『SMILES 和分子式自洽』，不能证明结构画对了。请再对着原文核一遍 MW。"
    )
    return filled


def main(argv: list[str] | None = None) -> int:
    from .cli import DEFAULT_DATA

    ap = argparse.ArgumentParser(description="录数据的校验与自动补全工具")
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--check", action="store_true", help="校验并报告（默认动作）")
    ap.add_argument("--fill-formula", action="store_true", help="回写 expected_formula")
    args = ap.parse_args(argv)

    data_dir = Path(args.data)
    if args.fill_formula:
        fill_formula(data_dir)
        print()
    try:
        problems = check(data_dir)
    except StructureError as exc:
        print(f"结构错误: {exc}")
        return 1
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
