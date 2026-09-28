"""Agent tool layer: validated calls, propose-only ledger writes, traced runs, faithful replay."""
import io
import json

import pytest

from phase0.ledger.migrate import build
from phase0.ledger.store import LedgerStore
from phase0.tools import chem_tools, ledger_tools, source_tools  # noqa: F401
from phase0.tools.core import REGISTRY, Run, ToolFailure, read_trace, replay, tools_in

OBS_6 = 'WO2011138751A2:example:6'


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'ledger.sqlite'
    LedgerStore(path).import_ledger(build(), 'test')
    return path


@pytest.fixture
def run(db, tmp_path):
    return Run(ledger_db=db, runs_dir=tmp_path / 'runs', cache_dir=tmp_path / 'cache')


def proposal(**patch):
    record = {'id': 'agent:obs:1', 'document_id': 'WO2011138751A2', 'compound_id': OBS_6,
              'assay_id': 'WO2011138751A2:ALK_WT_Ki', 'status': 'measured', 'relation': '=', 'value': 0.62,
              'unit': 'nM', 'source': {'pdf_page': 186}, 'raw': {},
              'review': {'record_status': 'proposed', 'provenance_status': 'agent_extraction'}}
    record.update(patch)
    return record


def test_ledger_tools_cannot_confirm_reject_or_import():
    names = {s.name for s in tools_in('ledger')}
    assert not any(w in n for n in names for w in ('confirm', 'reject', 'review', 'import', 'delete'))
    writers = {s.name for s in REGISTRY.values() if not s.read_only}
    assert writers == {'ledger_propose', 'ledger_propose_chembl_activities', 'ledger_propose_patent_index'}


def test_every_tool_says_when_to_call_it():
    for spec in REGISTRY.values():
        assert len(spec.description) > 40 and '调用' in spec.description, spec.name


def test_arguments_are_validated_before_the_tool_runs(run):
    with pytest.raises(ToolFailure, match='参数无效'):
        run.call('ledger_search_observations', {'compund_id': OBS_6})
    with pytest.raises(ToolFailure, match='参数无效'):
        run.call('ledger_search_observations', {'limit': 1000})
    with pytest.raises(ToolFailure, match='未知工具'):
        run.call('ledger_review', {})


def test_search_reports_missing_values_as_status(run):
    rows = run.call('ledger_search_observations', {'compound_id': OBS_6})['data']['rows']
    blanks = [r for r in rows if r['status'] != 'measured']
    assert [r['status'] for r in blanks] == ['not_tested', 'not_tested']
    assert all(r['value'] is None and r['relation'] is None for r in blanks)


def test_propose_is_attributed_to_the_run_and_refuses_confirmation(run, db):
    out = run.call('ledger_propose', {'kind': 'observations', 'record': proposal(), 'note': 'PDF p.186 下表'})
    assert out['data']['added'] == ['agent:obs:1'] and out['data']['actor'] == run.actor
    entry = LedgerStore(db).audit_log('agent:obs:1')[0]
    assert entry['actor'] == run.actor and entry['note'] == 'PDF p.186 下表'
    confirmed = proposal(id='x', review={'record_status': 'confirmed', 'provenance_status': 'a', 'reviewer': 'agent'})
    with pytest.raises(ToolFailure, match='只能是 proposed'):
        run.call('ledger_propose', {'kind': 'observations', 'record': confirmed, 'note': 'try to confirm'})
    with pytest.raises(ToolFailure, match='不存在的化合物'):
        run.call('ledger_propose', {'kind': 'observations', 'record': proposal(id='y', compound_id='nope'),
                                    'note': 'dangling reference'})
    with pytest.raises(ToolFailure, match='不能带数值'):
        run.call('ledger_propose', {'kind': 'observations', 'note': 'number for untested cell',
                                    'record': proposal(id='z', status='not_tested')})
    again = run.call('ledger_propose', {'kind': 'observations', 'record': proposal(value=99.0), 'note': 'overwrite?'})
    assert again['data']['already_present'] == ['agent:obs:1']
    stored = next(o for o in LedgerStore(db).load().observations if o.id == 'agent:obs:1')
    assert stored.value == 0.62


