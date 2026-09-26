"""Read-only Google Patents retrieval with explicit extraction boundaries."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from bs4 import BeautifulSoup
from rdkit import Chem
from .features import compute_features, StructureError
from .report import molecule_svg

BASE = 'https://patents.google.com/patent/'
MAX_BYTES = 12 * 1024 * 1024

def normalize_id(value):
    value = re.sub(r'[\s/-]', '', value.upper())
    if not re.fullmatch(r'[A-Z]{2}\d{5,12}[A-Z]\d{0,2}', value):
        raise ValueError('请输入完整公开号（含 A1/B2 等后缀），例如 WO2013132376A1；暂不接受申请号或网址。')
    return value

def parse_patent(html, publication):
    publication = normalize_id(publication)
    soup = BeautifulSoup(html, 'html.parser')
    def meta(name, scheme=None):
        attrs = {'name': name}
        if scheme is not None:
            attrs['scheme'] = scheme
        node = soup.find('meta', attrs=attrs)
        return node.get('content', '').strip() if node else ''
    found = re.sub(r'[^A-Z0-9]', '', meta('citation_patent_publication_number').upper())
    if not found:
        identifier = soup.select_one('dd[itemprop="publicationNumber"]')
        found = identifier.get_text(strip=True) if identifier else ''
    if found != publication:
        raise ValueError('上游未返回匹配的专利页面，可能公开号不存在或来源暂不可用。')
    url = BASE + publication + '/en'
    priority = soup.select_one('time[itemprop="priorityDate"]')
    priority_date = priority.get('datetime', '') if priority else ''
    if priority_date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', priority_date):
        priority_date = ''
    family = soup.select_one('section[itemprop="family"]')
    family_id = re.search(r'ID\s*=\s*(\d+)', family.get_text(' ',strip=True)) if family else None
    family_members = []
    if family:
        for node in family.select('[itemprop="representativePublication"]'):
            candidate = node.get_text(strip=True)
            if re.fullmatch(r'[A-Z]{2}\d{5,12}[A-Z]\d{0,2}', candidate) and candidate not in family_members:
                family_members.append(candidate)
    paragraphs = soup.select('.description-paragraph, .description heading')
    # Some publication layouts use <p> inside the description instead.
    if not paragraphs:
        paragraphs = soup.select('[itemprop="description"] p')
    examples = []
    for i, node in enumerate(paragraphs):
        text = node.get_text(' ', strip=True)
        if not re.fullmatch(r'(?:Examples?|实施例)\s*\d+[A-Za-z]?(?:\s*(?:and|&|,|、|和|to|[-–])\s*(?:Example\s*)?\d+[A-Za-z]?)*\s*[:.：]?', text, re.I):
            continue
        context = []
        for part in paragraphs[i + 1:i + 9]:
            line = part.get_text(' ', strip=True)
            if re.match(r'^(?:Examples?|实施例)\s*\d+\s*$', line, re.I) or line.startswith('Preparation of'):
                break
            context.append(line)
        examples.append({'label': text, 'locator': node.get('num') or node.get('id') or f'description paragraph {i+1}',
            'excerpt': ' '.join(context)[:1600], 'source_url': url + '#:~:text=' + quote(text),
            'structure_mapping': 'unresolved'})
    structures, seen, invalid = [], set(), 0
    for item in soup.select('[itemprop="match"]'):
        sm = item.select_one('[itemprop="smiles"]')
        if not sm:
            continue
        smiles = sm.get_text(strip=True)
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            invalid += 1
            continue
        # Markush/query structures cannot be treated as specific compounds.
        if any(a.GetAtomicNum() == 0 for a in mol.GetAtoms()):
            invalid += 1
            continue
        canonical = Chem.MolToSmiles(mol)
        if canonical in seen:
            continue
        seen.add(canonical)
        if len(structures) >= 120:
            continue
        try:
            features = compute_features(canonical)
        except StructureError:
            invalid += 1
            continue
        key = item.select_one('[itemprop="inchi_key"]')
        structures.append({'smiles': canonical, 'inchikey': features.inchikey,
            'source_inchikey': key.get_text(strip=True) if key else '',
            'formula': features.formula, 'features': features.numeric, 'svg': molecule_svg(canonical),
            'source_url': url, 'example': None,
            'status': '自动化学实体索引；可能包含试剂、中间体或背景结构，尚未映射实施例'})
    references = []
    for node in soup.select('meta[name="DC.relation"][scheme="references"]'):
        ref = re.sub(r'[^A-Z0-9]', '', node.get('content','').upper())
        if re.fullmatch(r'[A-Z]{2}\d{5,12}[A-Z]\d{0,2}', ref) and ref not in references:
            references.append(ref)
    dates = [n.get('content','') for n in soup.select('meta[name="DC.date"]') if not n.get('scheme')]
    return {'publication': publication, 'title': meta('DC.title'),
        'assignee': meta('DC.contributor','assignee'), 'priority_date': priority_date,
        'filing_date': meta('DC.date','dateSubmitted'), 'publication_date': dates[0] if dates else meta('DC.date','issue'),
        'family_id': family_id.group(1) if family_id else None, 'family_members': family_members,
        'references': references, 'source_url': url, 'pdf_url': meta('citation_pdf_url'),
        'examples': examples, 'structures': structures, 'invalid_or_query_structures': invalid,
        'structure_limit_reached': len(seen) > 120,
        'limitations': ['优先权日期取自 Google Patents 元数据，未做法律核验。',
            '同一家族的多国公开不等于不同研发代际；引用专利也不证明直接演化关系。',
            '实施例提取依赖可识别的标题与 OCR 文本，可能漏项；请核对原文 PDF。',
            '结构来自页面自动实体索引，未建立结构—实施例—活性表格的可靠对应。',
            '未确认连续分子路线前，不自动生成“他们在解什么问题”的结论。']}

def retrieve(publication, cache: Path):
    publication = normalize_id(publication)
    cache.mkdir(parents=True, exist_ok=True)
    raw_path = cache / f'{publication}.html'
    manifest_path = cache / f'{publication}.source.json'
    url = BASE + publication + '/en'
    cached = raw_path.exists() and manifest_path.exists()
    if cached:
        raw = raw_path.read_bytes()
        source = json.loads(manifest_path.read_text(encoding='utf-8'))
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError('缓存校验失败，请移走对应缓存后重新检索。')
    else:
        try:
            with urlopen(Request(url, headers={'User-Agent':'SAR-Evidence-Research/0.3'}), timeout=35) as response:
                raw = response.read(MAX_BYTES + 1)
        except HTTPError as exc:
            raise ValueError(f'专利来源返回 HTTP {exc.code}，请核对公开号或稍后重试；未使用样例替代。') from exc
        except (URLError, TimeoutError) as exc:
            raise ValueError('专利来源连接失败或超时，请稍后重试。') from exc
        if len(raw) > MAX_BYTES:
            raise ValueError('专利页面超出 12 MB 读取上限。')
        source = {'url': url, 'sha256': hashlib.sha256(raw).hexdigest(),
                  'retrieved_at': datetime.now(timezone.utc).isoformat()}
    result = parse_patent(raw.decode('utf-8'), publication)
    if not cached:
        raw_path.write_bytes(raw)
        manifest_path.write_text(json.dumps(source, indent=2), encoding='utf-8')
    result['source_snapshot'] = source | {'cache_hit': cached}
    (cache / f'{publication}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
    return result
