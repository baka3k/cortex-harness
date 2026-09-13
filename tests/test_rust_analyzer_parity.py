"""CI wrapper cho phase-04 analyzer parity harness.

Gọi `scripts/rust_parity/analyzer_parity.py` (pytest → 2 binary) sau khi build
`analyzer-python` release. Skip khi thiếu toolchain/graph backend:

- rust binary/cargo không có → skip
- FalkorDB không reachable tại CORTEX_PARITY_HOST:PORT → skip

Stock scenarios chỉ chạy khi ``CORTEX_PARITY_STOCK=1`` (repo thật nằm ngoài
repo này nên CI mặc định chạy testdata + summary gates).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "rust_parity" / "analyzer_parity.py"
PY_BIN = REPO / ".venv" / "bin" / "python"
RUST_DIR = REPO / "rust"
RUST_BIN = RUST_DIR / "target" / "release" / "analyzer-python"

HOST = os.environ.get("CORTEX_PARITY_HOST", "127.0.0.1")
PORT = int(os.environ.get("CORTEX_PARITY_PORT", "6379"))


def _falkordb_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2):
            return True
    except OSError:
        return False


def _ensure_binary() -> str:
    if RUST_BIN.exists():
        return str(RUST_BIN)
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("cargo không có trong PATH")
    subprocess.run(
        [cargo, "build", "--release", "-p", "analyzer-python"],
        cwd=RUST_DIR,
        check=True,
        timeout=600,
    )
    if not RUST_BIN.exists():
        pytest.skip("build analyzer-python thất bại/không tạo binary")
    return str(RUST_BIN)


@pytest.mark.skipif(not HARNESS.exists(), reason="thiếu analyzer_parity.py")
@pytest.mark.skipif(not PY_BIN.exists(), reason="thiếu harness venv")
@pytest.mark.skipif(
    not _falkordb_reachable(), reason=f"FalkorDB {HOST}:{PORT} không reachable"
)
def test_analyzer_parity_testdata(tmp_path) -> None:
    """Phase-04 gate: FULL testdata + incremental + summary schema, diff rỗng."""
    rust_bin = _ensure_binary()
    cmd = [
        str(PY_BIN),
        str(HARNESS),
        "--skip-stock",
        "--report",
        str(tmp_path / "analyzer-parity-testdata.md"),
        "--host",
        HOST,
        "--port",
        str(PORT),
        "--rust-bin",
        rust_bin,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, f"parity FAIL:\n{proc.stdout[-3000:]}\n{proc.stderr[-1000:]}"
    assert "ALL GATES PASS" in proc.stdout


@pytest.mark.skipif(
    os.environ.get("CORTEX_PARITY_STOCK") != "1",
    reason="stock repo thật — bật bằng CORTEX_PARITY_STOCK=1",
)
@pytest.mark.skipif(not PY_BIN.exists(), reason="thiếu harness venv")
def test_analyzer_parity_stock(tmp_path) -> None:
    """Phase-04 gate đầy đủ trên stock thật (FULL + incremental)."""
    rust_bin = _ensure_binary()
    if not _falkordb_reachable():
        pytest.skip(f"FalkorDB {HOST}:{PORT} không reachable")
    cmd = [
        str(PY_BIN),
        str(HARNESS),
        "--report",
        str(tmp_path / "analyzer-parity-full.md"),
        "--host",
        HOST,
        "--port",
        str(PORT),
        "--rust-bin",
        rust_bin,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    assert proc.returncode == 0, f"parity FAIL:\n{proc.stdout[-3000:]}\n{proc.stderr[-1000:]}"
    assert "ALL GATES PASS" in proc.stdout
