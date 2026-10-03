"""PubChem cross-references: which patents and PubMed articles a compound is linked to.

Access and terms are recorded in docs/pubchem-access.md. PUG-REST, no key;
at most 5 requests per second and 400 per minute; NCBI places no restriction
on use, but depositors may hold rights in what they submitted, and the source
should be attributed. This module makes at most three requests per lookup
(InChIKey -> CIDs, then patent and PubMed xrefs), one lookup at a time.

PubChem xrefs are deposited links: they say a compound record is associated
with a patent or an article, not that the compound is an example, claimed or
tested there. Patent links largely come from the same kind of automated
extraction as SureChEMBL, so the two are shown side by side and never merged
or counted as independent confirmation. Lookup is by exact standard InChIKey:
salts, other tautomers or stereoisomers may sit under other CIDs.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug'
SITE = 'https://pubchem.ncbi.nlm.nih.gov'
MAX_CIDS = 5
ATTRIBUTION = '数据来自 PubChem（NCBI/NLM）；部分记录由提交者提供，可能受其权利约束，引用时请注明 PubChem 与 CID。'
NOTICE = ('PubChem 交叉引用是提交者登记的关联：只说明该化合物记录与专利或文献有关，不说明它是实施例、被权利要求覆盖或经过测试。'
          '专利关联与 SureChEMBL 可能同源，两者并列显示、不合并，也不当作相互独立的佐证。'
          '按标准 InChIKey 精确查找：盐型、其他互变异构或立体异构体可能在别的 CID 下。')
INCHIKEY = re.compile(r'[A-Z]{14}-[A-Z]{10}-[A-Z]')


class PubChemError(RuntimeError):
    pass


def inchikey(value):
    if not isinstance(value, str) or not INCHIKEY.fullmatch(value.strip()):
        raise ValueError('请提供标准 InChIKey（形如 XXXXXXXXXXXXXX-XXXXXXXXXX-X）。')
    return value.strip()


def _get(path, cache):
    """GET one PUG-REST path, cached by URL. 'Not found' is an answer and is cached; other errors are not."""
    url = API + path
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    file = cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    hit = file.exists()
    if not hit:
        try:
            with urlopen(Request(url, headers={'User-Agent': 'SAR-Atlas/0.3 (research prototype)'}),
                         timeout=30) as response:
                raw = response.read(5 * 1024 * 1024 + 1)
        except HTTPError as exc:
            body = exc.read(64 * 1024)
            try:
                code = (json.loads(body).get('Fault') or {}).get('Code')
            except ValueError:
                code = None
            if exc.code == 404 and code == 'PUGREST.NotFound':
                raw = body
            else:
                raise PubChemError(f'PubChem 返回 HTTP {exc.code}（{code or "无说明"}）') from None
        if len(raw) > 5 * 1024 * 1024:
            raise PubChemError('来源响应超过大小限制')
        json.loads(raw)
        file.write_bytes(raw)
    raw = file.read_bytes()
    source = {'url': url, 'sha256': hashlib.sha256(raw).hexdigest(),
              'retrieved_at': datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat(),
              'cache_hit': hit}
    return json.loads(raw), source


def _not_found(payload):
    return (payload.get('Fault') or {}).get('Code') == 'PUGREST.NotFound'


def _xrefs(payload, kind):
    values = []
    for info in (payload.get('InformationList') or {}).get('Information') or []:
        values.extend(info.get(kind) or [])
    return list(dict.fromkeys(str(v) for v in values))


def lookup(key, cache, limit=20, curated=()):
    """Patents and PubMed articles linked to the compound with this standard InChIKey."""
    from .patents import normalize_id
    key = inchikey(key)
    payload, source = _get(f'/compound/inchikey/{quote(key)}/cids/JSON', cache)
    sources = [source]
    cids = [] if _not_found(payload) else [int(c) for c in (payload.get('IdentifierList') or {}).get('CID') or []]
    result = {'inchikey': key, 'cids': cids[:MAX_CIDS], 'more_cids': max(0, len(cids) - MAX_CIDS),
              'notice': NOTICE, 'attribution': ATTRIBUTION, 'sources': sources,
              'patents': {'total': 0, 'truncated': False, 'rows': [], 'curated_matches': []},
              'literature': {'total': 0, 'truncated': False, 'rows': []}}
    if not cids:
        return result
    joined = ','.join(map(str, cids[:MAX_CIDS]))
    patents, source = _get(f'/compound/cid/{joined}/xrefs/PatentID/JSON', cache)
    sources.append(source)
    rows = []
    for pid in ([] if _not_found(patents) else _xrefs(patents, 'PatentID')):
        try:
            publication = normalize_id(pid)
        except ValueError:
            publication = None
        rows.append({'patent_id': pid, 'publication': publication, 'url': f'{SITE}/patent/{quote(pid)}',
                     'curated': publication in curated})
    rows.sort(key=lambda r: (not r['curated'], r['patent_id']))
    result['patents'] = {'total': len(rows), 'truncated': len(rows) > limit, 'rows': rows[:limit],
                         'curated_matches': [r['publication'] for r in rows if r['curated']]}
    articles, source = _get(f'/compound/cid/{joined}/xrefs/PubMedID/JSON', cache)
    sources.append(source)
    pmids = sorted((p for p in ([] if _not_found(articles) else _xrefs(articles, 'PubMedID')) if p.isdigit()),
                   key=int, reverse=True)
    result['literature'] = {'total': len(pmids), 'truncated': len(pmids) > limit,
                            'rows': [{'pmid': p, 'url': f'https://pubmed.ncbi.nlm.nih.gov/{p}/'} for p in pmids[:limit]]}
    return result


def curated_publications():
    """Publication numbers of the curated evidence packages, highlighted when PubChem links them."""
    from .patent_evidence import DATA
    return frozenset(p.stem for p in DATA.glob('*.json'))
