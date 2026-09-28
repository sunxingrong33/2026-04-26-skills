"""Replay a recorded agent run against its starting ledger snapshot.

    python -m phase0.tools.replay artifacts/runs/<run_id> [--allow-network]

Returns 0 when every call reproduces its recorded result hash, 1 otherwise.
"""
import argparse
import json
import sys

from . import chem_tools, ledger_tools, source_tools  # noqa: F401  (register tools)
from .core import replay


def main(argv=None):
    ap = argparse.ArgumentParser(description='按轨迹重放一次 agent 运行')
    ap.add_argument('run_dir')
    ap.add_argument('--allow-network', action='store_true', help='允许联网（默认只用缓存）')
    args = ap.parse_args(argv)
    report = replay(args.run_dir, allow_network=args.allow_network)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report['faithful'] else 1


if __name__ == '__main__':
    sys.exit(main())
