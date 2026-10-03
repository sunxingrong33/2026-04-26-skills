"""The one place analyses get their evidence from.

By default the ledger is built from the committed evidence, exactly as before.
Setting ``SAR_LEDGER_DB`` to a SQLite file makes analyses read that store
instead (explicit opt-in, so a stale database never takes over silently; see
``python -m phase0.ledger.store status``).

Analysis views drop rejected records -- and observations whose compound,
assay or document was rejected -- but never delete them from the store.
"""
import os

from .migrate import build, to_legacy
from .store import LedgerStore

ENV = 'SAR_LEDGER_DB'


def current_ledger():
    path = os.environ.get(ENV)
    return LedgerStore(path).load() if path else build()


def usable(ledger):
    rejected = {(k, r.id) for k in ('documents', 'compounds', 'assays')
                for r in getattr(ledger, k) if r.review.record_status == 'rejected'}
    kept = [o for o in ledger.observations
            if o.review.record_status != 'rejected'
            and ('documents', o.document_id) not in rejected
            and ('compounds', o.compound_id) not in rejected
            and ('assays', o.assay_id) not in rejected]
    return ledger.model_copy(update={'observations': kept})


def analysis_view(ledger=None):
    """Legacy row format consumed by the existing analyses and ``/api/evidence``."""
    return to_legacy(usable(ledger if ledger is not None else current_ledger()))
