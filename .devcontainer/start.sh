#!/usr/bin/env bash
# Start the workbench. In a GitHub Codespace, also accept the forwarded port address
# (https://<codespace>-8766.<domain>); the server itself still listens on 127.0.0.1 only.
set -euo pipefail
cd "$(dirname "$0")/.."
args=(--port 8766 --ledger-db artifacts/ledger.sqlite)
if [[ -n "${CODESPACE_NAME:-}" && -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  args+=(--public-host "${CODESPACE_NAME}-8766.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}")
fi
exec python -m phase0.sar.serve "${args[@]}"
