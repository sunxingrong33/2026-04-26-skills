"""Tiered extraction controller: deterministic decisions, injected extractors.

Levels run in order (for example L1 structured records, L2 full text, L3 PDF
image/table recognition). Each level is a function

    extractor(publication, wanted) -> list[Fact]

where ``wanted`` is ``None`` on the first level (report everything you can find)
and afterwards the list of still-missing required slots. The controller, not the
extractor, decides:

* **identity** -- a fact must carry the publication number and example label the
  source itself showed; a mismatch is rejected with a reason, never re-keyed;
* **escalation** -- only missing required fields go to the next level; once
  nothing required is missing, later (more expensive) levels are not called;
* **conflicts** -- a later level disagreeing with an earlier one is kept as a
  conflict and lowers confidence; no value is silently preferred;
* **cross-checks** -- reported mass versus structure, and optionally a name ->
  structure resolver (e.g. OPSIN), both injected;
* **confidence** -- high only when independent evidence agrees; anything else is
  medium or low and carries flags, so errors surface for review.

Nothing here reads a PDF or calls a model; that is what the injected extractors do.
"""
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from rdkit import Chem, rdBase

from phase0.sar.mass_check import check_mass

COMPOUND_FIELDS = {'smiles', 'role', 'reported_mass', 'name'}
OBSERVATION_FIELDS = {'status', 'relation', 'value', 'unit', 'grade'}
ROLES = {'example', 'intermediate', 'reference', 'reagent'}
STATUSES = {'measured', 'not_tested', 'blank', 'not_reported', 'not_applicable'}
RELATIONS = {'=', '<', '<=', '>', '>=', '~', 'grade'}


@dataclass(frozen=True)
class Fact:
    kind: str                       # 'compound' or 'observation'
    example: str
    field: str
    value: Any
    level: str
    source: str
    seen_publication: str           # publication number as printed on the source read
    seen_example: str               # example label as printed on the source read
    assay_id: Optional[str] = None
    page: Optional[int] = None
    locator: Optional[str] = None

    @property
    def slot(self):
        return (self.kind, self.example, self.assay_id)


def _norm_pub(text):
    return re.sub(r'[^A-Z0-9]', '', str(text).upper())


def _norm_example(text):
    return re.sub(r'^(EXAMPLE|实施例|EX\.?)\s*', '', str(text).strip(), flags=re.I).strip().upper()


def _inchikey(smiles):
    _ = rdBase.BlockLogs()
    mol = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) else None
    return Chem.MolToInchiKey(mol) if mol else None


def _same(field_name, a, b):
    if field_name == 'smiles':
        ka, kb = _inchikey(a), _inchikey(b)
        return ka is not None and ka == kb
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=0)
    return str(a).strip() == str(b).strip()


def _invalid(f):
    if f.kind not in ('compound', 'observation'):
        return '未知记录类型'
    allowed = COMPOUND_FIELDS if f.kind == 'compound' else OBSERVATION_FIELDS
    if f.field not in allowed:
        return f'未知字段 {f.field}'
    if f.kind == 'observation' and not f.assay_id:
        return '观测缺少 assay_id'
    if f.field == 'role' and f.value not in ROLES:
        return f'角色无效：{f.value}'
    if f.field == 'status' and f.value not in STATUSES:
        return f'状态无效：{f.value}'
    if f.field == 'relation' and f.value not in RELATIONS:
        return f'限定符无效：{f.value}'
    if f.field == 'value' and (not isinstance(f.value, (int, float)) or isinstance(f.value, bool)
                               or not math.isfinite(f.value)):
        return '数值不是有限数'
    return None


def required_missing(kind, fields):
    """Fields that may still be needed for one slot.

    Asks for everything that could be required at once (an unknown status or qualifier
    also needs a value and unit), so one escalation can finish the slot; completeness is
    judged from the values actually found, so an untested cell or a graded value never
    needs a number.
    """
    have = {k for k, v in fields.items() if v.get('value') is not None}
    if kind == 'compound':
        return [f for f in ('smiles', 'role') if f not in have]
    if 'status' not in have:
        return [f for f in ('status', 'relation', 'value', 'unit') if f not in have]
    if fields['status']['value'] != 'measured':
        return []
    if 'relation' not in have:
        return [f for f in ('relation', 'value', 'unit') if f not in have]
    if fields['relation']['value'] == 'grade':
        return [] if 'grade' in have else ['grade']
    return [f for f in ('value', 'unit') if f not in have]


