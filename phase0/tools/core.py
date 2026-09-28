"""Tool registry, run trace and replay -- independent of MCP and of any model.

Every tool is a plain function ``fn(ctx, **arguments)``. Arguments are validated
with pydantic from the function signature before the tool runs; the tool
returns ``envelope(summary, data, preview)`` or raises ``ToolFailure`` with a
message the agent can act on.

A ``Run`` owns one working directory under ``artifacts/runs/<run_id>/``:

* ``trace.jsonl`` -- one line per call: tool, validated arguments, whether it
  failed, and the SHA-256 of the canonical result;
* ``ledger.start.sqlite`` -- a snapshot of the ledger store taken when the run
  began, so the run can be replayed exactly, writes included.

``replay`` copies the snapshot, re-executes every call in order against the
copy and compares result hashes. Network access is blocked during replay, so
source tools must be answered from the run's caches; a call that would need
the network shows up as a mismatch rather than silently fetching new data.
"""
import hashlib
import inspect
import json
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from unittest import mock
from urllib.error import URLError

from pydantic import ValidationError, create_model

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / 'artifacts' / 'runs'
PREVIEW = 5


class ToolFailure(Exception):
    """A failure the agent should see and can recover from (bad input, missing record, refusal)."""


def envelope(summary, data, preview=None):
    return {'summary': summary, 'data': data, 'preview': preview}


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


@dataclass
class ToolSpec:
    name: str
    domain: str
    description: str
    func: Callable
    read_only: bool
    open_world: bool
    idempotent: bool
    params: list = field(default_factory=list)
    model: Any = None

    def validate(self, arguments):
        if not isinstance(arguments, dict):
            raise ToolFailure('参数必须是对象')
        try:
            return self.model.model_validate(arguments).model_dump()
        except ValidationError as exc:
            problems = '；'.join(f"{'.'.join(map(str, e['loc'])) or '参数'}: {e['msg']}" for e in exc.errors())
            raise ToolFailure(f'参数无效：{problems}') from None


REGISTRY: dict[str, ToolSpec] = {}


def tool(domain, *, read_only=True, open_world=False, idempotent=True):
    """Register ``fn(ctx, **params)``; the docstring is the description the agent reads."""
    def register(fn):
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())[1:]
        fields = {p.name: (p.annotation, ... if p.default is inspect.Parameter.empty else p.default)
                  for p in params}
        model = create_model(f'{fn.__name__}_input', __config__={'extra': 'forbid'}, **fields)
        REGISTRY[fn.__name__] = ToolSpec(fn.__name__, domain, inspect.cleandoc(fn.__doc__ or ''), fn,
                                         read_only, open_world, idempotent, params, model)
        return fn
    return register


def tools_in(domain):
    return [s for s in REGISTRY.values() if s.domain == domain]


class Run:
    """One agent session: context for tools plus an append-only trace."""

    def __init__(self, ledger_db=None, run_id=None, runs_dir=RUNS, record=True, cache_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir else ROOT / 'artifacts'
        self.run_id = run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        self.ledger_db = str(ledger_db) if ledger_db else None
        self.actor = f'agent-run:{self.run_id}'
        self.dir = Path(runs_dir) / self.run_id
        self.record = record
        self.seq = 0
        if record:
            self.dir.mkdir(parents=True, exist_ok=False)
            if self.ledger_db and Path(self.ledger_db).exists():
                snapshot(self.ledger_db, self.dir / 'ledger.start.sqlite')
            (self.dir / 'run.json').write_text(canonical({
                'run_id': self.run_id, 'started_at': datetime.now(timezone.utc).isoformat(),
                'ledger_db': self.ledger_db, 'cache_dir': str(self.cache_dir),
                'snapshot': (self.dir / 'ledger.start.sqlite').exists()}) + '\n',
                encoding='utf-8')

    def store(self):
        from phase0.ledger.store import LedgerStore
        if not self.ledger_db:
            raise ToolFailure('本次会话未配置台账数据库；台账工具不可用。')
        store = LedgerStore(self.ledger_db)
        if store.is_empty():
            raise ToolFailure('台账数据库为空；请先运行 python -m phase0.ledger.store init。')
        return store

    def call(self, name, arguments):
        spec = REGISTRY.get(name)
        if spec is None:
            raise ToolFailure(f'未知工具：{name}')
        self.seq += 1
        entry = {'seq': self.seq, 'at': datetime.now(timezone.utc).isoformat(), 'tool': name}
        try:
            args = spec.validate(arguments)
            entry['arguments'] = args
            result = spec.func(self, **args)
            entry.update(ok=True, result_sha256=digest(result), summary=result.get('summary'))
            return result
        except ToolFailure as exc:
            entry.setdefault('arguments', arguments)
            entry.update(ok=False, error=str(exc), result_sha256=digest({'error': str(exc)}))
            raise
        finally:
            if self.record:
                with (self.dir / 'trace.jsonl').open('a', encoding='utf-8') as out:
                    out.write(canonical(entry) + '\n')


def snapshot(src, dest):
    with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
        source.backup(target)


def read_trace(run_dir):
    path = Path(run_dir) / 'trace.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _offline(*args, **kwargs):
    raise URLError('重放时禁止联网')


NETWORK = ('phase0.sar.discovery.urlopen', 'phase0.sar.patents.urlopen', 'phase0.sar.surechembl.urlopen')


def replay(run_dir, allow_network=False):
    """Re-execute a recorded run against its starting snapshot and compare every result hash."""
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
    entries = read_trace(run_dir)
    report = {'run_id': meta['run_id'], 'calls': len(entries), 'matched': 0, 'mismatched': [],
              'network_blocked': not allow_network}
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        if not allow_network:
            for target in NETWORK:
                stack.enter_context(mock.patch(target, _offline))
        db = None
        if meta['snapshot']:
            db = Path(tmp) / 'ledger.sqlite'
            shutil.copyfile(run_dir / 'ledger.start.sqlite', db)
        # Same run id, so actors and therefore audit entries and results are reproduced exactly.
        run = Run(ledger_db=db, run_id=meta['run_id'], record=False, cache_dir=meta.get('cache_dir'))
        for e in entries:
            try:
                got = digest(run.call(e['tool'], e['arguments']))
            except ToolFailure as exc:
                got = digest({'error': str(exc)})
            if got == e['result_sha256']:
                report['matched'] += 1
            else:
                report['mismatched'].append({'seq': e['seq'], 'tool': e['tool']})
    report['faithful'] = not report['mismatched']
    return report
