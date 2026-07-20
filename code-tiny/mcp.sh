#!/usr/bin/env bash
set -euo pipefail

# source .env
# python mcp/fastmcp_server.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp
# source .venv/bin/activate && source .env && python mcp/unified_mcp.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp
# source .env 
# python mcp/android/android_mcp.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp
# source .venv/bin/activate && source .env && python mcp/cplus/cplus_mcp.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp
# source .venv/bin/activate && source .env && python mcp/unified_mcp.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp
# source .venv/bin/activate && source .env && python mcp/android/android_mcp.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp
# Activate virtual environment

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

# The active harness environment has the highest precedence. The lifecycle
# launcher generates this file from .cortext-harness/config/<active>.json.
if [ -n "${CORTEX_HARNESS_ENV_FILE:-}" ] && [ -f "$CORTEX_HARNESS_ENV_FILE" ]; then
    source "$CORTEX_HARNESS_ENV_FILE"
fi

# Git Bash otherwise rewrites the route argument to C:/Program Files/Git/mcp
# before invoking the Windows Python executable.
MSYS_NO_PATHCONV=1 python mcp/unified_mcp.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp "$@"
