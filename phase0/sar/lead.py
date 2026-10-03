"""Lead compound entry and project constraints (iteration I2.8, work items A and C).

The lead structure is an *anchor*: it decides what is searched and which pairs
are worth looking at. It never produces a number, and it is not added to the
ledger by being typed here.

Constraints carry the part of a project brief that the pairing code can act on:

* ``keep`` — fragments that must survive a replacement. A pair whose A side has
  the fragment and whose B side does not is marked as leaving the constrained
  space. Such pairs stay visible (the cost of breaking a scaffold is worth
  seeing) but never count as a candidate direction.
* ``synthesis_notes`` — free text from the chemist, carried through as labels.
  Nothing here judges feasibility.

Measurements the chemist types in for the lead are kept verbatim as **text**,
in their own column, marked unverified. They are never parsed into numbers, so
they cannot reach a ratio, a threshold or an evidence grade by any path.
"""
from rdkit import Chem, rdBase

from . import structure_search as ss

MAX_KEEP = 5
MIN_KEEP_ATOMS = 3       # smaller fragments occur in nearly every drug-like structure
MAX_NOTES = 10
MAX_NOTE_CHARS = 200
MAX_REFERENCE = 20
USER_UNVERIFIED = 'user_unverified'

NOTICE = ('先导结构只作为检索与配对的锚点，不产生任何数值，也不因为填写而进入台账。'
          '“必须保留的片段”只用于标注哪些替换离开了约束范围，被标注的分子对仍然显示。'
          '用户填写的测量值按原文保存为文本，不参与任何计算。')


def _mol(text, field):
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f'{field}不能为空。')
    if len(text) > 200:
        raise ValueError(f'{field}不超过 200 个字符。')
    _ = rdBase.BlockLogs()
    text = text.strip()
    mol = Chem.MolFromSmarts(text)
    if mol is None or mol.GetNumAtoms() == 0:
        mol = Chem.MolFromSmiles(text)
    if mol is None or mol.GetNumAtoms() == 0:
        raise ValueError(f'{field}无法解析为 SMARTS 或 SMILES：{text}')
    return text, mol


