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
from .units import normalise, assays_comparable


@dataclass
class MeasureAggregate:
    """Median of one measure_type across the compounds of one generation."""

    measure_type: str
    median: float
    n: int
    unit: str
    assays: list[str]
    all_verified: bool
    sources: list[str] = field(default_factory=list)


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
    sources: list[str] = field(default_factory=list)

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
    activity_types: list[str] = field(default_factory=list)

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
    measured_members = [m for m in members if m.activity_value_nm is not None]
    assays = sorted({m.activity_assay for m in measured_members})

    by_type: dict[str, list] = {}
    for m in members:
        for meas in dataset.measurements_for(m.compound_id):
            by_type.setdefault(meas.measure_type, []).append(meas)
    measures: dict[str, MeasureAggregate] = {}
    for mtype, items in sorted(by_type.items()):
        values_units = [normalise(i.value, i.unit) for i in items]
        units = {unit for _, unit in values_units}
        if len(units) > 1:
            raise ValueError(
                f"{mtype} 在 Gen{generation} 中混用了单位 {sorted(units)}，"
                "无法聚合 —— 请统一单位后再录入"
            )
        per_compound: dict[str, list[float]] = {}
        for item, (value, _) in zip(items, values_units):
            per_compound.setdefault(item.compound_id, []).append(value)
        measures[mtype] = MeasureAggregate(
            measure_type=mtype,
            median=statistics.median(statistics.median(vals) for vals in per_compound.values()),
            n=len({i.compound_id for i in items}),
            unit=next(iter(units), ""),
            assays=sorted({i.assay for i in items}),
            all_verified=all(i.verified for i in items),
            sources=sorted({i.source for i in items}),
        )

    return GenerationSummary(
        program_id=program_id,
        generation=generation,
        members=members,
        numeric=numeric,
        boolean_fraction=boolean_fraction,
        earliest_priority=min(dates) if dates else None,
        patents=[m.patent_number for m in members],
        activity_median_nm=statistics.median(acts) if acts else None,
        activity_assays=assays,
        activity_n=len(acts),
        n_compounds=len(members),
        measures=measures,
        activity_types=sorted({m.activity_type for m in measured_members}),
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
        activity_comparable = False
        note = ""
        if prev.activity_median_nm is not None and curr.activity_median_nm is not None:
            activity_comparable = (
                assays_comparable(prev.activity_assays, curr.activity_assays)
                and assays_comparable(prev.activity_types, curr.activity_types)
            )
            if activity_comparable and prev.activity_median_nm > 0:
                ratio = curr.activity_median_nm / prev.activity_median_nm
            if not activity_comparable:
                note = (
                    f"活性 assay/类型缺失或不一致 ({'/'.join(prev.activity_assays)} vs "
                    f"{'/'.join(curr.activity_assays)})，仅并列展示，不计算变化倍数或推断趋势"
                )
        elif prev.activity_median_nm is not None or curr.activity_median_nm is not None:
            note = "仅一代有活性数据，无法计算变化倍数"
        else:
            note = "两代均无活性数据"

        measure_deltas: dict[str, MeasureDelta] = {}
        for mtype in sorted(set(prev.measures) & set(curr.measures)):
            ma, mb = prev.measures[mtype], curr.measures[mtype]
            if ma.unit != mb.unit:
                raise ValueError(f"{mtype} 跨代混用了单位 {ma.unit!r} / {mb.unit!r}，无法比较")
            measure_comparable = assays_comparable(ma.assays, mb.assays)
            measure_deltas[mtype] = MeasureDelta(
                measure_type=mtype,
                val_from=ma.median,
                val_to=mb.median,
                unit=ma.unit or mb.unit,
                ratio=mb.median / ma.median if ma.median > 0 and measure_comparable
                and not any(token in mtype.lower() for token in ("logd", "logp", "pka", "pic50", "pki", "pec50"))
                and mtype.lower() != "ph" else None,
                n_support=min(ma.n, mb.n),
                comparable=measure_comparable,
                note="" if measure_comparable
                else f"assay 缺失、混合或不同 ({'/'.join(ma.assays)} vs {'/'.join(mb.assays)})，仅并列展示",
                sources=sorted(set(ma.sources + mb.sources)),
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
                activity_comparable=activity_comparable,
                activity_note=note,
                n_support=n_support,
                measures=measure_deltas,
            )
        )
    return summaries, deltas
