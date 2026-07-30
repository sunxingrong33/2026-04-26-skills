"""Generation aggregation and delta computation.

Aggregates per-generation medians (numeric) and true-fractions (boolean), then
computes adjacent-generation deltas. Every delta carries ``n_support`` so a
one-compound-per-generation dataset -- which Phase 0 usually is -- degrades to a
weaker claim rather than a wrong one.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from rdkit import Chem

from .align import Alignment, align_generations
from .features import BOOLEAN_FEATURES, FEATURE_LABELS, NUMERIC_FEATURES
from .schema import Dataset, Membership


@dataclass
class MeasureAggregate:
    """Median of one measure_type across the compounds of one generation."""

    measure_type: str
    median: float
    n: int
    unit: str
    assays: list[str]
    all_verified: bool


@dataclass
class MeasureDelta:
    measure_type: str
    val_from: float
    val_to: float
    unit: str
    ratio: float | None
    n_support: int
    comparable: bool
    note: str = ""

    @property
    def delta(self) -> float:
        return round(self.val_to - self.val_from, 4)


@dataclass
class GenerationSummary:
    program_id: str
    generation: int
    members: list[Membership]
    numeric: dict[str, float]
    boolean_fraction: dict[str, float]
    earliest_priority: date | None
    patents: list[str]
    activity_median_nm: float | None
    activity_assays: list[str]
    activity_n: int
    n_compounds: int
    measures: dict[str, MeasureAggregate] = field(default_factory=dict)

    @property
    def patent_label(self) -> str:
        return ", ".join(sorted(set(p for p in self.patents if p))) or "(无专利号)"


@dataclass
class FeatureDelta:
    feature: str
    label: str
    val_from: float
    val_to: float
    delta: float
    n_support: int
    kind: str = "numeric"      # numeric | boolean

    @property
    def transition(self) -> str | None:
        if self.kind != "boolean":
            return None
        was, now = self.val_from >= 0.5, self.val_to >= 0.5
        if was == now:
            return None
        return f"{'true' if was else 'false'} -> {'true' if now else 'false'}"


@dataclass
class GenerationDelta:
    program_id: str
    gen_from: int
    gen_to: int
    summary_from: GenerationSummary
    summary_to: GenerationSummary
    features: dict[str, FeatureDelta]
    alignment: Alignment | None
    activity_ratio: float | None
    activity_comparable: bool
    activity_note: str = ""
    n_support: int = 0
    hypotheses: list = field(default_factory=list)
    measures: dict[str, MeasureDelta] = field(default_factory=dict)

    def delta_of(self, feature: str) -> FeatureDelta | None:
        return self.features.get(feature)

    @property
    def years_elapsed(self) -> float | None:
        a, b = self.summary_from.earliest_priority, self.summary_to.earliest_priority
        if not a or not b:
            return None
        return round((b - a).days / 365.25, 1)


def summarize_generation(
    dataset: Dataset, program_id: str, generation: int, members: list[Membership]
) -> GenerationSummary:
    feats = [dataset.compounds[m.compound_id].features for m in members]

    numeric: dict[str, float] = {}
    for name in NUMERIC_FEATURES:
        vals = [f.numeric[name] for f in feats if name in f.numeric]
        if vals:
            numeric[name] = round(statistics.median(vals), 3)

    boolean_fraction: dict[str, float] = {}
    for name in BOOLEAN_FEATURES:
        vals = [1.0 if f.boolean.get(name) else 0.0 for f in feats]
        if vals:
            boolean_fraction[name] = round(sum(vals) / len(vals), 3)

    dates = [m.priority_date for m in members if m.priority_date]
    acts = [m.activity_value_nm for m in members if m.activity_value_nm is not None]
    assays = sorted({m.activity_assay for m in members if m.activity_assay})

    by_type: dict[str, list] = {}
    for m in members:
        for meas in dataset.measurements_for(m.compound_id):
            by_type.setdefault(meas.measure_type, []).append(meas)
    measures: dict[str, MeasureAggregate] = {}
    for mtype, items in sorted(by_type.items()):
        units = {i.unit for i in items if i.unit}
        if len(units) > 1:
            raise ValueError(
                f"{mtype} 在 Gen{generation} 中混用了单位 {sorted(units)}，"
                "无法聚合 —— 请统一单位后再录入"
            )
        measures[mtype] = MeasureAggregate(
            measure_type=mtype,
            median=round(statistics.median(i.value for i in items), 4),
            n=len(items),
            unit=next(iter(units), ""),
            assays=sorted({i.assay for i in items if i.assay}),
            all_verified=all(i.verified for i in items),
        )

    return GenerationSummary(
        program_id=program_id,
        generation=generation,
        members=members,
        numeric=numeric,
        boolean_fraction=boolean_fraction,
        earliest_priority=min(dates) if dates else None,
        patents=[m.patent_number for m in members],
        activity_median_nm=round(statistics.median(acts), 3) if acts else None,
        activity_assays=assays,
        activity_n=len(acts),
        n_compounds=len(members),
        measures=measures,
    )


def _mols(dataset: Dataset, members: list[Membership]) -> list[Chem.Mol]:
    out = []
    for m in members:
        mol = Chem.MolFromSmiles(dataset.compounds[m.compound_id].smiles)
        if mol is not None:
            out.append(mol)
    return out


def compute_program_deltas(
    dataset: Dataset, program_id: str, run_mcs: bool = True
) -> tuple[list[GenerationSummary], list[GenerationDelta]]:
    gens = dataset.generations(program_id)
    summaries = [
        summarize_generation(dataset, program_id, g, members) for g, members in gens.items()
    ]

    deltas: list[GenerationDelta] = []
    for prev, curr in zip(summaries, summaries[1:]):
        n_support = min(prev.n_compounds, curr.n_compounds)
        feats: dict[str, FeatureDelta] = {}

        for name in NUMERIC_FEATURES:
            if name in prev.numeric and name in curr.numeric:
                a, b = prev.numeric[name], curr.numeric[name]
                feats[name] = FeatureDelta(
                    feature=name,
                    label=FEATURE_LABELS.get(name, name),
                    val_from=a,
                    val_to=b,
                    delta=round(b - a, 3),
                    n_support=n_support,
                )
        for name in BOOLEAN_FEATURES:
            if name in prev.boolean_fraction and name in curr.boolean_fraction:
                a, b = prev.boolean_fraction[name], curr.boolean_fraction[name]
                feats[name] = FeatureDelta(
                    feature=name,
                    label=FEATURE_LABELS.get(name, name),
                    val_from=a,
                    val_to=b,
                    delta=round(b - a, 3),
                    n_support=n_support,
                    kind="boolean",
                )

        ratio = None
        comparable = True
        note = ""
        if prev.activity_median_nm and curr.activity_median_nm:
            ratio = round(curr.activity_median_nm / prev.activity_median_nm, 3)
            shared = set(prev.activity_assays) & set(curr.activity_assays)
            if prev.activity_assays and curr.activity_assays and not shared:
                comparable = False
                note = (
                    f"活性来自不同 assay ({'/'.join(prev.activity_assays)} vs "
                    f"{'/'.join(curr.activity_assays)})，只能看数量级趋势，不能做定量比较"
                )
        elif prev.activity_median_nm or curr.activity_median_nm:
            note = "仅一代有活性数据，无法计算变化倍数"
        else:
            note = "两代均无活性数据"

        measure_deltas: dict[str, MeasureDelta] = {}
        for mtype in sorted(set(prev.measures) & set(curr.measures)):
            ma, mb = prev.measures[mtype], curr.measures[mtype]
            shared_assay = set(ma.assays) & set(mb.assays)
            comparable = not (ma.assays and mb.assays and not shared_assay)
            measure_deltas[mtype] = MeasureDelta(
                measure_type=mtype,
                val_from=ma.median,
                val_to=mb.median,
                unit=ma.unit or mb.unit,
                ratio=round(mb.median / ma.median, 3) if ma.median else None,
                n_support=min(ma.n, mb.n),
                comparable=comparable,
                note="" if comparable
                else f"assay 不同 ({'/'.join(ma.assays)} vs {'/'.join(mb.assays)})，仅趋势可读",
            )

        # Ring-system comparison always runs (it is what core-hop detection is
        # keyed on); the MCS pass is descriptive only and can be skipped.
        alignment = align_generations(
            _mols(dataset, prev.members), _mols(dataset, curr.members), run_mcs=run_mcs
        )

        deltas.append(
            GenerationDelta(
                program_id=program_id,
                gen_from=prev.generation,
                gen_to=curr.generation,
                summary_from=prev,
                summary_to=curr,
                features=feats,
                alignment=alignment,
                activity_ratio=ratio,
                activity_comparable=comparable,
                activity_note=note,
                n_support=n_support,
                measures=measure_deltas,
            )
        )
    return summaries, deltas
