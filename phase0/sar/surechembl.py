"""SureChEMBL: structure search over chemistry extracted from patents, and the
patents a compound was found in.

Access and licence are recorded in docs/surechembl-access.md. In short: public
REST API at https://www.surechembl.org/api, no key, no published quota; data
CC BY 4.0 (attribute SureChEMBL, keep SCHEMBL ids). The endpoint behaviour
below is not in the OpenAPI spec; it follows the live-verified notes of an
independent client (scigantic-surechembl, 2026-09-08) and has NOT yet been
checked from this project -- ``phase0.tools.verify_online`` does that.

- Every JSON response is an envelope ``{status, data, error_message}``;
  anything but ``status == "OK"`` is an error.
- Structure search is an asynchronous job: ``POST /search/structure`` with the
  body wrapped as ``{"StructureSearchRequest": {"struct", "structSearchType"}}``
  returns a hash; ``GET /search/{hash}/status`` says "Searching finished." with
  ``resultCount`` (or a message containing "error"); ``GET
  /search/{hash}/results`` pages the hits. The server caps a search at 10,000
  hits and returns similarity hits in its own order, not by score.
- ``POST /search/documents_for_structures`` lists documents containing a
  compound, with ``total_hits``.

A SureChEMBL hit means the structure was *extracted* from a patent's text or
images by automated chemistry annotation. It does not make the compound an
example, a claimed compound or a tested one; extraction errors are possible.

Only one request is in flight at a time (the server's work lock) and polling
backs off from 0.5 s to 5 s. Very broad substructure queries are refused
upstream by ``structure_search.query`` (>= 6 heavy atoms): trivially broad
jobs have been observed to stall the shared service.
"""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

API = 'https://www.surechembl.org/api'
SITE = 'https://www.surechembl.org'
MODES = {'exact': 'identical', 'similarity': 'similarity', 'substructure': 'substructure'}
CAP = 10_000
ATTRIBUTION = '数据来自 SureChEMBL（EMBL-EBI，CC BY 4.0）；保留 SCHEMBL 编号，引用时请注明 surechembl.org。'
NOTICE = ('SureChEMBL 命中表示该结构由自动化学标注从专利文本或图像中提取，不说明它是实施例、被权利要求覆盖或经过测试，'
          '提取本身也可能有误。')
SLEEP = time.sleep  # patched in tests
TIMEOUT = 120


class SureChEMBLError(RuntimeError):
    pass


def _call(method, path, params=None, body=None, timeout=60):
    url = API + path
    if params:
        from urllib.parse import urlencode
        url += '?' + urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {'User-Agent': 'SAR-Atlas/0.3 (research prototype)', 'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    with urlopen(Request(url, data=data, headers=headers, method=method), timeout=timeout) as response:
        raw = response.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise SureChEMBLError('来源响应超过大小限制')
    env = json.loads(raw)
    if not isinstance(env, dict) or str(env.get('status')) != 'OK':
        message = env.get('error_message') if isinstance(env, dict) else None
        raise SureChEMBLError(f"SureChEMBL 返回 {env.get('status') if isinstance(env, dict) else '非 JSON'}："
                              f"{message or '无说明'}")
    return env.get('data')


def _cached(cache, key, produce):
    """Cache the assembled answer under the query, so a search replays without the network.

    The job hash is server state and is never part of the result."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest() + '.json')
    hit = path.exists()
    if not hit:
        payload = produce()
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    raw = path.read_bytes()
    source = {'url': API + key[0], 'query': key[1:], 'sha256': hashlib.sha256(raw).hexdigest(),
              'retrieved_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
              'cache_hit': hit}
    return json.loads(raw), source


def _wait(search_hash):
    deadline = time.monotonic() + TIMEOUT
    wait = 0.5
    while True:
        status = _call('GET', f'/search/{search_hash}/status') or {}
        message = str(status.get('message', ''))
        if message.startswith('Searching finished'):
            return int(status.get('resultCount') or 0)
        if 'error' in message.lower():
            raise SureChEMBLError(f'检索任务失败：{message}')
        if time.monotonic() > deadline:
            raise SureChEMBLError(f'检索 {TIMEOUT} 秒内未完成（最后状态：{message or "未知"}）')
        SLEEP(wait)
        wait = min(wait * 2, 5.0)


def search(smiles, method, cache, limit=20):
    """First ``limit`` hits of one structure search; returns (payload, source)."""
    mode = MODES[method]

    def produce():
        job = _call('POST', '/search/structure',
                    body={'StructureSearchRequest': {'struct': smiles, 'structSearchType': mode}}) or {}
        total = _wait(str(job['hash']))
        page = _call('GET', f"/search/{job['hash']}/results", params={'page': 1, 'max_results': limit}) or {}
        records, seen = [], set()
        for r in (page.get('results') or {}).get('structures') or []:
            if str(r.get('id')) not in seen:  # pages past the end repeat; de-duplicate
                seen.add(str(r.get('id')))
                records.append(r)
        return {'total': total, 'records': records[:limit]}

    return _cached(cache, ('/search/structure', smiles, mode, limit), produce)


def schembl_id(value):
    try:
        n = int(str(value).upper().removeprefix('SCHEMBL'))
    except ValueError:
        raise ValueError('请选择有效的 SureChEMBL 化合物编号。') from None
    if n <= 0:
        raise ValueError('请选择有效的 SureChEMBL 化合物编号。')
    return n


def documents(compound, cache, limit=20):
    """Documents SureChEMBL found this compound in; returns (payload, source)."""
    n = schembl_id(compound)

    def produce():
        data = _call('POST', '/search/documents_for_structures',
                     params={'chemicalIds': n, 'page': 1, 'itemsPerPage': limit}) or {}
        results = data.get('results') or {}
        return {'total': int(results.get('total_hits') or 0), 'documents': (results.get('documents') or [])[:limit]}

    return _cached(cache, ('/search/documents_for_structures', n, limit), produce)


def compound_row(r):
    similarity = r.get('similarity')
    try:
        similarity = None if similarity in (None, '') else float(similarity)
    except (TypeError, ValueError):
        similarity = None
    n = int(float(r.get('id', r.get('chemical_id'))))
    return {'schembl_id': f'SCHEMBL{n}', 'url': f'{SITE}/chemical/{n}', 'smiles': r.get('smiles') or None,
            'inchi_key': r.get('inchi_key') or None, 'name': r.get('name') or None,
            'surechembl_similarity': similarity}


def document_row(d):
    from .patents import normalize_id
    meta = d.get('metadata') or {}
    title = None
    for entry in meta.get('titles') or []:
        titles = entry.get('titles') or []
        if titles and (entry.get('lang') == 'en' or title is None):
            title = titles[0]
    doc = str(d.get('docId', ''))
    try:
        publication = normalize_id(doc)
    except ValueError:
        publication = None
    date = meta.get('pd')
    assignee = d.get('pa')
    return {'doc_id': doc, 'url': f'{SITE}/patent/{doc}', 'publication': publication, 'title': title,
            'publication_date': None if date in (None, '', 'null') else str(date),
            'assignee': None if assignee in (None, '', 'null') else str(assignee)}
