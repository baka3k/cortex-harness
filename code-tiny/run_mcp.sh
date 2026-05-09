#!/usr/bin/env bash
set -euo pipefail

# Prevent MSYS/Git Bash from converting Unix paths (e.g. /mcp) to Windows paths.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="fast"
TRANSPORT="${FASTMCP_TRANSPORT:-${MCP_FASTMCP_TRANSPORT:-streamable-http}}"
HOST="${FASTMCP_HOST:-${MCP_FASTMCP_HOST:-127.0.0.1}}"
PORT="${FASTMCP_PORT:-${MCP_FASTMCP_PORT:-8788}}"
STREAM_PATH="${FASTMCP_STREAMABLE_HTTP_PATH:-${MCP_FASTMCP_PATH:-/mcp}}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
VENV_PATH="${VENV_PATH:-$ROOT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python}"
BACKEND_URL=""

usage() {
  cat <<'EOF'
Usage:
  ./run_mcp.sh [options]

Options:
  --mode fast|java|cplus|android   Which MCP to run (default: fast)
  --transport stdio|sse|streamable-http
  --host HOST                     HTTP host (ignored for stdio)
  --port PORT                     HTTP port (ignored for stdio)
  --path /mcp                     HTTP path (ignored for stdio)
  --env-file PATH                 .env file to source (default: ./\.env)
  --venv PATH                     venv path to activate (default: ./\.venv)
  --python BIN                    python executable (default: python)
  --backend-url URL               (accepted for convenience; ignored in this repo)
  -h, --help

Examples:
  ./run_mcp.sh
  ./run_mcp.sh --mode java --port 8790
  ./run_mcp.sh --mode cplus --transport stdio
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"; shift 2;;
    --transport)
      TRANSPORT="${2:-}"; shift 2;;
    --host)
      HOST="${2:-}"; shift 2;;
    --port)
      PORT="${2:-}"; shift 2;;
    --path)
      STREAM_PATH="${2:-}"; shift 2;;
    --env-file)
      ENV_FILE="${2:-}"; shift 2;;
    --venv)
      VENV_PATH="${2:-}"; shift 2;;
    --python)
      PYTHON_BIN="${2:-}"; shift 2;;
    --backend-url)
      BACKEND_URL="${2:-}"; shift 2;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2;;
  esac
done

if [[ -n "$BACKEND_URL" ]]; then
  echo "[warn] --backend-url is not supported by fastmcp_server.py in this repo; ignoring." >&2
fi

if [[ -d "$VENV_PATH" && -f "$VENV_PATH/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "$VENV_PATH/bin/activate"
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

case "$MODE" in
  fast|default|proxy)
    SCRIPT_PATH="$ROOT_DIR/mcp/fastmcp_server.py"
    ;;
  java)
    SCRIPT_PATH="$ROOT_DIR/mcp/java/java_mcp.py"
    ;;
  cplus)
    SCRIPT_PATH="$ROOT_DIR/mcp/cplus/cplus_mcp.py"
    ;;
  android)
    SCRIPT_PATH="$ROOT_DIR/mcp/android/android_mcp.py"
    ;;
  *)
    echo "Unsupported --mode: $MODE" >&2
    exit 2
    ;;
esac

if [[ ! -f "$SCRIPT_PATH" ]]; then
  echo "Script not found: $SCRIPT_PATH" >&2
  exit 2
fi

if [[ -n "$STREAM_PATH" && "$STREAM_PATH" != /* ]]; then
  STREAM_PATH="/$STREAM_PATH"
fi

CMD=("$PYTHON_BIN" "$SCRIPT_PATH" --transport "$TRANSPORT")
if [[ "$TRANSPORT" != "stdio" ]]; then
  CMD+=(--host "$HOST" --port "$PORT" --path "$STREAM_PATH")
fi

exec "${CMD[@]}"
