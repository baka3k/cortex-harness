#!/usr/bin/env bash
# Build PyO3 extension + chạy parity check Python↔Rust cho retrieval brain.
# Chạy từ repo root: bash scripts/rust_parity/build_pyo3.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_DIR="$REPO/scripts/rust_parity"

cd "$REPO/rust"
# macOS linker cần cho phép undefined symbols (extension module resolve lúc import).
RUSTFLAGS="${RUSTFLAGS:-} -C link-arg=-undefined -C link-arg=dynamic_lookup" \
    cargo build -p cortex-retrieval-py --release

SO_NAME="cortex_retrieval_py.so"
case "$(uname -s)" in
    Darwin) SRC="target/release/libcortex_retrieval_py.dylib" ;;
    Linux)  SRC="target/release/libcortex_retrieval_py.so" ;;
    *) echo "unsupported OS"; exit 1 ;;
esac
cp "$SRC" "$OUT_DIR/$SO_NAME"
echo "built $OUT_DIR/$SO_NAME"

"$REPO/.venv/bin/python" "$REPO/scripts/rust_parity/test_pyo3_parity.py"
