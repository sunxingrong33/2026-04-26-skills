"""Evidence ledger entities.

Contract enforced here rather than by convention:

* every record carries ``record_status``; anything written by a tool or agent is
  ``proposed`` and only a human review may make it ``confirmed``;
* missing measurements keep their meaning (not tested / blank / not reported /
  not applicable) and never carry a number;
* qualifiers are kept; graded values carry a grade defined by their assay and
  never a continuous value;
* compound role (example / intermediate / reference / reagent) is explicit;
  ``unspecified`` means nobody has annotated it yet and is listed as a gap;
* every observation resolves to a document, a compound and an assay;
* a relation between documents is a curated record whose basis is a list of
  machine-checkable conditions; dates or citations alone never create one.

    python -m phase0.ledger.schema            # write ledger.schema.json
    python -m phase0.ledger.schema --check    # fail if the committed schema drifted
"""
import argparse
import json
import math
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 2
SCHEMA_FILE = Path(__file__).with_name('ledger.schema.json')


class DocumentKind(str, Enum):
    paper = 'paper'
    patent = 'patent'


class RecordStatus(str, Enum):
    proposed = 'proposed'
    confirmed = 'confirmed'
    rejected = 'rejected'


class CompoundRole(str, Enum):
    example = 'example'
    intermediate = 'intermediate'
    reference = 'reference'
    reagent = 'reagent'
    unspecified = 'unspecified'


class ObservationStatus(str, Enum):
    measured = 'measured'
    not_tested = 'not_tested'
    blank = 'blank'
    not_reported = 'not_reported'
    not_applicable = 'not_applicable'


class Relation(str, Enum):
    eq = '='
    lt = '<'
    le = '<='
    gt = '>'
    ge = '>='
    approx = '~'
    grade = 'grade'


class RelationType(str, Enum):
    same_family = 'same_family'
    sibling_application = 'sibling_application'
    citation = 'citation'
    inventor_overlap = 'inventor_overlap'
    related_series = 'related_series'


class CheckKind(str, Enum):
    family_id = 'family_id'
    priority_date = 'priority_date'
    snapshot_matches_evidence = 'snapshot_matches_evidence'
    has_evidence_cards = 'has_evidence_cards'
    cites = 'cites'


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', use_enum_values=True)


class Location(_Model):
    """Where a fact was read. ``locator`` is free text (table row, paragraph, record)."""
    url: Optional[str] = None
    pdf_page: Optional[int] = Field(default=None, ge=1)
    printed_page: Optional[int] = None
    locator: Optional[str] = None
    scope: Optional[str] = None


class Review(_Model):
    record_status: RecordStatus = RecordStatus.proposed
    provenance_status: str = Field(description='来源处理状态原文，例如 database_import_pending_original_review')
    reviewer: Optional[str] = None
    gaps: list[str] = Field(default_factory=list, description='显式列出的待补证据')

    @model_validator(mode='after')
    def _confirmed_needs_reviewer(self):
        if self.record_status == RecordStatus.confirmed.value and not self.reviewer:
            raise ValueError('confirmed 记录必须写明复核人')
        return self


class Document(_Model):
    id: str
    kind: DocumentKind
    url: str
    identifiers: dict[str, Any] = Field(default_factory=dict, description='DOI、PubMed、公开号等')
    source_sha256: dict[str, str] = Field(default_factory=dict, description='原始快照哈希，例如 html / pdf')
    review: Review


class Compound(_Model):
    id: str
    document_id: str
    label: str
    smiles: str
    role: CompoundRole
    structure_source: Location
    mapping_source: Location = Field(description='编号与结构对应关系的来源')
    stereochemistry_note: Optional[str] = None
    reported_mass: Optional[float] = Field(default=None, description='原文报告的质谱值（如 LCMS M+H），仅作辅助核对')
    review: Review


class Assay(_Model):
    id: str = Field(description='台账内唯一：<document_id>:<source_assay_id>')
    document_id: str
    source_assay_id: str = Field(description='原文或数据库中的 assay 编号，不同文档可以重名')
    endpoint: str = Field(description='终点，例如 IC50、Ki、Emax；Emax 等激动剂终点作为独立 assay 记录')
    protocol: Optional[str] = None
    protocol_locator: Optional[str] = None
    unit: Optional[str] = None
    variant: Optional[str] = Field(default=None, description='协议变体，例如 10 点 / 20 点格式')
    grade_definitions: Optional[dict[str, str]] = Field(
        default=None, description='分级值的等级定义，例如 {"A": "EC50 < 10 nM"}')


class Observation(_Model):
    id: str
    document_id: str
    compound_id: str
    assay_id: str
    status: ObservationStatus
    relation: Optional[Relation] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    grade: Optional[str] = None
    missing_reason: Optional[str] = None
    quality_flag: Optional[str] = None
    source: Location
    raw: dict[str, Any] = Field(description='来源记录原样保存，用于无损回溯')
    review: Review

    @field_validator('value')
    @classmethod
    def _finite(cls, v):
        if v is not None and not math.isfinite(v):
            raise ValueError('测量值必须是有限数值')
        return v

    @model_validator(mode='after')
    def _status_consistency(self):
        where = f'观测 {self.id}'
        if self.status != ObservationStatus.measured.value:
            if self.value is not None or self.grade is not None or self.relation is not None:
                raise ValueError(f'{where} 状态为 {self.status}，不能带数值、等级或限定符')
        elif self.relation is None:
            raise ValueError(f'{where} 已测量但缺少限定符')
        elif self.relation == Relation.grade.value:
            if not self.grade:
                raise ValueError(f'{where} 为分级值但缺少 grade')
            if self.value is not None:
                raise ValueError(f'{where} 为分级值，不能同时给出连续数值')
        elif self.value is None:
            raise ValueError(f'{where} 已测量但缺少数值')
        return self


