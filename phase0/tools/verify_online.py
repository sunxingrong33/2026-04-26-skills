"""Online path check, to run once on a machine that can reach the sources.

    python -m phase0.tools.verify_online [--out DIR]

Uses a fresh cache and a fresh ledger built from committed evidence, then goes
through the path a user or agent would: read two patents from Google Patents,
look up the target in ChEMBL, read one page of its measurements, add that page
and one patent index to the ledger (proposed only), run a similarity and a
substructure search in ChEMBL, a SureChEMBL similarity search and the patents
of its top hit, and replay the whole run with the network
blocked. Writes ``report.md`` and ``report.json`` to the output
directory and exits non-zero when any check fails.

A warning is not a failure: e.g. a patent page whose hash no longer matches the
committed evidence package pauses the evidence cards by design, and the report
says that the mapping needs re-checking.
"""
import argparse
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from phase0.ledger.migrate import build
from phase0.ledger.store import LedgerStore
from phase0.sar.patent_evidence import DATA as EVIDENCE
from . import chem_tools, ledger_tools, source_tools  # noqa: F401  (register tools)
from .core import ROOT, Run, ToolFailure, replay

PUBLICATIONS = ('WO2011138751A2', 'WO2013132376A1')
TARGET, TARGET_ID = 'ALK', 'CHEMBL4247'
LORLATINIB = 'C[C@H]1Oc2cc(cnc2N)-c2c(nn(C)c2C#N)CN(C)C(=O)c2ccc(F)cc21'  # WO2013132376A1 Example 2
FRAGMENT = 'Nc1ncccc1OCc1ccccc1'  # aminopyridine benzyl ether shared by both ALK families


class Checks:
    def __init__(self):
        self.items = []
        self.seconds = None

    def add(self, name, status, detail, **data):
        self.items.append({'check': name, 'status': status, 'detail': detail, 'seconds': self.seconds, **data})
        self.seconds = None
        return status != 'fail'

    def step(self, name, fn):
        """Run fn, timing it; an exception becomes a failed check instead of stopping the report."""
        start = time.monotonic()
        try:
            return fn()
        except (ToolFailure, ValueError, RuntimeError, OSError) as exc:
            self.seconds = round(time.monotonic() - start, 2)
            self.add(name, 'fail', f'{type(exc).__name__}: {exc}')
        finally:
            if self.seconds is None:
                self.seconds = round(time.monotonic() - start, 2)


def check_patent(checks, run, pid):
    r = checks.step(f'专利 {pid}', lambda: run.call('patent_fetch', {'publication': pid}))
    if r is None:
        return
    d = r['data']
    packaged = (EVIDENCE / f'{pid}.json').exists()
    detail = (f"家族 {d['family_id']}，优先权日 {d['priority_date']}，索引结构 {len(d['structure_index'])} 个，"
              f"证据卡 {len(d['evidence_cards'])} 张；页面 SHA-256 {d['source_sha256'][:12]}…")
    status = 'pass' if d['structure_index'] else 'fail'
    if packaged and not d['evidence_cards']:
        status = 'warn' if status == 'pass' else status
        detail += f"。{d['evidence_status']}"
    elif d['evidence_cards']:
        bad = [c['example'] for c in d['evidence_cards'] if c['mass_check'] != 'consistent']
        detail += '；质谱校验全部一致' if not bad else f'；质谱校验不一致：Example {bad}'
        status = 'warn' if bad and status == 'pass' else status
    checks.add(f'专利 {pid}', status, detail, source_sha256=d['source_sha256'],
               evidence_cards=len(d['evidence_cards']))


def check_target(checks, cache):
    from phase0.sar.discovery import discover
    r = checks.step('靶点检索', lambda: discover({'mode': 'target', 'query': TARGET}, cache / 'discovery-cache'))
    if r is None:
        return
    ids = [t.get('target_chembl_id') for t in r['targets']]
    ok = TARGET_ID in ids
    checks.add('靶点检索', 'pass' if ok else 'fail',
               f'“{TARGET}” 返回 {len(ids)} 个候选，' + (f'含 {TARGET_ID}' if ok else f'不含 {TARGET_ID}'))


