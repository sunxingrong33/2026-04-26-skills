"""Curated relations between source snapshots, never inferred from date order."""
import json
from .patent_evidence import DATA

EARLY = 'WO2011138751A2'
LATE = 'WO2013132376A1'
EXPECTED = {EARLY: ('44278717', '2010-05-04'), LATE: ('48142828', '2012-03-06')}


def build_lineage(documents):
    docs = {d['publication']: d for d in documents}
    groups = {}
    for d in docs.values():
        key = d.get('family_id') or d['publication']
        node = groups.setdefault(key, {'family_id': d.get('family_id'), 'publications': [],
                                      'priority_date': '', 'evidence_count': 0})
        node['publications'].append(d['publication'])
        date = d.get('priority_date')
        if date and (not node['priority_date'] or date < node['priority_date']):
            node['priority_date'] = date
        node['evidence_count'] += len(d.get('evidence_cards', []))
    result = {'nodes': sorted(groups.values(), key=lambda n: n['priority_date'] or '9999'),
              'edges': [], 'notice': '按已加载公开文本的优先权日排列；同族合并。日期先后不证明分子研发顺序。'}
    for pid, (family, date) in EXPECTED.items():
        d = docs.get(pid)
        package = json.loads((DATA / (pid + '.json')).read_text(encoding='utf8'))
        if (not d or d.get('family_id') != family or d.get('priority_date') != date
                or d.get('source_snapshot', {}).get('sha256') != package['source_html_sha256']
                or not d.get('evidence_cards')):
            return result
    if EARLY not in docs[LATE].get('references', []):
        return result
    result['edges'].append({
        'from': EARLY, 'to': LATE, 'status': 'related_series_not_direct_evolution',
        'label': '相关 ALK 系列 · 直接演化未证实',
        'facts': ['两份公开文本属于不同专利家族，申请人均为 Pfizer，涉及 ALK 抑制剂。',
                  '较晚公开文本的引用列表含较早专利；此处未判定引用由申请人还是审查员加入。',
                  '所选 Example 7 与 Example 6 共享氨基吡啶、吡唑及含氟芳基醚结构片段；后者含大环连接。'],
        'hypothesis': '待验证问题：大环化是否用于限制构象，并影响 ALK 抑制？这是基于所选结构的研究假设，不是原文确认的研发动机。',
        'gaps': ['未找到证明这两个实施例直接先后优化的材料。',
                 '跨专利实验批次与可比性未核实；不计算活性改善倍数。',
                 '结构转录与实验表尚待独立化学家复核；不能据此判断脑暴露、选择性或毒性。'],
        'sources': [
            {'label': '较早专利：家族、日期及申请人', 'url': docs[EARLY]['source_url']},
            {'label': '较晚专利：引用列表', 'url': docs[LATE]['source_url'] + '#patentCitations'},
            {'label': '早期 Example 7：通式 PDF p.127', 'url': 'https://patentimages.storage.googleapis.com/09/cd/ed/8c529f4bb6a3e6/WO2011138751A2.pdf#page=127'},
            {'label': '早期 Example 7：取代基行 PDF p.128', 'url': 'https://patentimages.storage.googleapis.com/09/cd/ed/8c529f4bb6a3e6/WO2011138751A2.pdf#page=128'},
            {'label': '大环 Example 6：最终结构 PDF p.268', 'url': 'https://patentimages.storage.googleapis.com/45/f2/58/34b02c33ca73a9/WO2013132376A1.pdf#page=268'},
        ],
        'pair': {'a': {'publication': EARLY, 'example': 7},
                 'b': {'publication': LATE, 'example': 6}},
    })
    return result
