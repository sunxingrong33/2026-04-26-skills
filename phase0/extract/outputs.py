"""Turn a cascade result into: a gold-format prediction, ledger proposals, and a readable report."""
from phase0.eval.gold import SCHEMA_VERSION, validate


def _value(fields, name):
    return fields.get(name, {}).get('value')


def _page(fields, *names):
    for name in names:
        for s in fields.get(name, {}).get('support', []):
            if s.get('page') is not None:
                return s['page']
    return None


def _slots(result, kind):
    return sorted(((slot, fields) for slot, fields in result.slots.items() if slot[0] == kind),
                  key=lambda x: (x[0][1], x[0][2] or ''))


def to_prediction(result):
    """Gold-format document for ``phase0.eval.extraction_metrics``.

    Incomplete observations are left out (the evaluator counts them as missed, not as
    fabricated); incomplete compounds stay in with the ``incomplete`` flag.
    """
    compounds, observations = [], []
    for (_, example, _), fields in _slots(result, 'compound'):
        a = result.assessments[('compound', example, None)]
        compounds.append({'example': example, 'role': _value(fields, 'role') or 'example',
                          'smiles': _value(fields, 'smiles'), 'structure_pdf_page': _page(fields, 'smiles'),
                          'reported_mass': _value(fields, 'reported_mass'),
                          'confidence': a['confidence'], 'flags': a['flags']})
    for slot, fields in _slots(result, 'observation'):
        a = result.assessments[slot]
        if a['missing']:
            continue
        status = _value(fields, 'status')
        measured = status == 'measured'
        observations.append({
            'example': slot[1], 'assay_id': slot[2], 'status': status,
            'relation': _value(fields, 'relation') if measured else None,
            'value': _value(fields, 'value') if measured and _value(fields, 'relation') != 'grade' else None,
            'unit': _value(fields, 'unit') if measured and _value(fields, 'relation') != 'grade' else None,
            'grade': _value(fields, 'grade') if measured else None,
            'table_pdf_page': _page(fields, 'value', 'grade', 'status'),
            'confidence': a['confidence'], 'flags': a['flags']})
    return validate({'schema_version': SCHEMA_VERSION, 'publication': result.publication,
                     'compounds': compounds, 'observations': observations})


def to_ledger_items(result, document_id, assay_ids, provenance='tiered_extraction_pending_review'):
    """Proposed ledger records for complete slots.

    ``assay_ids`` maps the extraction's assay labels to existing ledger assay ids;
    observations for unmapped assays are returned as refused rather than inventing an
    assay definition (endpoint and protocol come from the protocol section, not a table).
    """
    items, refused = [], []
    for (_, example, _), fields in _slots(result, 'compound'):
        a = result.assessments[('compound', example, None)]
        if 'smiles' in a['missing'] or 'invalid_structure' in a['flags']:
            refused.append({'slot': ['compound', example], 'reason': '结构缺失或无法解析'})
            continue
        page = _page(fields, 'smiles')
        items.append(('compounds', {
            'id': f'{document_id}:example:{example}', 'document_id': document_id,
            'label': f'Example {example}', 'smiles': _value(fields, 'smiles'),
            'role': _value(fields, 'role') or 'unspecified',
            'structure_source': {'pdf_page': page, 'locator': _loc(fields, 'smiles')},
            'mapping_source': {'pdf_page': page, 'locator': f'Example {example}'},
            'reported_mass': _float(_value(fields, 'reported_mass')),
            'review': _review(provenance, a)}))
    for slot, fields in _slots(result, 'observation'):
        a = result.assessments[slot]
        if a['missing']:
            refused.append({'slot': list(slot), 'reason': '必填字段缺失：' + '、'.join(a['missing'])})
            continue
        if slot[2] not in assay_ids:
            refused.append({'slot': list(slot), 'reason': '实验未对应到台账中的实验定义'})
            continue
        status = _value(fields, 'status')
        measured = status == 'measured'
        grade = measured and _value(fields, 'relation') == 'grade'
        items.append(('observations', {
            'id': f'{document_id}:extract:{slot[1]}:{slot[2]}', 'document_id': document_id,
            'compound_id': f'{document_id}:example:{slot[1]}', 'assay_id': assay_ids[slot[2]],
            'status': status, 'relation': _value(fields, 'relation') if measured else None,
            'value': _value(fields, 'value') if measured and not grade else None,
            'unit': _value(fields, 'unit') if measured and not grade else None,
            'grade': _value(fields, 'grade') if grade else None,
            'source': {'pdf_page': _page(fields, 'value', 'grade', 'status'), 'locator': _loc(fields, 'value', 'status')},
            'raw': {'extraction': {name: cell['support'] for name, cell in fields.items()}},
            'review': _review(provenance, a)}))
    return items, refused