def check_activities(checks, run):
    r = checks.step('靶点测量', lambda: run.call('chembl_activities',
                                                 {'entity': 'target', 'chembl_id': TARGET_ID, 'offset': 0}))
    if r is None:
        return []
    rows = r['data']['rows']
    relations = sorted({str(x['standard_relation']) for x in rows})
    blank = sum(x['standard_value'] is None for x in rows)
    checks.add('靶点测量', 'pass' if rows else 'fail',
               f"共 {r['data']['total']} 条，本页 {len(rows)} 条；限定符 {relations}，无数值 {blank} 条（原样保留）")
    return [x['activity_id'] for x in rows]


def check_structure(checks, run):
    for name, args in (('结构检索（相似性）', {'smiles': LORLATINIB, 'method': 'similarity', 'threshold': 70}),
                       ('结构检索（子结构）', {'smiles': FRAGMENT, 'method': 'substructure'})):
        r = checks.step(name, lambda: run.call('structure_search', args))
        if r is None:
            continue
        chembl = r['data']['chembl']
        rows = chembl['rows']
        disagree = [x['molecule_chembl_id'] for x in rows if x['local_check']['status'] != 'agrees']
        detail = (f"{r['summary']} 本地复核不一致 {len(disagree)} 个" + (f"：{disagree[:5]}" if disagree else '')
                  + ('；结果已截断' if chembl['truncated'] else ''))
        if args['method'] == 'similarity':
            same = any(x['local_check'].get('similarity') == 1.0 for x in rows)
            detail += '；' + ('命中查询分子本身' if same else '未命中查询分子本身')
        status = 'fail' if not rows else 'warn' if disagree else 'pass'
        checks.add(name, status, detail)


def check_surechembl(checks, run, publications):
    name = '结构检索（SureChEMBL）'
    r = checks.step(name, lambda: run.call('structure_search', {'smiles': LORLATINIB, 'method': 'similarity',
                                                                 'threshold': 70, 'external': False,
                                                                 'surechembl': True}))
    if r is None:
        return
    rows = r['data']['surechembl']['rows']
    same = next((x for x in rows if x['local_check'].get('similarity') == 1.0), None)
    disagree = [x['schembl_id'] for x in rows if x['local_check']['status'] != 'agrees']
    checks.add(name, 'fail' if not rows else 'pass' if same and not disagree else 'warn',
               f"{r['summary']} 本地复核不一致 {len(disagree)} 个；" +
               (f"查询分子本身为 {same['schembl_id']}" if same else '未命中查询分子本身'))
    if same is None:
        return
    name = 'SureChEMBL 专利关联'
    p = checks.step(name, lambda: run.call('surechembl_patents', {'compound': same['schembl_id']}))
    if p is None:
        return
    found = sorted({x['publication'] for x in p['data']['patents']} & set(publications))
    checks.add(name, 'pass' if found else 'warn',
               p['summary'] + (f" 前 {len(p['data']['patents'])} 份中含已整理专利 {found}" if found else
                               ' 前 20 份中不含已整理的两份专利（可能在后续页，或未被提取）'))


def _record_ids(db):
    led = LedgerStore(db).load()
    return {(kind, r.id): r.review.record_status
            for kind in ('documents', 'compounds', 'assays', 'observations') for r in getattr(led, kind)}


