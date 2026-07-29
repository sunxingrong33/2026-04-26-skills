"""CSV loading + the provenance gate.

Phase 0 uses hand-curated CSVs instead of the BigQuery/SureChEMBL pipeline, but
it enforces the same auditability contract the production schema will:
``source`` fields are NOT NULL, structures must pass a molecular-formula check,
and any row whose provenance is not a checkable citation is surfaced loudly
rather than silently mixed into the analysis.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from .features import FeatureSet, StructureError, compute_features

# Provenance values that mean "a human can click through and check this".
CHECKABLE_PROVENANCE = {"pubchem", "chembl", "patent", "paper", "drawn_and_checked"}
# Everything else (notably ``unverified``) is usable but flagged everywhere.

REQUIRED_COMPOUND_COLUMNS = (
    "compound_id",
    "compound_name",
    "smiles",
    "expected_formula",
    "structure_source",
    "structure_provenance",
)

REQUIRED_MEMBER_COLUMNS = (
    "program_id",
    "compound_id",
    "generation",
    "patent_number",
    "priority_date",
    "example_ref",
    "source_url",
)


@dataclass
class Compound:
    compound_id: str
    compound_name: str
    smiles: str
    expected_formula: str
    structure_source: str
    structure_provenance: str
    features: FeatureSet

    @property
    def verified(self) -> bool:
        return self.structure_provenance in CHECKABLE_PROVENANCE


@dataclass
class Membership:
    """One compound's appearance in one program generation, with its citation."""

    program_id: str
    compound_id: str
    generation: int
    patent_number: str
    priority_date: date | None
    example_ref: str
    source_url: str
    activity_type: str = ""
    activity_value_nm: float | None = None
    activity_assay: str = ""
    activity_source: str = ""
    activity_provenance: str = "unverified"
    note: str = ""

    @property
    def citation(self) -> str:
        bits = [b for b in (self.patent_number, self.example_ref) if b]
        return " ".join(bits) if bits else "(无引用)"


@dataclass
class Program:
    program_id: str
    program_name: str
    assignee: str
    target: str
    kind: str = "single_assignee"
    note: str = ""


@dataclass
class Dataset:
    programs: dict[str, Program]
    compounds: dict[str, Compound]
    memberships: list[Membership]
    warnings: list[str] = field(default_factory=list)

    def members_of(self, program_id: str) -> list[Membership]:
        return [m for m in self.memberships if m.program_id == program_id]

    def generations(self, program_id: str) -> dict[int, list[Membership]]:
        out: dict[int, list[Membership]] = {}
        for m in self.members_of(program_id):
            out.setdefault(m.generation, []).append(m)
        return dict(sorted(out.items()))

    def unverified_structures(self) -> list[Compound]:
        return [c for c in self.compounds.values() if not c.verified]

    def unverified_activities(self) -> list[Membership]:
        return [
            m
            for m in self.memberships
            if m.activity_value_nm is not None
            and m.activity_provenance not in CHECKABLE_PROVENANCE
        ]


def _require_columns(path: Path, header: Iterable[str], required: Iterable[str]) -> None:
    missing = [c for c in required if c not in set(header)]
    if missing:
        raise ValueError(f"{path.name} 缺少必需列: {', '.join(missing)}")


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def _parse_float(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return float(raw)


def load_dataset(data_dir: str | Path) -> Dataset:
    data_dir = Path(data_dir)
    programs_path = data_dir / "programs.csv"
    compounds_path = data_dir / "compounds.csv"
    members_path = data_dir / "program_members.csv"

    warnings: list[str] = []

    programs: dict[str, Program] = {}
    with programs_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row.get("program_id", "").strip():
                continue
            programs[row["program_id"]] = Program(
                program_id=row["program_id"],
                program_name=row["program_name"],
                assignee=row["assignee"],
                target=row["target"],
                kind=row.get("kind", "single_assignee"),
                note=row.get("note", ""),
            )

    compounds: dict[str, Compound] = {}
    with compounds_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        _require_columns(compounds_path, reader.fieldnames or [], REQUIRED_COMPOUND_COLUMNS)
        for row in reader:
            cid = row.get("compound_id", "").strip()
            if not cid:
                continue
            if not row.get("structure_source", "").strip():
                raise ValueError(f"{cid}: structure_source 为空 —— 可审计字段不允许缺失")
            try:
                feats = compute_features(
                    row["smiles"].strip(), row.get("expected_formula", "").strip() or None
                )
            except StructureError as exc:
                raise StructureError(f"{cid} ({row.get('compound_name','')}): {exc}") from exc
            compounds[cid] = Compound(
                compound_id=cid,
                compound_name=row.get("compound_name", "").strip(),
                smiles=row["smiles"].strip(),
                expected_formula=row.get("expected_formula", "").strip(),
                structure_source=row["structure_source"].strip(),
                structure_provenance=row.get("structure_provenance", "unverified").strip()
                or "unverified",
                features=feats,
            )

    memberships: list[Membership] = []
    with members_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        _require_columns(members_path, reader.fieldnames or [], REQUIRED_MEMBER_COLUMNS)
        for row in reader:
            pid = row.get("program_id", "").strip()
            cid = row.get("compound_id", "").strip()
            if not pid or not cid:
                continue
            if pid not in programs:
                raise ValueError(f"program_members.csv 引用了未知 program_id: {pid}")
            if cid not in compounds:
                raise ValueError(f"program_members.csv 引用了未知 compound_id: {cid}")
            if not row.get("source_url", "").strip():
                raise ValueError(f"{pid}/{cid}: source_url 为空 —— 可审计字段不允许缺失")
            memberships.append(
                Membership(
                    program_id=pid,
                    compound_id=cid,
                    generation=int(row["generation"]),
                    patent_number=row.get("patent_number", "").strip(),
                    priority_date=_parse_date(row.get("priority_date", "")),
                    example_ref=row.get("example_ref", "").strip(),
                    source_url=row["source_url"].strip(),
                    activity_type=row.get("activity_type", "").strip(),
                    activity_value_nm=_parse_float(row.get("activity_value_nm", "")),
                    activity_assay=row.get("activity_assay", "").strip(),
                    activity_source=row.get("activity_source", "").strip(),
                    activity_provenance=row.get("activity_provenance", "unverified").strip()
                    or "unverified",
                    note=row.get("note", "").strip(),
                )
            )

    dataset = Dataset(programs, compounds, memberships, warnings)

    n_unver_struct = len(dataset.unverified_structures())
    if n_unver_struct:
        warnings.append(
            f"{n_unver_struct} 个结构的 provenance 不可核查 (structure_provenance=unverified)。"
            "在给化学家看之前必须逐个核对。"
        )
    n_unver_act = len(dataset.unverified_activities())
    if n_unver_act:
        warnings.append(
            f"{n_unver_act} 条活性数据的 provenance 不可核查。趋势可看，绝对值不可引用。"
        )
    return dataset
