"""Loopback-only interactive patent explorer: python -m phase0.sar.serve."""
from __future__ import annotations
import argparse
import json
import os
import re
import threading
from rdkit import Chem
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from .patents import retrieve, normalize_id
from .report import ROOT, structure_pair
from .features import FEATURE_LABELS
from .lineage import build_lineage
from .discovery import discover, discover_documents
from .programs import analyse_programs
from .scaffolds import align_evidence
from phase0.ledger.access import ENV as LEDGER_ENV, analysis_view
from phase0.ledger.intake import from_activities, from_patent, from_surechembl
from phase0.ledger.store import KINDS, LedgerStore
from .evidence_pair import analyse_pair
from .sar_workflow import run_workflow
from .patent_evidence import compare_measurements, provisional_direction
from . import study as studies

WEB = ROOT/'phase0/web'
APP = WEB/'app'
APP_PAGES = {'/': 'home.html', '/index.html': 'home.html', '/search': 'search.html', '/upload': 'upload.html',
             '/review': 'review.html'}
STUDY_TABS = ('overview', 'evidence', 'analysis', 'timeline', 'compare', 'report')
STUDY_PAGE = re.compile(r'/s/([a-z0-9-]{1,60})/(' + '|'.join(STUDY_TABS) + ')')
APP_ASSET = re.compile(r'/app/([a-z0-9-]{1,40})\.(css|js)')
CLASSIC = {'/classic': 'patents.html', '/classic/evidence': 'evidence.html', '/classic/project': 'project.html',
           '/evidence': 'evidence.html', '/project': 'project.html'}
ASSET_TYPES = {'css': 'text/css; charset=utf-8', 'js': 'text/javascript; charset=utf-8'}