def check_intake(checks, run, db, activity_ids, pid):
    before = _record_ids(db)
    args = {'entity': 'target', 'chembl_id': TARGET_ID, 'activity_ids': activity_ids, 'note': '在线路径验证'}
    r = checks.step('测量加入台账', lambda: run.call('ledger_propose_chembl_activities', args))
    if r is not None:
        d = r['data']
        counted = d['added'].get('observations', 0) + len(d['refused']) + len(d['already_present'])
        reasons = sorted({x['reason'] for x in d['refused']})
        checks.add('测量加入台账', 'pass' if counted == len(activity_ids) else 'fail',
                   f"{r['summary']} 拒绝原因：{reasons or '无'}", refused=d['refused'])
        again = checks.step('重复加入', lambda: run.call('ledger_propose_chembl_activities', args))
        if again is not None:
            checks.add('重复加入', 'pass' if not again['data']['added'] else 'fail', again['summary'])
    r = checks.step('专利索引加入台账', lambda: run.call('ledger_propose_patent_index', {'publication': pid}))
    if r is not None:
        checks.add('专利索引加入台账', 'pass', r['summary'])
    new = {k: v for k, v in _record_ids(db).items() if k not in before}
    statuses = sorted(set(new.values()))
    checks.add('新增记录状态', 'pass' if statuses in ([], ['proposed']) else 'fail',
               f'新增 {len(new)} 条记录，状态 {statuses or "—"}（只能是 proposed）')


def render(report):
    icon = {'pass': '通过', 'warn': '注意', 'fail': '失败'}
    lines = [f"# 在线路径核对报告", '',
             f"- 时间：{report['finished_at']}",
             f"- 环境：Python {report['python']} · {report['platform']}",
             f"- 结论：**{report['verdict']}**（通过 {report['counts']['pass']}，注意 {report['counts']['warn']}，"
             f"失败 {report['counts']['fail']}）",
             f"- 运行记录：`{report['run_dir']}`", '',
             '| 检查 | 结果 | 说明 | 用时 (s) |', '|---|---|---|---:|']
    for c in report['checks']:
        detail = c['detail'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {c['check']} | {icon[c['status']]} | {detail} | {'' if c['seconds'] is None else c['seconds']} |")
    lines += ['', '结果补入 `docs/baseline.md` 的“在线路径”一节。']
    return '\n'.join(lines) + '\n'


def verify(out, publications=PUBLICATIONS):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    db = out / 'ledger.sqlite'
    LedgerStore(db).import_ledger(build(), 'online-check')
    cache = out / 'cache'  # fresh: every source is really fetched
    run = Run(ledger_db=db, runs_dir=out / 'runs', cache_dir=cache)
    checks = Checks()
    for pid in publications:
        check_patent(checks, run, pid)
    check_target(checks, cache)
    activity_ids = check_activities(checks, run)
    if activity_ids:
        check_intake(checks, run, db, activity_ids, publications[-1])
    check_structure(checks, run)
    check_surechembl(checks, run, publications)
    earlier_failed = any(c['status'] == 'fail' for c in checks.items)
    result = checks.step('断网重放', lambda: replay(run.dir))
    if result is not None:
        detail = f"{result['matched']}/{result['calls']} 一致" + (
            f"；不一致：{result['mismatched']}" if result['mismatched'] else '')
        if earlier_failed and result['faithful']:
            detail += '；前序步骤有失败，重放只说明失败可复现'
        checks.add('断网重放', 'fail' if not result['faithful'] else 'warn' if earlier_failed else 'pass', detail)
    counts = {k: sum(c['status'] == k for c in checks.items) for k in ('pass', 'warn', 'fail')}
    report = {'finished_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'python': platform.python_version(), 'platform': platform.platform(terse=True),
              'run_dir': str(run.dir), 'counts': counts, 'checks': checks.items,
              'verdict': '失败' if counts['fail'] else '通过（有注意项）' if counts['warn'] else '通过'}
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'report.md').write_text(render(report), encoding='utf-8')
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    ap.add_argument('--out', default=str(ROOT / 'artifacts' / 'online-check' / stamp))
    args = ap.parse_args(argv)
    report = verify(args.out)
    print((Path(args.out) / 'report.md').read_text(encoding='utf-8'))
    return 1 if report['counts']['fail'] else 0


if __name__ == '__main__':
    sys.exit(main())
