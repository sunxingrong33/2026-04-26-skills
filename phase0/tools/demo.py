"""Offline walkthrough of the agent tool layer: a scripted session, then its replay.

    python -m phase0.tools.demo [--keep]

Builds a temporary ledger from committed evidence, calls the tools the way an agent
would (including a refused attempt to confirm a record), and replays the recorded run
from its starting snapshot. No network, no model, no API key.
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from phase0.ledger.migrate import build
from phase0.ledger.store import LedgerStore
from . import chem_tools, ledger_tools, source_tools  # noqa: F401  (register tools)
from .core import Run, ToolFailure, replay

EX1, EX7 = 'WO2011138751A2:example:1', 'WO2011138751A2:example:7'
PROPOSAL = {'id': 'demo:obs:1', 'document_id': 'WO2011138751A2', 'compound_id': EX7,
            'assay_id': 'WO2011138751A2:ALK_WT_Ki', 'status': 'measured', 'relation': '=', 'value': 0.536,
            'unit': 'nM', 'source': {'pdf_page': 186, 'locator': 'Ki / ELISA 表，Example 7 行'}, 'raw': {},
            'review': {'record_status': 'proposed', 'provenance_status': 'demo_agent'}}
STEPS = [
    ('ledger_overview', {}),
    ('ledger_search_observations', {'compound_id': 'WO2011138751A2:example:6'}),
    ('chem_mass_check', {'smiles': 'CC(Oc1cc(-c2c(C)n(C)nc2C)cnc1N)c1cc(F)ccc1OC', 'reported': '371'}),
    ('chem_compare_observations', {'a_observation_id': f'{EX1}:0', 'b_observation_id': f'{EX7}:0'}),
    ('ledger_propose', {'kind': 'observations', 'record': PROPOSAL, 'note': 'PDF p.186 Ki 表 Example 7 行'}),
    ('ledger_propose', {'kind': 'observations', 'note': '试图直接确认',
                        'record': {**PROPOSAL, 'id': 'demo:obs:2',
                                   'review': {'record_status': 'confirmed', 'provenance_status': 'demo_agent',
                                              'reviewer': 'agent'}}}),
    ('ledger_search_observations', {'compund_id': EX7}),
]


def run_demo(workdir, out=print):
    db = Path(workdir) / 'ledger.sqlite'
    LedgerStore(db).import_ledger(build(), 'demo')
    run = Run(ledger_db=db, runs_dir=Path(workdir) / 'runs', cache_dir=Path(workdir) / 'cache')
    out(f'运行 {run.run_id}（台账快照与轨迹：{run.dir}）\n')
    results = {}
    for i, (name, args) in enumerate(STEPS, 1):
        try:
            results[i] = run.call(name, args)
            out(f'{i}. {name} → {results[i]["summary"]}')
        except ToolFailure as exc:
            out(f'{i}. {name} → 拒绝：{exc}')

    def wt(result):
        return next(m for m in result['data']['measurements'] if m['assay_id'] == 'ALK_WT_Ki')
    before = wt(results[4])
    out(f"\n   第 4 步 WT Ki：A {before['a'][0]['value']} nM → B {before['b'][0]['value']} nM，"
        f"暂定 B/A = {before['ratio_b_over_a']:.3f}（同一专利、同一实验；不是改善倍数）")
    audit = LedgerStore(db).audit_log('demo:obs:1')[0]
    out(f"   第 5 步提交：状态 proposed，写入者 {audit['actor']}，依据「{audit['note']}」")
    after = wt(run.call('chem_compare_observations', dict(STEPS[3][1])))
    out(f"   再次对照：B 现有 {len(after['b'])} 条 WT Ki，比值 = {after['ratio_b_over_a']}；原因：{after['reasons'][0]}")
    report = replay(run.dir)
    out(f"\n重放：{report['matched']}/{report['calls']} 次调用结果一致（联网已禁用），faithful = {report['faithful']}")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description='agent 工具层离线演示')
    ap.add_argument('--keep', action='store_true', help='保留临时目录（台账、轨迹）供查看')
    args = ap.parse_args(argv)
    workdir = tempfile.mkdtemp(prefix='sar-tools-demo-')
    try:
        report = run_demo(workdir)
    finally:
        if args.keep:
            print(f'\n已保留：{workdir}')
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0 if report['faithful'] else 1


if __name__ == '__main__':
    sys.exit(main())
