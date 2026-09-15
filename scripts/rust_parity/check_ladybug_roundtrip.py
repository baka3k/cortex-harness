#!/usr/bin/env python3
"""Roundtrip check LadybugDB (phase 06): Python đọc store mà Rust đã ghi.

Chạy từ repo root:
    .venv/bin/python scripts/rust_parity/check_ladybug_roundtrip.py
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import ladybug

REPO = Path(__file__).resolve().parents[2]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        export_path = Path(tmp) / "ladybug_rust_written_store"
        env = {**os.environ, "CORTEX_EXPORT_LADYBUG_STORE": str(export_path)}
        subprocess.run(
            ["cargo", "test", "-p", "cortex-graph-driver",
             "--test", "cross_language", "rust_writes_into_python_store"],
            cwd=REPO / "rust",
            check=True,
            env=env,
            capture_output=True,
        )
        assert export_path.exists(), "Rust chưa export store"

        db = ladybug.Database(str(export_path))
        conn = ladybug.Connection(db)
        result = conn.execute("MATCH (f:File) RETURN f.id ORDER BY f.id")
        ids = [row[0] for row in result]
        db.close()

    assert "rust-file-1" in ids, f"node Rust ghi phải đọc được từ Python, got {ids}"
    assert ids == ["py-file-1", "py-file-2", "rust-file-1"], ids
    print(f"roundtrip OK — Python ladybug đọc đúng {len(ids)} nodes (có node do Rust ghi)")


if __name__ == "__main__":
    main()
