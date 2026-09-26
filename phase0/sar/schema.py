"""CSV loading + the provenance gate.

Phase 0 uses hand-curated CSVs instead of the BigQuery/SureChEMBL pipeline, but
it enforces the same auditability contract the production schema will:
``source`` fields are NOT NULL, structures must pass a molecular-formula check,
and any row whose provenance is not a checkable citation is surfaced loudly
rather than silently mixed into the analysis.
"""

from __future__ import annotations

import csv
import math
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from .features import FeatureSet, StructureError, compute_features

# Provenance values that mean "a human can click through and check this".
CHECKABLE_PROVENANCE = {"pubchem", "chembl", "patent", "paper", "drawn_and_checked"}
# Everything else (notably ``unverified``) is usable but flagged everywhere.

# A skeleton row whose SMILES has not been filled in yet. Such rows are skipped
# with a warning rather than raising, so a half-curated dataset still runs and
# the curation tool can report what is left. Anything else that fails to parse is
# still a hard error -- this is a placeholder allowance, not a lenient parser.
PLACEHOLDER_SMILES = {"", "待补", "TODO", "todo", "<SMILES>", "?", "-"}

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
class Measurement:
    """One measured value for one compound.

    Separate from Membership because the interesting generations in a real
    programme are characterised by several numbers at once (wild-type potency,
    mutant potency, efflux ratio, free brain/plasma), and a single activity
    column cannot express the trade-off those numbers are being traded against.
    """

    compound_id: str
    measure_type: str
    value: float
    unit: str = ""
    assay: str = ""
    source: str = ""
    provenance: str = "unverified"
    note: str = ""

    @property
    def verified(self) -> bool:
        return self.provenance in CHECKABLE_PROVENANCE


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
    # Intermediate compounds usually come from the discovery paper rather than a
    # patent example, so the citation has to be able to say which it is.
    source_kind: str = "patent"
    activity_type: str = ""
    activity_value_nm: float | None = None
    activity_assay: str = ""
    activity_source: str = ""
    activity_provenance: str = "unverified"
    note: str = ""

    @property
    def citation(self) -> str:
        bits = [b for b in (self.patent_number, self.example_ref) if b]
        if not bits:
            return "(无引用)"
        label = " ".join(bits)
        if self.source_kind == "paper":
            return f"{label} [文献]"
        return label


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
    measurements: list[Measurement] = field(default_factory=list)

    def measurements_for(self, compound_id: str) -> list[Measurement]:
        return [m for m in self.measurements if m.compound_id == compound_id]

    def measure_types(self) -> list[str]:
        return sorted({m.measure_type for m in self.measurements})

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
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("测量值必须是有限数值")
    return value


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
    pending: list[str] = []
    with compounds_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        _require_columns(compounds_path, reader.fieldnames or [], REQUIRED_COMPOUND_COLUMNS)
        for row in reader:
            cid = row.get("compound_id", "").strip()
            if not cid:
                continue
            if cid in compounds or cid in pending:
                raise ValueError(f"重复 compound_id: {cid}")
            if row.get("smiles", "").strip() in PLACEHOLDER_SMILES:
                pending.append(cid)
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
            if cid in pending:
                continue
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
                    source_kind=row.get("source_kind", "").strip() or "patent",
                    activity_type=row.get("activity_type", "").strip(),
                    activity_value_nm=_parse_float(row.get("activity_value_nm", "")),
                    activity_assay=row.get("activity_assay", "").strip(),
                    activity_source=row.get("activity_source", "").strip(),
                    activity_provenance=row.get("activity_provenance", "unverified").strip()
                    or "unverified",
                    note=row.get("note", "").strip(),
                )
            )

    # measurements.csv is optional -- a dataset with only a primary activity
    # column stays valid.
    measurements: list[Measurement] = []
    measures_path = data_dir / "measurements.csv"
    if measures_path.exists():
        with measures_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            _require_columns(
                measures_path,
                reader.fieldnames or [],
                ("compound_id", "measure_type", "value", "source"),
            )
            for row in reader:
                cid = row.get("compound_id", "").strip()
                mtype = row.get("measure_type", "").strip()
                if not cid or not mtype:
                    continue
                if cid in pending:
                    continue
                if cid not in compounds:
                    raise ValueError(f"measurements.csv 引用了未知 compound_id: {cid}")
                value = _parse_float(row.get("value", ""))
                if value is None:
                    raise ValueError(f"{cid}/{mtype}: value 为空")
                if not row.get("source", "").strip():
                    raise ValueError(
                        f"{cid}/{mtype}: source 为空 —— 可审计字段不允许缺失"
                    )
                measurements.append(
                    Measurement(
                        compound_id=cid,
                        measure_type=mtype,
                        value=value,
                        unit=row.get("unit", "").strip(),
                        assay=row.get("assay", "").strip(),
                        source=row["source"].strip(),
                        provenance=row.get("provenance", "unverified").strip()
                        or "unverified",
                        note=row.get("note", "").strip(),
                    )
                )

    dataset = Dataset(programs, compounds, memberships, warnings, measurements)
    metadata_path = data_dir / "sources.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        warnings.extend(metadata.get("limitations", []))
    incomplete_citations = [m for m in memberships if m.example_ref in PLACEHOLDER_SMILES
                           or not m.source_url.startswith(("https://", "http://"))]
    if incomplete_citations:
        warnings.append(f"{len(incomplete_citations)} 条来源缺少可定位的编号或有效链接；不能作为已核实引用。")

    if pending:
        warnings.append(
            f"{len(pending)} 行骨架行的 SMILES 还没填，已跳过: {', '.join(pending)}。"
            "跑 `python -m phase0.sar.curate --check` 看还缺什么。"
        )
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
    flagged = [
        (c.compound_id, c.features.synthetic_handles)
        for c in compounds.values()
        if c.features.synthetic_handles
    ]
    if flagged:
        detail = "; ".join(f"{cid}({'/'.join(h)})" for cid, h in flagged)
        warnings.append(
            f"{len(flagged)} 个结构带保护基或偶联把手: {detail}。"
            "受测类似物几乎不会带这些基团 —— 请确认没有把合成砌块当成受测化合物录入。"
        )

    n_unver_meas = sum(1 for m in measurements if not m.verified)
    if n_unver_meas:
        warnings.append(
            f"{n_unver_meas} 条实测数据 (measurements.csv) 的 provenance 不可核查。"
        )
    return dataset
