"""Classify patent pages into regions before any extraction, and keep the evidence.

Only the pages that matter are handed to expensive extractors: activity tables,
example (final product) sections, and intermediate / preparation sections that
must *not* be mistaken for examples. Classification is keyword and pattern based
on page text; every decision keeps the lines that triggered it, so a reviewer can
correct a wrong range before extraction runs.

Input is a list of ``(pdf_page, text)``; text extraction from the PDF itself is a
separate adapter.
"""
import re
from dataclasses import dataclass, field

HEADINGS = {
    'example': re.compile(r'^\s*(?:Example|实施例)\s*(\d+[A-Za-z]?)\b', re.I | re.M),
    'intermediate': re.compile(r'^\s*(?:Intermediate|Preparation|Reference\s+Example|中间体|制备例)\s*(\d+[A-Za-z]?)\b',
                               re.I | re.M),
}
ACTIVITY_TERMS = re.compile(r'\b(IC50|IC 50|EC50|EC 50|Ki|Kd|Emax|E max|pIC50|% inhibition)\b', re.I)
TABLE_MARK = re.compile(r'\bTable\s*\d+\b|表\s*\d+', re.I)
UNIT = re.compile(r'\b(?:nM|µM|uM|μM|mM|pM|%)\b')
ASSAY_WORDS = re.compile(r'\b(assay|biological|activity|inhibition|binding|potency)\b', re.I)


@dataclass
class PageLabel:
    page: int
    region: str                     # activity_table / example / intermediate / other
    score: float
    evidence: list = field(default_factory=list)
    identifiers: list = field(default_factory=list)


def classify(page, text):
    """Label one page; ties favour 'other' so nothing is claimed without evidence."""
    lines = text.splitlines()
    examples = HEADINGS['example'].findall(text)
    intermediates = HEADINGS['intermediate'].findall(text)
    activity_lines = [l.strip() for l in lines if ACTIVITY_TERMS.search(l)]
    unit_lines = [l.strip() for l in lines if UNIT.search(l)]
    numeric_rows = [l.strip() for l in lines if re.match(r'^\s*\d+[A-Za-z]?\s+[<>≤≥~=]?\s*\d', l)]
    scores = {
        'activity_table': 2.0 * bool(TABLE_MARK.search(text)) + min(len(activity_lines), 3)
                          + min(len(numeric_rows), 5) * 0.6 + (1.0 if ASSAY_WORDS.search(text) and unit_lines else 0),
        'example': 2.0 * len(examples),
        'intermediate': 2.0 * len(intermediates),
    }
    region, score = max(scores.items(), key=lambda kv: kv[1])
    if score < 2.0:
        return PageLabel(page, 'other', score)
    evidence = {
        'activity_table': ([m.group(0) for m in TABLE_MARK.finditer(text)] + activity_lines + numeric_rows)[:6],
        'example': [f'Example {e}' for e in examples][:6],
        'intermediate': [f'Intermediate {e}' for e in intermediates][:6],
    }[region]
    identifiers = {'activity_table': [], 'example': examples, 'intermediate': intermediates}[region]
    return PageLabel(page, region, round(score, 2), evidence, identifiers)


def partition(pages):
    """Label every page and merge consecutive pages of the same region into ranges."""
    labels = [classify(p, t) for p, t in sorted(pages)]
    ranges = []
    for label in labels:
        last = ranges[-1] if ranges else None
        if label.region != 'other' and last and last['region'] == label.region and last['end'] == label.page - 1:
            last['end'] = label.page
            last['evidence'] += label.evidence[:2]
            last['identifiers'] += label.identifiers
        elif label.region != 'other':
            ranges.append({'region': label.region, 'start': label.page, 'end': label.page,
                           'evidence': label.evidence[:3], 'identifiers': list(label.identifiers)})
    return {'pages': [label.__dict__ for label in labels], 'ranges': ranges,
            'notice': '页面分区基于关键词与版式特征，是抽取前的定位建议，需可人工修正；未识别的页面不代表没有相关内容。'}


def pages_for(result, region):
    return [p for r in result['ranges'] if r['region'] == region for p in range(r['start'], r['end'] + 1)]