@dataclass
class Result:
    publication: str
    slots: dict = field(default_factory=dict)
    log: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    assessments: dict = field(default_factory=dict)


class Cascade:
    def __init__(self, publication, levels, name_to_smiles: Optional[Callable[[str], Optional[str]]] = None):
        self.publication = publication
        self.levels = levels
        self.name_to_smiles = name_to_smiles

    def missing(self, slots):
        return [(slot, f) for slot, fields in slots.items() for f in required_missing(slot[0], fields)]

    def run(self):
        result = Result(self.publication)
        target = _norm_pub(self.publication)
        stopped = False
        for level, extractor in self.levels:
            if stopped:
                result.log.append({'level': level, 'action': 'not_called', 'reason': '必填字段已齐全'})
                continue
            wanted = None if not result.slots else self.missing(result.slots)
            entry = {'level': level, 'action': 'called', 'requested': 'all' if wanted is None else len(wanted),
                     'returned': 0, 'accepted': 0, 'filled': 0, 'corroborated': 0, 'conflicts': 0}
            try:
                facts = list(extractor(self.publication, wanted))
            except Exception as exc:  # an extractor failure must not stop the cascade
                entry.update(action='failed', error=f'{type(exc).__name__}: {exc}')
                facts = []
            entry['returned'] = len(facts)
            for f in facts:
                reason = None
                if _norm_pub(f.seen_publication) != target:
                    reason = f'公开号不符：来源显示 {f.seen_publication}'
                elif _norm_example(f.seen_example) != _norm_example(f.example):
                    reason = f'实施例编号不符：来源显示 {f.seen_example}'
                else:
                    reason = _invalid(f)
                if reason:
                    result.rejected.append({'level': level, 'slot': list(f.slot), 'field': f.field, 'reason': reason})
                    continue
                entry['accepted'] += 1
                cell = result.slots.setdefault(f.slot, {}).setdefault(f.field, {'value': None, 'support': [],
                                                                                 'conflicts': []})
                prov = {'level': f.level, 'source': f.source, 'page': f.page, 'locator': f.locator}
                if cell['value'] is None:
                    cell['value'] = f.value
                    cell['support'].append(prov)
                    entry['filled'] += 1
                elif _same(f.field, cell['value'], f.value):
                    cell['support'].append(prov)
                    entry['corroborated'] += 1
                else:
                    cell['conflicts'].append({**prov, 'value': f.value})
                    entry['conflicts'] += 1
            entry['missing_after'] = len(self.missing(result.slots)) if result.slots else None
            result.log.append(entry)
            if result.slots and not entry['missing_after']:
                stopped = True
        self._assess(result)
        return result

    def _assess(self, result):
        for slot, fields in result.slots.items():
            kind = slot[0]
            flags, checks = [], {}
            missing = required_missing(kind, fields)
            if missing:
                flags.append('incomplete')
            flags += [f'conflict:{name}' for name, cell in fields.items() if cell['conflicts']]
            if kind == 'compound':
                smiles = fields.get('smiles', {}).get('value')
                if smiles is not None and _inchikey(smiles) is None:
                    flags.append('invalid_structure')
                reported = fields.get('reported_mass', {}).get('value')
                if smiles is not None and reported is not None:
                    checks['mass'] = check_mass(smiles, reported)['status']
                    flags += {'inconsistent': ['mass_mismatch'], 'alternative_match': ['mass_alternative_ion'],
                              'invalid_structure': []}.get(checks['mass'], [])
                name = fields.get('name', {}).get('value')
                if smiles is not None and name and self.name_to_smiles:
                    resolved = self.name_to_smiles(name)
                    key = _inchikey(resolved) if resolved else None
                    checks['name'] = 'unresolved' if key is None else 'agree' if key == _inchikey(smiles) else 'disagree'
                    if checks['name'] == 'disagree':
                        flags.append('name_disagrees')
                levels = {s['level'] for s in fields.get('smiles', {}).get('support', [])}
                if flags:
                    confidence = 'low'
                elif checks.get('mass') == 'consistent' and (checks.get('name') == 'agree' or len(levels) >= 2):
                    confidence = 'high'
                else:
                    confidence = 'medium'
            else:
                required = [f for f in ('status', 'relation', 'value', 'unit', 'grade') if f in fields]
                independent = all(len({s['level'] for s in fields[f]['support']}) >= 2 for f in required)
                confidence = 'low' if flags else 'high' if independent else 'medium'
            result.assessments[slot] = {'confidence': confidence, 'flags': flags, 'checks': checks,
                                        'missing': missing}
