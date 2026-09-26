"""Small, explicitly reviewed patent evidence sets; never inferred from index order."""
import json
from pathlib import Path
from .features import compute_features
from .units import normalise

DATA = Path(__file__).resolve().parents[1]/'data'/'patent_evidence'

def attach_evidence(result):
    from .report import molecule_svg
    path=DATA/(result['publication']+'.json')
    result['evidence_cards']=[]
    result['evidence_status']='尚无逐项核对的结构—实施例—活性映射。'
    if not path.exists():return
    package=json.loads(path.read_text(encoding='utf-8'))
    if result['source_snapshot']['sha256'] != package['source_html_sha256']:
        result['evidence_status']='来源快照已变化，已暂停使用旧映射；需要重新核对。'
        return
    assays={a['id']:a for a in package['assays']}
    for card in package['cards']:
        features=compute_features(card['smiles'])
        card['formula']=features.formula
        card['inchikey']=features.inchikey
        card['features']=features.numeric
        card['svg']=molecule_svg(card['smiles'])
        card['review_status']=package['review']['status']
        for m in card['measurements']:
            m['assay']=assays[m['assay_id']]
            m['source_url']=card['table_source']['url']
            m['locator']=card['table_source']['locator']
        result['evidence_cards'].append(card)
    result['evidence_status']='3 个实施例已由助手对照原始 PDF 图表转录；尚未经过独立化学家复核。'
    result['evidence_review']=package['review']
    result['evidence_pdf_sha256']=package['source_pdf_sha256']

def compare_measurements(a,b):
    """Only matching patent protocols; censored numbers never enter ratios."""
    output=[]
    right={m['assay_id']:m for m in b['measurements']}
    for m in a['measurements']:
        n=right.get(m['assay_id'])
        if not n:continue
        x,u=normalise(m['value'],m['unit']);y,v=normalise(n['value'],n['unit'])
        exact=m['relation']=='=' and n['relation']=='='
        comparable=u==v and m['assay']==n['assay']
        output.append({'assay_id':m['assay_id'],'label':m['assay']['label'],
            'from':m['raw'],'to':n['raw'],'unit':u,
            'ratio':y/x if exact and comparable and x>0 else None,
            'note':'精确值 B/A；不是改善倍数' if exact and comparable else '限定值或协议不一致，不计算倍数',
            'source_from':{'url':m['source_url'],'locator':m['locator']},
            'source_to':{'url':n['source_url'],'locator':n['locator']}})
    return output

def provisional_direction(rows):
    cells=[r for r in rows if r['assay_id'].startswith('cell_')]
    if len(cells)==2 and all(r['ratio'] is not None and r['ratio']<1 for r in cells):
        return {'claim':'待复核候选方向：增强细胞 ALK 抑制。B 的两项细胞 IC50 均低于 A；这只是所选分子的实测关联，不证明真实优化顺序、单一结构改动的因果作用或作者动机。',
                'status':'provisional_pending_independent_review','evidence':cells}
    return {'claim':'现有对照不足以提出一致的细胞活性改善方向；不推断研发动机。',
            'status':'insufficient_evidence','evidence':cells}