def _loc(fields, *names):
    for name in names:
        for s in fields.get(name, {}).get('support', []):
            if s.get('locator'):
                return f"{s['level']} {s['source']}: {s['locator']}"
    return None


def _float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _review(provenance, assessment):
    gaps = ['independent_review', f"extraction_confidence:{assessment['confidence']}"]
    gaps += [f'extraction_flag:{f}' for f in assessment['flags']]
    return {'record_status': 'proposed', 'provenance_status': provenance, 'gaps': gaps}


def report(result, partition=None):
    """Markdown extraction report for the reviewer: what ran, what was filled, what needs a look."""
    lines = [f'# 抽取报告 · {result.publication}', '']
    if partition:
        lines += ['## 页面分区', '']
        for r in partition['ranges']:
            pages = f"{r['start']}–{r['end']}" if r['start'] != r['end'] else str(r['start'])
            lines.append(f"- {r['region']}：PDF 第 {pages} 页（依据：{'；'.join(r['evidence'][:2])}）")
        lines.append('')
    lines += ['## 各级调用', '', '| 级别 | 动作 | 请求 | 返回 | 接受 | 新填 | 互证 | 冲突 | 剩余缺失 |', '|---|---|---|---|---|---|---|---|---|']
    for e in result.log:
        if e['action'] == 'not_called':
            lines.append(f"| {e['level']} | 未调用（{e['reason']}） | | | | | | | |")
            continue
        action = e['action'] if e['action'] != 'failed' else f"失败：{e['error']}"
        lines.append(f"| {e['level']} | {action} | {e['requested']} | {e['returned']} | {e['accepted']} | "
                     f"{e['filled']} | {e['corroborated']} | {e['conflicts']} | {e.get('missing_after')} |")
    counts = {}
    for a in result.assessments.values():
        counts[a['confidence']] = counts.get(a['confidence'], 0) + 1
    lines += ['', '## 置信度', '', '、'.join(f'{k} {v}' for k, v in sorted(counts.items())) or '无记录', '']
    review = [(slot, a) for slot, a in result.assessments.items() if a['confidence'] != 'high']
    if review:
        lines += ['## 需要人工核对', '']
        order = {'low': 0, 'medium': 1}
        for slot, a in sorted(review, key=lambda x: (order[x[1]['confidence']], x[0][1], x[0][2] or '')):
            name = f"Example {slot[1]}" + (f" · {slot[2]}" if slot[2] else '')
            detail = '、'.join(a['flags']) or '仅单一来源'
            if a['missing']:
                detail += f"；缺失 {'、'.join(a['missing'])}"
            lines.append(f"- {name}：{a['confidence']}（{detail}）")
            for fname, cell in result.slots[slot].items():
                for c in cell['conflicts']:
                    lines.append(f"  - {fname} 冲突：{cell['value']!r}（{cell['support'][0]['level']}）"
                                 f" vs {c['value']!r}（{c['level']}）")
        lines.append('')
    if result.rejected:
        lines += ['## 被拒绝的抽取结果', '']
        lines += [f"- {r['level']} {'/'.join(str(x) for x in r['slot'] if x)} {r['field']}：{r['reason']}"
                  for r in result.rejected]
        lines.append('')
    lines.append('高置信度只表示多路独立证据一致；所有记录仍需独立复核后才能确认。')
    return '\n'.join(lines) + '\n'
