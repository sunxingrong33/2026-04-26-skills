"""MCP adapter, exercised in-process with the official client (no model, no API key)."""
import asyncio
import json

import pytest

pytest.importorskip('mcp')
from mcp import Client  # noqa: E402

from phase0.ledger.migrate import build  # noqa: E402
from phase0.ledger.store import LedgerStore  # noqa: E402
from phase0.tools.core import Run, read_trace  # noqa: E402
from phase0.tools.mcp_server import build_server, main, parser  # noqa: E402


@pytest.fixture
def run(tmp_path):
    db = tmp_path / 'ledger.sqlite'
    LedgerStore(db).import_ledger(build(), 'test')
    return Run(ledger_db=db, runs_dir=tmp_path / 'runs')


def session(server, fn):
    async def go():
        async with Client(server) as client:
            return await fn(client)
    return asyncio.run(go())


def test_tool_list_and_annotations(run):
    tools = session(build_server('all', run), lambda c: c.list_tools()).tools
    by_name = {t.name: t for t in tools}
    assert len(by_name) == 13
    assert not any(w in n for n in by_name for w in ('confirm', 'reject', 'review', 'import'))
    assert by_name['ledger_propose'].annotations.read_only_hint is False
    assert by_name['ledger_propose'].annotations.destructive_hint is False
    assert by_name['chem_describe'].annotations.read_only_hint is True
    assert by_name['patent_fetch'].annotations.open_world_hint is True
    assert all(t.input_schema.get('additionalProperties') is False for t in tools)
    chem = session(build_server('chem', run), lambda c: c.list_tools()).tools
    assert sorted(t.name for t in chem) == ['chem_compare_observations', 'chem_describe', 'chem_mass_check']


def test_results_are_envelopes_and_failures_are_tool_errors(run):
    server = build_server('all', run)
    ok = session(server, lambda c: c.call_tool('ledger_search_observations', {'compound_id': 'WO2011138751A2:example:6'}))
    assert ok.is_error is False
    body = json.loads(ok.content[0].text)
    assert set(body) == {'summary', 'data', 'preview'} and body['data']['total'] == 4
    bad = session(server, lambda c: c.call_tool('ledger_get_record', {'kind': 'compounds', 'record_id': 'nope'}))
    assert bad.is_error is True and '没有编号 nope' in bad.content[0].text


def test_misspelled_arguments_are_rejected_not_ignored(run):
    result = session(build_server('ledger', run),
                     lambda c: c.call_tool('ledger_search_observations', {'compund_id': 'WO2011138751A2:example:6'}))
    assert result.is_error is True and 'compund_id' in result.content[0].text


def test_confirmation_is_refused_through_mcp(run):
    record = {'id': 'agent:obs:1', 'document_id': 'WO2011138751A2', 'compound_id': 'WO2011138751A2:example:6',
              'assay_id': 'WO2011138751A2:ALK_WT_Ki', 'status': 'measured', 'relation': '=', 'value': 0.62,
              'unit': 'nM', 'source': {'pdf_page': 186}, 'raw': {},
              'review': {'record_status': 'confirmed', 'provenance_status': 'agent', 'reviewer': 'agent'}}
    result = session(build_server('ledger', run), lambda c: c.call_tool(
        'ledger_propose', {'kind': 'observations', 'record': record, 'note': 'try to confirm'}))
    assert result.is_error is True and 'proposed' in result.content[0].text


def test_mcp_calls_land_in_the_run_trace(run):
    session(build_server('chem', run), lambda c: c.call_tool('chem_describe', {'smiles': 'CCO'}))
    assert [e['tool'] for e in read_trace(run.dir)] == ['chem_describe']


def test_cli_rejects_unknown_domain():
    with pytest.raises(SystemExit):
        main(['everything'])


def test_stdio_subprocess_as_clients_launch_it(tmp_path):
    """The path Claude Code / the Agent SDK use: spawn the CLI and talk over stdin/stdout."""
    import sys
    from pathlib import Path
    from mcp import StdioServerParameters
    root = Path(__file__).resolve().parents[2]
    params = StdioServerParameters(command=sys.executable, cwd=str(root),
                                   args=['-m', 'phase0.tools.mcp_server', 'chem', '--runs-dir', str(tmp_path)])
    result = session(params, lambda c: c.call_tool('chem_describe', {'smiles': 'CCO'}))
    assert result.is_error is False and 'C2H6O' in result.content[0].text
    traces = list(tmp_path.glob('*/trace.jsonl'))
    assert len(traces) == 1 and '"chem_describe"' in traces[0].read_text(encoding='utf-8')


def test_example_client_config_matches_cli_and_readme():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    example = json.loads((root / '.mcp.json.example').read_text(encoding='utf-8'))
    readme = (root / 'phase0' / 'tools' / 'README.md').read_text(encoding='utf-8')
    assert json.loads(re.search(r'```json\n(.*?)```', readme, re.S).group(1)) == example
    assert not (root / '.mcp.json').exists()  # not enabled by default
    for name, server in example['mcpServers'].items():
        assert server['args'][:2] == ['-m', 'phase0.tools.mcp_server']
        args = parser().parse_args(server['args'][2:])
        assert name == f'sar-{args.domain}'
        assert args.ledger_db or args.domain == 'sources'
