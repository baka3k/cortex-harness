#!/usr/bin/env bash
set -euo pipefail

# Fail-fast init checks for harness runtime.

REQUIRED_CMDS=(python3)
OPTIONAL_CMDS=(curl jq)

STRICT_INIT="${STRICT_INIT:-0}"
GRAPH_MCP_URL="${GRAPH_MCP_URL:-http://127.0.0.1:8788/mcp}"
MIND_MCP_URL="${MIND_MCP_URL:-${VECTOR_MCP_URL:-http://127.0.0.1:8789/mcp}}"

echo "[init] Starting harness init checks"

for cmd in "${REQUIRED_CMDS[@]}"; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[init][error] Missing required command: $cmd"
    exit 1
  fi
done

for cmd in "${OPTIONAL_CMDS[@]}"; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[init][warn] Optional command not found: $cmd"
  fi
done

check_mcp_url() {
  local name="$1"
  local url="$2"

  if [[ -z "$url" ]]; then
    echo "[init][warn] $name not configured"
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1; then
    if [[ "$STRICT_INIT" == "1" ]]; then
      echo "[init][error] curl is required in strict mode for MCP checks"
      exit 1
    fi
    echo "[init][warn] Skipping MCP check for $name because curl is unavailable"
    return 0
  fi

  local payload
  payload='{"jsonrpc":"2.0","id":"init-check","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"harness-init","version":"0.1"}}}'

  local code
  code=$(curl -sS -o /dev/null -w "%{http_code}" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d "$payload" "$url" || true)

  if [[ "$code" == "200" || "$code" == "202" || "$code" == "204" ]]; then
    echo "[init] $name MCP reachable: $url (HTTP $code)"
  elif [[ "$code" == "406" ]]; then
    echo "[init][warn] $name reachable but transport handshake mismatch (HTTP 406): $url"
  else
    if [[ "$STRICT_INIT" == "1" ]]; then
      echo "[init][error] $name MCP unreachable: $url (HTTP $code)"
      exit 1
    fi
    echo "[init][warn] $name MCP may be unreachable: $url (HTTP $code)"
  fi
}

check_mcp_url "graph_mcp" "$GRAPH_MCP_URL"
check_mcp_url "mind_mcp" "$MIND_MCP_URL"

echo "[init] Init checks completed"
