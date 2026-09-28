"""SQLite storage for the typed ledger, with an append-only audit log.

Write paths mirror the review contract:

* ``propose`` adds a new record and only accepts ``record_status="proposed"``;
  it is the only write a tool or agent will be given;
* ``review`` moves a record to ``confirmed`` or ``rejected`` and requires a
  named reviewer; it is meant for people, not tools;
* ``import_ledger`` replaces the whole store from committed evidence.

Every write is validated against the full ledger (referential integrity,
status rules) before it is committed, and appended to ``audit`` with hashes of
the record before and after. Nothing is deleted: a rejected record stays in the
store and is only excluded from analysis views.

    python -m phase0.ledger.store init   [--db artifacts/ledger.sqlite]
    python -m phase0.ledger.store status [--db ...]
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schema import (Assay, Compound, Document, DocumentRelation, InputFile, Ledger, Observation,
                     RecordStatus)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / 'artifacts' / 'ledger.sqlite'
KINDS = {'documents': Document, 'compounds': Compound, 'assays': Assay,
         'observations': Observation, 'relations': DocumentRelation}


def _hash(body):
    return hashlib.sha256(body.encode('utf-8')).hexdigest() if body else None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class LedgerStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.path)
        con.execute('PRAGMA foreign_keys = ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    def _init(self):
        with self._connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, '
                        'ord INTEGER NOT NULL, record_status TEXT NOT NULL, body TEXT NOT NULL, '
                        'PRIMARY KEY (kind, id))')
            con.execute('CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, '
                        'at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, kind TEXT, '
                        'record_id TEXT, before_sha256 TEXT, after_sha256 TEXT, note TEXT)')

    def _audit(self, con, actor, action, kind=None, record_id=None, before=None, after=None, note=None):
        if not actor:
            raise ValueError('写入台账必须写明操作者')
        con.execute('INSERT INTO audit (at, actor, action, kind, record_id, before_sha256, after_sha256, note) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (_now(), actor, action, kind, record_id, _hash(before), _hash(after), note))

    # -- reading ---------------------------------------------------------------

    def is_empty(self):
        with self._connect() as con:
            return con.execute("SELECT 1 FROM meta WHERE key = 'envelope'").fetchone() is None

    def load(self):
        with self._connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key = 'envelope'").fetchone()
            if row is None:
                raise LookupError(f'台账数据库为空，请先运行 python -m phase0.ledger.store init --db {self.path}')
            envelope = json.loads(row[0])
            data = {k: [] for k in KINDS}
            for kind, body in con.execute('SELECT kind, body FROM records ORDER BY kind, ord'):
                data[kind].append(json.loads(body))
        return Ledger.model_validate({**envelope, **data})

    def audit_log(self, record_id=None):
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            sql = 'SELECT * FROM audit' + (' WHERE record_id = ?' if record_id else '') + ' ORDER BY seq'
            return [dict(r) for r in con.execute(sql, (record_id,) if record_id else ())]

    def inputs(self):
        with self._connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key = 'envelope'").fetchone()
        return json.loads(row[0])['inputs'] if row else None

    # -- writing ---------------------------------------------------------------

    def import_ledger(self, ledger, actor):
        envelope = {'schema_version': ledger.schema_version, 'scope': ledger.scope, 'notice': ledger.notice,
                    'inputs': [i.model_dump() for i in ledger.inputs]}
        with self._connect() as con:
            con.execute('DELETE FROM records')
            con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('envelope', ?)",
                        (json.dumps(envelope, ensure_ascii=False),))
            for kind in KINDS:
                for i, record in enumerate(getattr(ledger, kind)):
                    con.execute('INSERT INTO records (kind, id, ord, record_status, body) VALUES (?, ?, ?, ?, ?)',
                                (kind, record.id, i, record.review.record_status, record.model_dump_json(exclude_unset=True)))
            self._audit(con, actor, 'import', note=json.dumps(
                {k: len(getattr(ledger, k)) for k in KINDS} | {'inputs': [i.sha256[:12] for i in ledger.inputs]}))

    def propose(self, kind, record, actor, note=None):
        """Add one new record; it must be ``proposed`` and keep the ledger valid."""
        if kind not in KINDS:
            raise ValueError(f'未知记录类型：{kind}')
        item = KINDS[kind].model_validate(record)
        if item.review.record_status != RecordStatus.proposed.value:
            raise PermissionError('新写入的记录只能是 proposed；确认或拒绝须经人工复核')
        ledger = self.load()
        if any(x.id == item.id for x in getattr(ledger, kind)):
            raise ValueError(f'{kind} 中已存在编号 {item.id}；修改须走复核，不能覆盖')
        data = json.loads(ledger.model_dump_json(exclude_unset=True))
        data[kind].append(json.loads(item.model_dump_json(exclude_unset=True)))
        Ledger.model_validate(data)
        body = item.model_dump_json(exclude_unset=True)
        with self._connect() as con:
            ord_ = con.execute('SELECT COALESCE(MAX(ord), -1) + 1 FROM records WHERE kind = ?', (kind,)).fetchone()[0]
            con.execute('INSERT INTO records (kind, id, ord, record_status, body) VALUES (?, ?, ?, ?, ?)',
                        (kind, item.id, ord_, item.review.record_status, body))
            self._audit(con, actor, 'propose', kind, item.id, None, body, note)
        return item

    def propose_batch(self, items, actor, note=None):
        """Add several new records atomically; ids already in the store are skipped, not overwritten.

        ``items`` is a list of ``(kind, record)``. The whole ledger is validated once with
        every addition before anything is written, so a batch lands completely or not at all.
        """
        ledger = self.load()
        existing = {k: {x.id for x in getattr(ledger, k)} for k in KINDS}
        data = json.loads(ledger.model_dump_json(exclude_unset=True))
        added, skipped = [], []
        for kind, record in items:
            if kind not in KINDS:
                raise ValueError(f'未知记录类型：{kind}')
            item = KINDS[kind].model_validate(record)
            if item.review.record_status != RecordStatus.proposed.value:
                raise PermissionError('新写入的记录只能是 proposed；确认或拒绝须经人工复核')
            if item.id in existing[kind]:
                skipped.append((kind, item.id))
                continue
            existing[kind].add(item.id)
            data[kind].append(json.loads(item.model_dump_json(exclude_unset=True)))
            added.append((kind, item))
        Ledger.model_validate(data)
        with self._connect() as con:
            for kind, item in added:
                ord_ = con.execute('SELECT COALESCE(MAX(ord), -1) + 1 FROM records WHERE kind = ?',
                                   (kind,)).fetchone()[0]
                body = item.model_dump_json(exclude_unset=True)
                con.execute('INSERT INTO records (kind, id, ord, record_status, body) VALUES (?, ?, ?, ?, ?)',
                            (kind, item.id, ord_, item.review.record_status, body))
                self._audit(con, actor, 'propose', kind, item.id, None, body, note)
        return {'added': [(k, i.id) for k, i in added], 'already_present': skipped}

    def review(self, kind, record_id, status, reviewer, note):
        """Confirm or reject an existing record; a named reviewer and a reason are required."""
        if status not in (RecordStatus.confirmed.value, RecordStatus.rejected.value):
            raise ValueError('复核结论只能是 confirmed 或 rejected')
        if not reviewer or not note:
            raise ValueError('复核必须写明复核人和理由')
        with self._connect() as con:
            row = con.execute('SELECT body FROM records WHERE kind = ? AND id = ?', (kind, record_id)).fetchone()
        if row is None:
            raise LookupError(f'{kind} 中没有编号 {record_id}')
        before = row[0]
        data = json.loads(before)
        data['review'] = {**data['review'], 'record_status': status, 'reviewer': reviewer}
        item = KINDS[kind].model_validate(data)
        after = item.model_dump_json(exclude_unset=True)
        with self._connect() as con:
            con.execute('UPDATE records SET record_status = ?, body = ? WHERE kind = ? AND id = ?',
                        (status, after, kind, record_id))
            self._audit(con, reviewer, status, kind, record_id, before, after, note)
        return item


def main(argv=None):
    from .migrate import build
    ap = argparse.ArgumentParser(description='证据台账 SQLite 存储')
    ap.add_argument('command', choices=['init', 'status'])
    ap.add_argument('--db', default=str(DEFAULT_DB))
    ap.add_argument('--actor', default='migration')
    args = ap.parse_args(argv)
    store = LedgerStore(args.db)
    if args.command == 'init':
        ledger = build()
        store.import_ledger(ledger, args.actor)
        print(f'已导入 {len(ledger.observations)} 条观测到 {args.db}')
        return 0
    if store.is_empty():
        print('台账数据库为空')
        return 1
    from phase0.sar.evidence_ledger import build_ledger
    ledger = store.load()
    current = build_ledger()['inputs']
    stale = store.inputs() != current
    counts = {k: len(getattr(ledger, k)) for k in KINDS}
    statuses = {}
    for k in KINDS:
        for r in getattr(ledger, k):
            statuses[r.review.record_status] = statuses.get(r.review.record_status, 0) + 1
    print(json.dumps({'db': args.db, 'counts': counts, 'record_status': statuses,
                      'audit_entries': len(store.audit_log()),
                      'stale_vs_committed_data': stale}, ensure_ascii=False, indent=1))
    return 2 if stale else 0


if __name__ == '__main__':
    sys.exit(main())
