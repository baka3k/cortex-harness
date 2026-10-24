#!/usr/bin/env bash
set -euo pipefail

# Absolute script path so the process cmdline carries
# "…/link-tiny/mcp_server.py" — the substring the lifecycle/dev pause logic
# matches on (unique vs code-tiny's fastmcp_server.py).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
elif [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate"
elif [ -f "$ROOT_DIR/.venv/Scripts/activate" ]; then
    source "$ROOT_DIR/.venv/Scripts/activate"
fi

if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# The active harness environment has the highest precedence. The lifecycle
# launcher generates this file from .cortext-harness/config/<active>.json.
if [ -n "${CORTEX_HARNESS_ENV_FILE:-}" ] && [ -f "$CORTEX_HARNESS_ENV_FILE" ]; then
    source "$CORTEX_HARNESS_ENV_FILE"
fi

# Git Bash otherwise rewrites the route argument to C:/Program Files/Git/mcp
# before invoking the Windows Python executable.
MSYS_NO_PATHCONV=1 python "$SCRIPT_DIR/mcp_server.py" --transport streamable-http --host 127.0.0.1 --port 8790 --path /mcp "$@"
