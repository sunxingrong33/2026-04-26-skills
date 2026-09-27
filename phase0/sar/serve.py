"""Loopback-only interactive patent explorer: python -m phase0.sar.serve."""
from __future__ import annotations
import argparse
import json
import threading
from rdkit import Chem
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from .patents import retrieve, normalize_id
from .report import ROOT, structure_pair
from .features import FEATURE_LABELS
from .lineage import build_lineage
from .discovery import discover
from .programs import analyse_programs
from .scaffolds import align_evidence
from .evidence_ledger import build_ledger
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
        origin = self.headers.get('Origin')
        return host in valid and (not origin or origin in {'http://' + v for v in valid})

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error':'仅接受本机同源请求。'})
        url = urlsplit(self.path)
        if url.path in ('/', '/index.html'):
            return self.reply(200, (ROOT/'phase0/web/patents.html').read_bytes(), 'text/html; charset=utf-8')
        if url.path == '/programs.js':
            return self.reply(200, (ROOT/'phase0/web/programs.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/evidence':
            return self.reply(200, (ROOT/'phase0/web/evidence.html').read_bytes(), 'text/html; charset=utf-8')
        if url.path == '/evidence-workflow.js':
            return self.reply(200, (ROOT/'phase0/web/evidence-workflow.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/sar-workflow.js':
            return self.reply(200, (ROOT/'phase0/web/sar-workflow.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/api/evidence':
            try:
                return self.reply(200, build_ledger())
            except (ValueError, KeyError, OSError):
                return self.reply(422, {'error': '证据包读取或校验失败，未返回部分台账。'})
        if url.path == '/discovery.js':
            return self.reply(200, (ROOT/'phase0/web/discovery.js').read_bytes(), 'text/javascript; charset=utf-8')
        if url.path == '/examples':
            path = ROOT/'artifacts/sar-explorer.html'
            return self.reply(200, path.read_bytes(), 'text/html; charset=utf-8') if path.exists() else self.reply(404, {'error':'请先运行 python -m phase0.sar.demo'})
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



def create_server(port=8766, cache=None):
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.cache = cache or ROOT/'artifacts/patent-cache'
    server.work_lock = threading.Lock()
    server.results = {}
    return server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    server=create_server(args.port)
    print(f'Open http://127.0.0.1:{server.server_port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
