#!/usr/bin/env bash
# dev entrypoint — binary-only (phase-06 cutover; no Python rollback).
# Resolution (D1): CORTEX_DEV_BIN -> ~/.local/bin/cortex-dev -> repo release build.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${CORTEX_DEV_BIN:-}" ] && [ -x "$CORTEX_DEV_BIN" ]; then
    exec "$CORTEX_DEV_BIN" "$@"
fi
if [ -x "$HOME/.local/bin/cortex-dev" ]; then
    exec "$HOME/.local/bin/cortex-dev" "$@"
fi
if [ -x "$SCRIPT_DIR/rust/target/release/cortex-dev" ]; then
    exec "$SCRIPT_DIR/rust/target/release/cortex-dev" "$@"
fi
echo "[error] cortex-dev binary not found. Install it (make install) or set CORTEX_DEV_BIN." >&2
exit 1
