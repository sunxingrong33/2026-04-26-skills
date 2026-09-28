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
from phase0.ledger.intake import from_activities, from_patent
from phase0.ledger.store import KINDS, LedgerStore
from .evidence_pair import analyse_pair
from .sar_workflow import run_workflow
from .patent_evidence import compare_measurements, provisional_direction

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
        if url.path in ('/', '/index.html'):
            return self.reply(200, (ROOT/'phase0/web/patents.html').read_bytes(), 'text/html; charset=utf-8')
        if url.path == '/programs.js':
            return self.reply(200, (ROOT/'phase0/web/programs.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/project':
            return self.reply(200, (ROOT/'phase0/web/project.html').read_bytes(), 'text/html; charset=utf-8')
        if url.path == '/project.js':
            return self.reply(200, (ROOT/'phase0/web/project.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/evidence':
            return self.reply(200, (ROOT/'phase0/web/evidence.html').read_bytes(), 'text/html; charset=utf-8')
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
            if not isinstance(request, dict) or request.get('source') not in ('patent', 'activities'):
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
