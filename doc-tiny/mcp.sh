#!/usr/bin/env bash
set -euo pipefail

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
fi

if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

if [ -n "${CORTEX_HARNESS_ENV_FILE:-}" ] && [ -f "$CORTEX_HARNESS_ENV_FILE" ]; then
    source "$CORTEX_HARNESS_ENV_FILE"
fi

# Vector-lane phase-05 cutover: CORTEX_MCP_BACKEND=python keeps the legacy
# python server (rollback flag); unset/auto/rust execs the cortex-mcp binary.
# Runs AFTER the env sourcing so the rust process inherits CORTEX_* vars.
if [ "${CORTEX_MCP_BACKEND:-}" != "python" ]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    repo_root="$(dirname "$script_dir")"
    for bin in "${CORTEX_MCP_BIN:-}" \
               "$repo_root/rust/target/release/cortex-mcp" \
               "$repo_root/rust/target/debug/cortex-mcp"; do
        if [ -n "$bin" ] && [ -x "$bin" ]; then
            exec "$bin" "$@"
        fi
    done
fi

MSYS_NO_PATHCONV=1 python mcp_graph_rag.py --host 127.0.0.1 --port 8789 --transport streamable-http --path /mcp "$@"
