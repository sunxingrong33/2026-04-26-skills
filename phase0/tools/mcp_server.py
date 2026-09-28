"""MCP adapters over the tool registry (official ``mcp`` SDK 2.x, ``pip install -r requirements-agent.txt``).

One process = one ``Run``: every call is validated, executed and traced by
``phase0.tools.core``; this module only translates to MCP. A ``ToolFailure``
becomes an MCP tool error the agent can read and recover from.

    python -m phase0.tools.mcp_server ledger  --ledger-db artifacts/ledger.sqlite
    python -m phase0.tools.mcp_server chem    [--ledger-db ...]
    python -m phase0.tools.mcp_server sources
    python -m phase0.tools.mcp_server all     --ledger-db artifacts/ledger.sqlite
"""
import argparse
import inspect

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import ConfigDict

from . import chem_tools, ledger_tools, source_tools  # noqa: F401  (register tools)
from .core import Run, ToolFailure, tools_in

DOMAINS = ('ledger', 'chem', 'sources')
INSTRUCTIONS = (
    '药物化学证据工具。所有数值（描述符、比值、质量校验）都由工具的确定性代码计算，不要自行估算。'
    '台账记录都有复核状态；你只能提交 proposed（待确认）记录，并在 note 中写明依据，确认必须由具名的人完成。'
    '限定值（如 "<0.2 nM"）不是精确值；缺失测量不能补数；不同来源或不同实验的数值不能直接比较。'
    '每次调用都会记录在本次运行的轨迹中。')


def _adapter(run, spec):
    def call(**arguments):
        try:
            return run.call(spec.name, arguments)
        except ToolFailure as exc:
            raise ToolError(str(exc)) from None
    call.__name__ = spec.name
    call.__doc__ = spec.description
    call.__signature__ = inspect.Signature(spec.params, return_annotation=dict)
    call.__annotations__ = {p.name: p.annotation for p in spec.params} | {'return': dict}
    return call


def _forbid_unknown_arguments(server, name):
    """Reject misspelled or unknown arguments instead of silently dropping them.

    The SDK's argument model ignores extra keys, so a typo such as ``compund_id`` would
    turn a filtered search into an unfiltered one without telling the agent. The SDK
    offers no public switch; its FuncMetadata fields are read live, so the registered
    tool gets a stricter subclass of its argument model and a matching published schema.
    Covered by a test so an SDK change cannot silently undo it (mcp pinned in
    requirements-agent.txt).
    """
    registered = server._tool_manager.get_tool(name)
    base = registered.fn_metadata.arg_model
    strict = type(base.__name__, (base,), {'model_config': ConfigDict(**base.model_config, extra='forbid')})
    registered.fn_metadata.arg_model = strict
    registered.parameters = strict.model_json_schema(by_alias=True)


def build_server(domain, run):
    domains = DOMAINS if domain == 'all' else (domain,)
    server = MCPServer(name=f'sar-{domain}', instructions=INSTRUCTIONS)
    for d in domains:
        for spec in tools_in(d):
            annotations = ToolAnnotations(read_only_hint=spec.read_only, destructive_hint=False,
                                          idempotent_hint=spec.idempotent, open_world_hint=spec.open_world)
            server.tool(name=spec.name, description=spec.description, annotations=annotations,
                        structured_output=False)(_adapter(run, spec))
            _forbid_unknown_arguments(server, spec.name)
    return server


def parser():
    ap = argparse.ArgumentParser(description='SAR Atlas MCP 工具服务（stdio）')
    ap.add_argument('domain', choices=[*DOMAINS, 'all'])
    ap.add_argument('--ledger-db', help='台账 SQLite；台账工具必需')
    ap.add_argument('--runs-dir', help='运行轨迹目录，默认 artifacts/runs')
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    kwargs = {'runs_dir': args.runs_dir} if args.runs_dir else {}
    run = Run(ledger_db=args.ledger_db, **kwargs)
    build_server(args.domain, run).run('stdio')


if __name__ == '__main__':
    main()
