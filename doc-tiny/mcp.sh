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

MSYS_NO_PATHCONV=1 python mcp_graph_rag.py --host 127.0.0.1 --port 8789 --transport streamable-http --path /mcp "$@"