class RelationCheck(_Model):
    """One condition that must hold on the loaded sources before the relation is shown."""
    check: CheckKind
    publication: str
    expected: Optional[str] = Field(default=None, description='family_id / priority_date / cites 的期望值')

    @model_validator(mode='after')
    def _expected_when_needed(self):
        needs = {CheckKind.family_id.value, CheckKind.priority_date.value, CheckKind.cites.value}
        if (self.check in needs) != (self.expected is not None):
            raise ValueError(f'条件 {self.check} 的 expected 设置不正确')
        return self


class RelationSource(_Model):
    """A fixed URL, or the loaded publication's source page plus an optional fragment."""
    label: str
    url: Optional[str] = None
    publication: Optional[str] = None
    fragment: str = ''

    @model_validator(mode='after')
    def _one_target(self):
        if (self.url is None) == (self.publication is None):
            raise ValueError(f'来源 {self.label} 必须且只能给出 url 或 publication 之一')
        return self


class DocumentRelation(_Model):
    id: str
    type: RelationType
    from_publication: str
    to_publication: str
    status: str = Field(description='对外展示的关系状态，例如 related_series_not_direct_evolution')
    label: str
    basis: list[RelationCheck] = Field(min_length=1, description='全部满足才展示该关系')
    facts: list[str]
    hypothesis: Optional[str] = Field(default=None, description='研究假设，不是原文确认的结论')
    gaps: list[str]
    sources: list[RelationSource]
    pair: Optional[dict[str, dict[str, Any]]] = Field(default=None, description='预设的 A/B 分子对照')
    review: Review

    @model_validator(mode='after')
    def _scoped(self):
        ends = {self.from_publication, self.to_publication}
        if len(ends) != 2:
            raise ValueError(f'关系 {self.id} 两端不能是同一文档')
        stray = [c.publication for c in self.basis if c.publication not in ends]
        stray += [s.publication for s in self.sources if s.publication and s.publication not in ends]
        if stray:
            raise ValueError(f'关系 {self.id} 的条件或来源引用了两端以外的文档：{"、".join(stray)}')
        return self


class InputFile(_Model):
    path: str
    sha256: str


class Ledger(_Model):
    schema_version: int = SCHEMA_VERSION
    scope: str
    notice: str
    inputs: list[InputFile]
    documents: list[Document]
    compounds: list[Compound]
    assays: list[Assay]
    observations: list[Observation]
    relations: list[DocumentRelation] = Field(default_factory=list)

    @model_validator(mode='after')
    def _integrity(self):
        problems = []
        for name in ('documents', 'compounds', 'assays', 'observations', 'relations'):
            ids = [x.id for x in getattr(self, name)]
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            if dupes:
                problems.append(f'{name} 编号重复：{"、".join(dupes[:5])}')
        docs = {d.id for d in self.documents}
        compounds = {c.id for c in self.compounds}
        assays = {a.id: a for a in self.assays}
        for c in self.compounds:
            if c.document_id not in docs:
                problems.append(f'化合物 {c.id} 引用了不存在的文档 {c.document_id}')
        for a in self.assays:
            if a.document_id not in docs:
                problems.append(f'assay {a.id} 引用了不存在的文档 {a.document_id}')
        for o in self.observations:
            if o.document_id not in docs:
                problems.append(f'观测 {o.id} 引用了不存在的文档 {o.document_id}')
            if o.compound_id not in compounds:
                problems.append(f'观测 {o.id} 引用了不存在的化合物 {o.compound_id}')
            assay = assays.get(o.assay_id)
            if assay is None:
                problems.append(f'观测 {o.id} 引用了不存在的 assay {o.assay_id}')
            elif assay.document_id != o.document_id:
                problems.append(f'观测 {o.id} 的 assay 属于另一文档 {assay.document_id}')
            elif o.relation == Relation.grade.value and o.grade not in (assay.grade_definitions or {}):
                problems.append(f'观测 {o.id} 的等级 {o.grade} 未在 assay {assay.id} 中定义')
        for r in self.relations:
            for end in (r.from_publication, r.to_publication):
                if end not in docs:
                    problems.append(f'关系 {r.id} 引用了不存在的文档 {end}')
        if problems:
            raise ValueError('；'.join(problems[:20]))
        return self


def json_schema():
    return json.dumps(Ledger.model_json_schema(), ensure_ascii=False, indent=1, sort_keys=True) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description='导出或核对证据台账 JSON Schema')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    if args.check:
        if not SCHEMA_FILE.exists() or SCHEMA_FILE.read_text(encoding='utf8') != json_schema():
            print('ledger.schema.json 与模型不一致，请运行 python -m phase0.ledger.schema')
            return 1
        return 0
    SCHEMA_FILE.write_text(json_schema(), encoding='utf8')
    print(f'写入 {SCHEMA_FILE.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