def test_mass_check_keeps_reported_precision(run):
    smiles = 'CC(Oc1cc(-c2c(C)[nH]nc2C)cnc1N)c1cc(F)ccc1OC'
    assert run.call('chem_mass_check', {'smiles': smiles, 'reported': 357})['data']['resolution'] == 'nominal'
    assert run.call('chem_mass_check', {'smiles': smiles, 'reported': '357'})['data']['resolution'] == 'nominal'
    assert run.call('chem_mass_check', {'smiles': smiles, 'reported': '370.22'})['data']['resolution'] == 'low'


def test_compare_uses_the_runs_ledger(run, db):
    # Select compound A through another of its observations, so rejecting the WT Ki row leaves A selectable.
    args = {'a_observation_id': 'WO2011138751A2:example:1:1', 'b_observation_id': 'WO2011138751A2:example:7:0'}
    wt = lambda: next(m for m in run.call('chem_compare_observations', args)['data']['measurements']
                      if m['assay_id'] == 'ALK_WT_Ki')
    assert wt()['ratio_b_over_a'] == pytest.approx(0.536 / 2.9)
    LedgerStore(db).review('observations', 'WO2011138751A2:example:1:0', 'rejected', 'chemist-a', 'test')
    assert wt()['ratio_b_over_a'] is None


def test_every_call_is_traced_including_failures(run):
    run.call('chem_describe', {'smiles': 'CCO'})
    with pytest.raises(ToolFailure):
        run.call('chem_describe', {'smiles': 'C1CC'})
    trace = read_trace(run.dir)
    assert [(e['tool'], e['ok']) for e in trace] == [('chem_describe', True), ('chem_describe', False)]
    assert all(len(e['result_sha256']) == 64 for e in trace)
    assert (run.dir / 'ledger.start.sqlite').exists()


def test_replay_reproduces_reads_and_writes_from_the_snapshot(run, db):
    run.call('ledger_overview', {})
    run.call('ledger_propose', {'kind': 'observations', 'record': proposal(), 'note': 'PDF p.186 下表'})
    run.call('ledger_search_observations', {'compound_id': OBS_6})
    with pytest.raises(ToolFailure):
        run.call('ledger_get_record', {'kind': 'compounds', 'record_id': 'nope'})
    # The live store keeps changing after the run; replay must not depend on it.
    LedgerStore(db).review('observations', 'agent:obs:1', 'rejected', 'chemist-a', 'later decision')
    report = replay(run.dir)
    assert report == {'run_id': run.run_id, 'calls': 4, 'matched': 4, 'mismatched': [],
                      'network_blocked': True, 'faithful': True}


def test_replay_detects_a_tampered_trace(run):
    run.call('chem_describe', {'smiles': 'CCO'})
    run.call('chem_describe', {'smiles': 'CCN'})
    path = run.dir / 'trace.jsonl'
    lines = path.read_text(encoding='utf-8').splitlines()
    entry = json.loads(lines[1])
    entry['arguments']['smiles'] = 'CCC'
    path.write_text(lines[0] + '\n' + json.dumps(entry, ensure_ascii=False) + '\n', encoding='utf-8')
    report = replay(run.dir)
    assert report['faithful'] is False and report['mismatched'] == [{'seq': 2, 'tool': 'chem_describe'}]


def test_source_tools_replay_from_cache_with_network_blocked(run, monkeypatch):
    page = {'activities': [{'activity_id': 1, 'molecule_chembl_id': 'CHEMBL1', 'standard_value': '5'}],
            'page_meta': {'total_count': 1}}
    monkeypatch.setattr('phase0.sar.discovery.urlopen', lambda *a, **k: io.BytesIO(json.dumps(page).encode()))
    out = run.call('chembl_activities', {'entity': 'target', 'chembl_id': 'CHEMBL4247'})
    assert out['data']['rows'][0]['activity_id'] == 1
    monkeypatch.undo()
    assert replay(run.dir)['faithful'] is True
    for f in (run.cache_dir / 'discovery-cache').glob('*.json'):
        f.unlink()
    report = replay(run.dir)
    assert report['faithful'] is False and report['network_blocked'] is True


def test_ledger_tools_need_a_configured_store(tmp_path):
    bare = Run(runs_dir=tmp_path / 'runs')
    with pytest.raises(ToolFailure, match='未配置台账数据库'):
        bare.call('ledger_overview', {})
    assert bare.call('chem_describe', {'smiles': 'CCO'})['data']['formula'] == 'C2H6O'