class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body, content_type='application/json; charset=utf-8'):
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8') if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self):
        host = self.headers.get('Host','')
        valid = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origins = {'http://' + v for v in valid}
        # An explicitly named forwarding host (e.g. a GitHub Codespaces port URL) is served over https.
        valid |= self.server.public_hosts
        origins |= {'https://' + h for h in self.server.public_hosts}
        origin = self.headers.get('Origin')
        return host in valid and (not origin or origin in origins)

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error':'仅接受本机同源请求。'})
        url = urlsplit(self.path)
        if url.path in APP_PAGES:
            return self.reply(200, (APP/APP_PAGES[url.path]).read_bytes(), 'text/html; charset=utf-8')
        page = STUDY_PAGE.fullmatch(url.path)
        if page:
            return self.reply(200, (APP/(page.group(2) + '.html')).read_bytes(), 'text/html; charset=utf-8')
        asset = APP_ASSET.fullmatch(url.path)
        if asset:
            path = APP/(asset.group(1) + '.' + asset.group(2))
            return self.reply(200, path.read_bytes(), ASSET_TYPES[asset.group(2)]) if path.exists() \
                else self.reply(404, {'error': '未找到文件。'})
        if url.path in CLASSIC:
            return self.reply(200, (WEB/CLASSIC[url.path]).read_bytes(), 'text/html; charset=utf-8')
        if url.path.startswith('/api/') and url.path in STUDY_GET:
            return STUDY_GET[url.path](self, parse_qs(url.query))
        if url.path == '/programs.js':
            return self.reply(200, (ROOT/'phase0/web/programs.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/project.js':
            return self.reply(200, (ROOT/'phase0/web/project.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/evidence-workflow.js':
            return self.reply(200, (ROOT/'phase0/web/evidence-workflow.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/sar-workflow.js':
            return self.reply(200, (ROOT/'phase0/web/sar-workflow.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/api/evidence':
            try:
                return self.reply(200, analysis_view())
            except (ValueError, KeyError, OSError, LookupError):
                return self.reply(422, {'error': '证据包读取或校验失败，未返回部分台账。'})
        if url.path == '/discovery.js':
            return self.reply(200, (ROOT/'phase0/web/discovery.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/examples':
            path = ROOT/'artifacts/sar-explorer.html'
            return self.reply(200, path.read_bytes(), 'text/html; charset=utf-8') if path.exists() else self.reply(404, {'error':'请先运行 python -m phase0.sar.demo'})
        if url.path == '/api/ledger/status':
            return self.ledger_status()
        if url.path == '/api/health':
            return self.reply(200, {'service':'SAR patent explorer'})
        if url.path == '/api/lineage':
            ids = parse_qs(url.query).get('id', [])
            if not ids or len(ids) > 16 or any(pid not in self.server.results for pid in ids):
                return self.reply(400, {'error':'请先检索所选专利；服务重启后需重新检索。'})
            return self.reply(200, build_lineage([self.server.results[pid] for pid in ids]))
        if url.path != '/api/patent':
            return self.reply(404, {'error':'未找到页面。'})
        query = parse_qs(url.query)
        try:
            pid = normalize_id(query.get('id',[''])[0])
        except ValueError as exc:
            return self.reply(400, {'error':str(exc)})
        if not self.server.work_lock.acquire(blocking=False):
            return self.reply(429, {'error':'正在检索另一份专利，请稍后重试。'})
        try:
            result = retrieve(pid, self.server.cache)
            self.server.results[pid] = result
            if len(self.server.results) > 16:
                del self.server.results[next(iter(self.server.results))]
            self.reply(200, result)
        except ValueError as exc:
            self.reply(422, {'error':str(exc)})
        except Exception:
            self.reply(502, {'error':'来源解析失败，未生成推断。请查看公开号对应的原始专利。'})
        finally:
            self.server.work_lock.release()

    def do_POST(self):
        if not self.allowed():
            return self.reply(403, {'error':'仅接受本机同源请求。'})
        if self.path in ('/api/evidence-pair', '/api/sar-workflow'):
            return self.evidence_pair_request()
        if self.path in ('/api/programs', '/api/scaffolds'):
            return self.analysis_request()
        if self.path == '/api/discover':
            return self.discover_request()
        if self.path == '/api/project-sar':
            return self.project_sar_request()
        if self.path == '/api/ledger/propose':
            return self.ledger_propose()
        if self.path in STUDY_POST:
            return STUDY_POST[self.path](self)
        if self.path != '/api/compare':
            return self.reply(404, {'error':'未找到接口。'})
        try:
            size = int(self.headers.get('Content-Length','0'))
            if size < 1 or size > 2048:
                return self.reply(400, {'error':'请求大小无效。'})
            request = json.loads(self.rfile.read(size))
            selected = []
            kinds = []
            publications = []
            for field in ('a','b'):
                value = request[field]
                index = value['index']
                if type(index) is not int or index < 0:
                    raise ValueError()
                kind=value.get('kind','index')
                if kind not in ('index','evidence'):raise ValueError()
                pid=normalize_id(value['publication'])
                selected.append(self.server.results[pid]['evidence_cards' if kind=='evidence' else 'structures'][index])
                kinds.append(kind)
                publications.append(pid)
            identities = [Chem.MolToSmiles(Chem.MolFromSmiles(c['smiles']), isomericSmiles=True) for c in selected]
            if identities[0] == identities[1]:
                return self.reply(400, {'error':'A 和 B 是同一个分子，请选择两个不同的分子。'})
            result = structure_pair(selected[0]['smiles'],selected[1]['smiles'])
            result['deltas'] = [{'name':FEATURE_LABELS.get(key,key),'from':value,'to':selected[1]['features'][key],
                'delta':round(selected[1]['features'][key]-value,4)} for key,value in selected[0]['features'].items()
                if key in selected[1]['features'] and selected[1]['features'][key] != value]
            result['interpretation'] = '这是用户选择的索引结构对照；尚无实施例映射和同协议实验数据，不能推断真实研发意图或改善效果。'
            if kinds == ['evidence','evidence']:
                result['measurements']=compare_measurements(*selected)
                result['candidate']=provisional_direction(result['measurements'])
                result['interpretation']='结构与测量已关联至明确实施例，助手已核对 PDF；独立复核尚未完成。这是指定分子对照，不是已证实的历史步骤。'
                result['measurement_title']='同专利协议实测对照（转录待独立复核）'
                if publications[0] != publications[1]:
                    result['measurement_title']='跨专利测量并列（可比性待核实）'
                    result['interpretation']='来自不同专利的实施例；结构差异与原始测量并列展示，不计算跨专利改善倍数，不认定直接演化。'
                    result['candidate']={'status':'insufficient_evidence','claim':'跨专利测量可比性未核实，不能据此确认活性改善或研发动机。','evidence':[]}
            self.reply(200,result)
        except (KeyError, IndexError, TypeError, ValueError):
            self.reply(400, {'error':'请选择两项已检索的结构；服务重启后需要重新检索。'})

    def project_sar_request(self):
        from .project_sar import handle
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 32000:
                raise ValueError('请求大小无效。')
            request = json.loads(self.rfile.read(size))
        except ValueError:
            return self.reply(400, {'error': '请求格式或大小无效。'})
        if not self.server.work_lock.acquire(blocking=False):
            return self.reply(429, {'error': '正在处理另一请求，请稍后重试。'})
        try:
            self.reply(200, handle(request))
        except ValueError as exc:
            self.reply(400, {'error': str(exc)})
        except Exception:
            self.reply(422, {'error': '分析未完成，未用默认结果替代。'})
        finally:
            self.server.work_lock.release()

    def evidence_pair_request(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16000:
                raise ValueError('请求大小无效。')
            request = json.loads(self.rfile.read(size))
        except ValueError:
            return self.reply(400, {'error': '请求格式或大小无效。'})
        if not self.server.work_lock.acquire(blocking=False):
            return self.reply(429, {'error': '正在处理另一请求，请稍后重试。'})
        try:
            self.reply(200, run_workflow(request) if self.path == '/api/sar-workflow' else analyse_pair(request))
        except ValueError as exc:
            self.reply(400, {'error': str(exc)})
        except Exception:
            self.reply(422, {'error': '对照未完成，请核对证据包；未用默认结果替代。'})
        finally:
            self.server.work_lock.release()

    def analysis_request(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            ids = data['publications']
            if not isinstance(ids, list) or not 1 <= len(ids) <= 16 or any(not isinstance(pid, str) for pid in ids):
                raise ValueError()
            if len(set(ids)) != len(ids):
                raise ValueError()
            docs = [self.server.results[normalize_id(pid)] for pid in ids]
        except (KeyError, TypeError, ValueError):
            return self.reply(400, {'error': '请选择 1–16 份已检索且不重复的专利；重启后需要重新检索。'})
        if not self.server.work_lock.acquire(blocking=False):
            return self.reply(429, {'error': '正在处理另一请求，请稍后重试。'})
        try:
            result = analyse_programs(docs) if self.path == '/api/programs' else align_evidence(docs)
            result['input_snapshots'] = [{'publication': d['publication'], 'source': d.get('source_snapshot')} for d in docs]
            self.reply(200, result)
        except ValueError as exc:
            self.reply(400, {'error': str(exc)})
        except Exception:
            self.reply(422, {'error': '分析未完成；不会用默认分组或 R 位点替代失败结果。'})
        finally:
            self.server.work_lock.release()

    def discover_request(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16000:
                raise ValueError('请求大小无效。')
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict):
                raise ValueError('请求格式无效。')
        except (ValueError, TypeError):
            return self.reply(400, {'error': '请输入有效的检索内容。'})
        if not self.server.work_lock.acquire(blocking=False):
            return self.reply(429, {'error': '正在检索，请稍后重试。'})
        try:
            self.reply(200, discover(request, self.server.cache.parent / 'discovery-cache'))
        except ValueError as exc:
            self.reply(400, {'error': str(exc)})
        except Exception:
            self.reply(502, {'error': 'ChEMBL 暂不可用或解析失败，请稍后重试；未返回样例。'})
        finally:
            self.server.work_lock.release()



    def ledger_store(self):
        path = self.server.ledger_db or os.environ.get(LEDGER_ENV)
        if not path:
            return None
        store = LedgerStore(path)
        return None if store.is_empty() else store

    def ledger_status(self):
        store = self.ledger_store()
        if store is None:
            return self.reply(200, {'enabled': False,
                'notice': '未启用台账数据库；用 python -m phase0.sar.serve --ledger-db artifacts/ledger.sqlite 启动。'})
        ledger = store.load()
        counts = {k: {} for k in KINDS}
        for k in KINDS:
            for r in getattr(ledger, k):
                counts[k][r.review.record_status] = counts[k].get(r.review.record_status, 0) + 1
        return self.reply(200, {'enabled': True, 'counts': counts})

    def ledger_propose(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise ValueError()
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict) or request.get('source') not in ('patent', 'activities', 'surechembl'):
                raise ValueError()
        except (ValueError, TypeError):
            return self.reply(400, {'error': '请求格式无效。'})
        store = self.ledger_store()
        if store is None:
            return self.reply(409, {'error': '未启用台账数据库，未写入任何记录。请用 --ledger-db 启动服务。'})
        if not self.server.work_lock.acquire(blocking=False):
            return self.reply(429, {'error': '正在处理另一请求，请稍后重试。'})
        try:
            if request['source'] == 'patent':
                pid = normalize_id(request.get('publication', ''))
                if pid not in self.server.results:
                    return self.reply(400, {'error': '请先检索该专利；只写入服务端已读取的结果。'})
                items, refused = from_patent(self.server.results[pid])
                present, note = [], {'source': 'patent', 'publication': pid,
                                     'sha256': self.server.results[pid].get('source_snapshot', {}).get('sha256')}
            elif request['source'] == 'surechembl':
                from . import surechembl
                n = surechembl.schembl_id(request.get('schembl'))
                pid = normalize_id(request.get('publication', ''))
                cache = self.server.cache.parent / 'discovery-cache' / 'surechembl'
                try:
                    record, src = surechembl.compound(n, cache)
                    if record is None:
                        return self.reply(400, {'error': f'SureChEMBL 中没有 SCHEMBL{n}；未写入。'})
                    payload, doc_src = surechembl.documents(n, cache)
                except Exception:
                    return self.reply(502, {'error': 'SureChEMBL 暂不可用，未写入任何记录。'})
                rows = [surechembl.document_row(d) for d in payload['documents']]
                patent = next((r for r in rows if r['publication'] == pid), None)
                if patent is None:
                    return self.reply(400, {'error': '服务端读取的 SureChEMBL 结果中没有该专利与该化合物的关联；未写入。'})
                items, refused = from_surechembl(f'SCHEMBL{n}', record, pid, patent)
                present = []
                note = {'source': 'surechembl', 'schembl': f'SCHEMBL{n}', 'publication': pid,
                        'sha256': [src['sha256'], doc_src['sha256']]}
            else:
                wanted = request.get('activity_ids')
                if not isinstance(wanted, list) or not 1 <= len(wanted) <= 20:
                    raise ValueError('请选择 1–20 条测量。')
                wanted = {str(x) for x in wanted}
                query = {k: request.get(k) for k in ('entity', 'id', 'offset')}
                cache = self.server.cache.parent / 'discovery-cache'
                page = discover({**query, 'mode': 'activities'}, cache)
                chosen = [a for a in page['activities'] if str(a.get('activity_id')) in wanted]
                missing = wanted - {str(a.get('activity_id')) for a in chosen}
                docs = discover_documents(query, cache)['documents'] if chosen else []
                items, refused, present = from_activities(
                    chosen, {d['id']: d['document'] for d in docs if d.get('document')}, store.load())
                refused += [{'id': x, 'reason': '不在服务端读取的该页结果中'} for x in sorted(missing)]
                note = {'source': 'chembl_activities', **query, 'sources': [x['sha256'] for x in page['sources']]}
            result = store.propose_batch(items, 'workbench', note=json.dumps(note, ensure_ascii=False))
            added, present_counts = {}, {}
            for kind, _ in result['added']:
                added[kind] = added.get(kind, 0) + 1
            for kind, _ in result['already_present']:
                present_counts[kind] = present_counts.get(kind, 0) + 1
            if present:
                present_counts['observations'] = present_counts.get('observations', 0) + len(present)
            notice = '状态均为待确认；确认或拒绝须经人工复核。'
            if request['source'] == 'patent':
                notice += '结构索引条目未映射实施例、尚无测量，不会出现在分析视图中。'
            elif request['source'] == 'surechembl':
                notice += '该结构由 SureChEMBL 自动提取，未映射实施例、没有测量；需回到专利原文核实后才能作为证据。'
            else:
                notice += '测量已进入证据台账页（带待复核缺口），可比性仍按原规则判断。'
            self.reply(200, {
                'added': added,
                'already_present': sorted(set(present) | {i for k, i in result['already_present'] if k == 'observations'}),
                'already_present_counts': present_counts,
                'refused': refused,
                'notice': notice})
        except ValueError as exc:
            self.reply(400, {'error': str(exc) or '请求内容无效。'})
        except PermissionError as exc:
            self.reply(403, {'error': str(exc)})
        except Exception:
            self.reply(502, {'error': '写入台账失败，未写入部分记录。'})
        finally:
            self.server.work_lock.release()


# ---------------------------------------------------------------- redesigned workbench (studies)

def _studies_folder(handler):
    return handler.server.cache.parent


def _ledger():
    from phase0.ledger.access import current_ledger
    return current_ledger()


def _body(handler, limit):
    size = int(handler.headers.get('Content-Length', '0') or 0)
    if not 0 < size <= limit:
        raise ValueError('请求大小无效。')
    data = json.loads(handler.rfile.read(size))
    if not isinstance(data, dict):
        raise ValueError('请求格式无效。')
    return data


def _guard(fn):
    """Uniform errors: input problems are 400, unknown ids 404, anything else 422 without partial results."""
    def run(handler, *args):
        try:
            return fn(handler, *args)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return handler.reply(400, {'error': str(exc) or '请求内容无效。'})
        except KeyError:
            return handler.reply(422, {'error': '处理未完成，未返回部分结果。'})
        except LookupError as exc:
            return handler.reply(404, {'error': str(exc.args[0]) if exc.args else '未找到。'})
        except PermissionError as exc:
            return handler.reply(403, {'error': str(exc)})
        except Exception:
            return handler.reply(422, {'error': '处理未完成，未返回部分结果。'})
    return run


def _one(query, key, limit=200):
    value = (query.get(key) or [''])[0]
    if not value or len(value) > limit:
        raise ValueError(f'缺少或过长的参数 {key}。')
    return value


@_guard
def get_studies(handler, query):
    ledger = _ledger()
    rows = [studies.summary(ledger, s) for s in studies.load_all(_studies_folder(handler))]
    pending = sum(1 for c in ledger.compounds if c.review.record_status == 'proposed')
    return handler.reply(200, {'studies': rows, 'examples': studies.examples(ledger), 'review_pending': pending,
                               'ledger_writable': handler.ledger_store() is not None,
                               'documents': [d.id for d in ledger.documents]})


@_guard
def get_study(handler, query):
    study = studies.get(_studies_folder(handler), _one(query, 'id', 60))
    view = (query.get('view') or ['overview'])[0]
    views = {'overview': studies.overview, 'evidence': studies.evidence, 'timeline': studies.timeline,
             'report': studies.report}
    if view not in views:
        raise ValueError('view 必须是 overview、evidence、timeline 或 report。')
    out = views[view](_ledger(), study)
    out['ledger_writable'] = handler.ledger_store() is not None
    return handler.reply(200, out)


@_guard
def get_documents(handler, query):
    return handler.reply(200, {'documents': studies.documents(_ledger(), studies.load_all(_studies_folder(handler)))})


@_guard
def get_depict(handler, query):
    smiles = _one(query, 'smiles', 2000)
    size = (query.get('size') or ['m'])[0]
    w, h = {'s': (160, 110), 'm': (240, 160), 'l': (360, 240)}.get(size, (240, 160))
    svg = studies.depict(smiles, w, h)
    if svg is None:
        raise ValueError('SMILES 无法解析。')
    raw = svg.encode('utf-8')
    handler.send_response(200)
    handler.send_header('Content-Type', 'image/svg+xml')
    handler.send_header('Content-Length', str(len(raw)))
    handler.send_header('Cache-Control', 'private, max-age=86400')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'")
    handler.end_headers()
    handler.wfile.write(raw)


@_guard
def get_smiles_info(handler, query):
    return handler.reply(200, studies.smiles_info(_one(query, 'smiles', 2000)))


@_guard
def get_review_queue(handler, query):
    store = handler.ledger_store()
    ledger = store.load() if store is not None else _ledger()
    out = studies.review_queue(ledger, store)
    out['writable'] = store is not None
    if store is None:
        out['notice'] = '未启用台账数据库：可以查看待确认记录，但不能保存复核结论。请用 --ledger-db 启动服务。'
    return handler.reply(200, out)


@_guard
def post_studies(handler):
    return handler.reply(200, studies.create(_studies_folder(handler), _body(handler, 1024).get('name')))


@_guard
def post_study_documents(handler):
    req = _body(handler, 4096)
    known = {d.id for d in _ledger().documents}
    return handler.reply(200, studies.add_documents(_studies_folder(handler), req.get('id'), req.get('documents'), known))


@_guard
def post_study_note(handler):
    req = _body(handler, 4096)
    return handler.reply(200, studies.add_note(_studies_folder(handler), req.get('id'), req))


@_guard
def post_study_compare(handler):
    req = _body(handler, 1024)
    if not all(isinstance(req.get(k), str) for k in ('a', 'b')):
        raise ValueError('请选择 A、B 两个分子。')
    return handler.reply(200, studies.compare(_ledger(), req['a'], req['b']))


@_guard
def post_study_report(handler):
    req = _body(handler, 32000)
    study = studies.get(_studies_folder(handler), req.get('id'))
    ledger = _ledger()
    rep = studies.report(ledger, study)
    analysis = None
    if isinstance(req.get('analysis'), dict):
        from .project_sar import handle
        analysis = handle({**req['analysis'], 'mode': 'report'})['markdown']
    options = req.get('options') if isinstance(req.get('options'), dict) else {}
    options = {k: options.get(k) is True for k in ('include_l1', 'include_hashes')}
    return handler.reply(200, {'report': rep, 'markdown': studies.report_markdown(rep, analysis, options)})


@_guard
def post_study_analysis(handler):
    req = _body(handler, 32000)
    folder = _studies_folder(handler)
    study = studies.get(folder, req.get('id'))
    setup = req.get('analysis')
    if not isinstance(setup, dict):
        raise ValueError('缺少分析设置。')
    from .project_sar import handle
    if setup.get('documents') and any(d not in study['documents'] for d in setup['documents']):
        raise ValueError('分析范围超出了本调研的文档。')
    handle({**setup, 'mode': 'analyse'})  # only a set-up that runs is kept
    return handler.reply(200, studies.save_analysis(folder, study['id'], setup))


@_guard
def post_molfile(handler):
    return handler.reply(200, studies.molfile_smiles(_body(handler, 210000).get('text')))


@_guard
def post_review(handler):
    req = _body(handler, 4096)
    store = handler.ledger_store()
    if store is None:
        return handler.reply(409, {'error': '未启用台账数据库，复核结论未保存。请用 --ledger-db 启动服务。'})
    if not handler.server.work_lock.acquire(blocking=False):
        return handler.reply(429, {'error': '正在处理另一请求，请稍后重试。'})
    try:
        return handler.reply(200, studies.review_compound(store, req.get('id'), req.get('status'),
                                                         req.get('reviewer'), req.get('note')))
    finally:
        handler.server.work_lock.release()


STUDY_GET = {'/api/studies': get_studies, '/api/study': get_study, '/api/depict': get_depict,
             '/api/smiles-info': get_smiles_info, '/api/documents': get_documents, '/api/review/queue': get_review_queue}
STUDY_POST = {'/api/studies': post_studies, '/api/study/documents': post_study_documents,
              '/api/study/note': post_study_note, '/api/study/compare': post_study_compare,
              '/api/study/report': post_study_report, '/api/study/analysis': post_study_analysis,
              '/api/molfile': post_molfile, '/api/review': post_review}


PUBLIC_HOST = re.compile(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+')


def public_host(value):
    value = value.strip().lower()
    if not PUBLIC_HOST.fullmatch(value):
        raise argparse.ArgumentTypeError(f'不是有效的主机名：{value!r}（不接受通配符、端口或网址）')
    return value


def create_server(port=8766, cache=None, ledger_db=None, public_hosts=()):
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.public_hosts = frozenset(public_hosts)
    server.cache = cache or ROOT/'artifacts/patent-cache'
    server.ledger_db = ledger_db
    server.work_lock = threading.Lock()
    server.results = {}
    return server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--ledger-db',help='启用 SQLite 台账（空库时从已提交数据初始化）；分析也改为读取该库')
    parser.add_argument('--public-host',action='append',default=[],type=public_host,
                        help='另外接受的转发主机名（如 GitHub Codespaces 的端口地址），经 https 访问；可重复。服务仍只监听 127.0.0.1')
    args=parser.parse_args()
    if args.ledger_db:
        store = LedgerStore(args.ledger_db)
        if store.is_empty():
            from phase0.ledger.migrate import build
            store.import_ledger(build(), 'serve --ledger-db')
            print(f'已从已提交数据初始化台账：{args.ledger_db}', flush=True)
        os.environ[LEDGER_ENV] = args.ledger_db
    server=create_server(args.port, ledger_db=args.ledger_db, public_hosts=args.public_host)
    print(f'Open http://127.0.0.1:{server.server_port}',flush=True)
    for host in args.public_host:
        print(f'Also accepting https://{host}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