def read_lead(spec, ledger=None):
    """Standardise the lead structure and report whether the ledger already holds it.

    Returns the search record (input, standardisation steps, searched structure) plus exact
    ledger matches. Nothing here writes to the ledger.
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        spec = {'smiles': spec}
    if not isinstance(spec, dict):
        raise ValueError('先导结构格式无效。')
    q, mol = ss.query({'query': spec.get('smiles'), 'method': 'exact',
                       'standardize': spec.get('standardize', True)})
    hits = ss.local_search(q, mol, ledger)
    label = spec.get('label')
    if label is not None and (not isinstance(label, str) or len(label) > 60):
        raise ValueError('先导化合物名称不超过 60 个字符。')
    matches = [{'compound_id': r['compound_id'], 'document_id': r['document_id'], 'label': r['label'],
                'measured_observations': r['measured_observations']} for r in hits['rows']]
    return {'label': (label or '').strip() or None, 'input': q['input'], 'smiles': q['searched_smiles'],
            'original_smiles': q['original_smiles'], 'standardize': q['standardize'], 'steps': q['steps'],
            'ledger_matches': matches, 'in_ledger': bool(matches),
            'note': ('台账中已有该结构，可直接参与配对。' if matches else
                     '台账中没有该结构：它只用作锚点，本次分析的分子对仍来自台账中已有的化合物。'
                     '要让它参与配对，请先在专利工作台把它加入台账（待确认）。')}


def read_constraints(spec):
    """Validate project constraints: fragments that must survive, plus synthesis notes."""
    empty = {'keep': [], 'synthesis_notes': [], 'notice': NOTICE}
    if spec is None:
        return empty
    if not isinstance(spec, dict):
        raise ValueError('约束格式无效。')
    raw = spec.get('keep') or []
    if not isinstance(raw, list) or len(raw) > MAX_KEEP:
        raise ValueError(f'必须保留的片段最多 {MAX_KEEP} 个。')
    keep = []
    for item in raw:
        item = {'pattern': item} if isinstance(item, str) else item
        if not isinstance(item, dict):
            raise ValueError('必须保留的片段格式无效。')
        pattern, mol = _mol(item.get('pattern'), '必须保留的片段')
        if mol.GetNumAtoms() < MIN_KEEP_ATOMS:
            raise ValueError(f'必须保留的片段至少需要 {MIN_KEEP_ATOMS} 个原子，否则几乎所有结构都含有它。')
        label = item.get('label')
        if label is not None and (not isinstance(label, str) or len(label) > 40):
            raise ValueError('片段名称不超过 40 个字符。')
        keep.append({'pattern': pattern, 'label': (label or '').strip() or pattern, 'atoms': mol.GetNumAtoms()})
    notes = spec.get('synthesis_notes') or []
    if isinstance(notes, str):
        notes = [notes]
    if not isinstance(notes, list) or len(notes) > MAX_NOTES:
        raise ValueError(f'合成限制最多 {MAX_NOTES} 条。')
    cleaned = []
    for n in notes:
        if not isinstance(n, str) or len(n) > MAX_NOTE_CHARS:
            raise ValueError(f'每条合成限制不超过 {MAX_NOTE_CHARS} 个字符。')
        if n.strip():
            cleaned.append(n.strip())
    return {**empty, 'keep': keep, 'synthesis_notes': cleaned}


def patterns(constraints):
    """Compiled queries for the keep fragments, in the order they were given."""
    _ = rdBase.BlockLogs()
    return [(k['label'], _mol(k['pattern'], '必须保留的片段')[1]) for k in constraints['keep']]


def check_pair(a_mol, b_mol, compiled):
    """Whether a pair stays inside the constrained space.

    ``lost`` names fragments the A side has and the B side does not — the replacement
    destroys them. ``absent`` names fragments neither side has: the constraint simply
    does not describe this pair, which is not a violation.
    """
    if not compiled:
        return {'ok': True, 'lost': [], 'absent': [], 'kept': []}
    lost, absent, kept = [], [], []
    for label, query in compiled:
        a_has = a_mol is not None and a_mol.HasSubstructMatch(query)
        b_has = b_mol is not None and b_mol.HasSubstructMatch(query)
        if a_has and not b_has:
            lost.append(label)
        elif not a_has and not b_has:
            absent.append(label)
        else:
            kept.append(label)
    return {'ok': not lost, 'lost': lost, 'absent': absent, 'kept': kept}


def read_reference(spec, goal):
    """User-typed measurements for the lead: kept verbatim as text, never parsed.

    Each row names one goal property so the page can show it beside that property's
    evidence. The value is a string and stays a string.
    """
    if spec is None:
        return []
    if not isinstance(spec, list) or len(spec) > MAX_REFERENCE:
        raise ValueError(f'当前测量值最多 {MAX_REFERENCE} 条。')
    labels = {p['id']: p['label'] for p in goal['properties']}
    out = []
    for row in spec:
        if not isinstance(row, dict):
            raise ValueError('当前测量值格式无效。')
        pid = row.get('property_id')
        if pid is not None and pid not in labels:
            raise ValueError(f'当前测量值引用了目标中不存在的性质：{pid}')
        for field, limit in (('value', 60), ('note', MAX_NOTE_CHARS)):
            v = row.get(field)
            if v is not None and (not isinstance(v, str) or len(v) > limit):
                raise ValueError(f'当前测量值的 {field} 须为不超过 {limit} 个字符的文本。')
        value = (row.get('value') or '').strip()
        if not value:
            continue
        out.append({'property_id': pid, 'property': labels.get(pid, '未指定性质'), 'value': value,
                    'note': (row.get('note') or '').strip() or None, 'source': USER_UNVERIFIED,
                    'source_label': '用户提供，未核实'})
    return out
