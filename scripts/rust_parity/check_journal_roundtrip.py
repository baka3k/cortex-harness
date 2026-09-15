#!/usr/bin/env python3
"""Roundtrip check 2 chiều cho journal (phase 05).

Chiều 1 (Python → Rust): cargo test `inspect_python_created_journal` đọc DB
do `SQLiteJournal` Python tạo (fixture committed).
Chiều 2 (Rust → Python): script này chạy cargo test với
`CORTEX_EXPORT_JOURNAL_DB` để Rust xuất DB nó tạo, rồi `inspect_journal`
Python thật đọc + so kết quả với expected trong fixture.

Chạy từ repo root:
    python3 scripts/rust_parity/check_journal_roundtrip.py
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
FIXTURE = (
    REPO / "rust" / "crates" / "cortex-graph-core" / "tests" / "fixtures" / "journal_golden.json"
)


def main() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as handle:
        export_path = Path(handle.name)

    env = {**os.environ, "CORTEX_EXPORT_JOURNAL_DB": str(export_path)}
    subprocess.run(
        ["cargo", "test", "-p", "cortex-graph-core", "--test", "journal_golden",
         "inspect_rust_created_journal"],
        cwd=REPO / "rust",
        check=True,
        env=env,
        capture_output=True,
    )

    # Python `inspect_journal` thật đọc DB do Rust tạo.
    import tools.graph.journal.sqlite_store as store_module  # noqa: E402

    fixed = dt.datetime.fromtimestamp(fixture["now_epoch"], tz=dt.timezone.utc)
    original_now = store_module._utc_now
    store_module._utc_now = lambda: fixed
    try:
        actual = store_module.inspect_journal(export_path)
    finally:
        store_module._utc_now = original_now
        export_path.unlink(missing_ok=True)

    assert len(actual) == len(fixture["expected"]), (
        f"run count mismatch: rust_db={len(actual)} python_expected={len(fixture['expected'])}"
    )
    for actual_run, expected_run in zip(actual, fixture["expected"]):
        for key, expected_value in expected_run.items():
            actual_value = actual_run[key]
            if key == "journal_bytes":
                continue  # file size phụ thuộc engine/viewport — so các field khác
            if isinstance(expected_value, float):
                assert abs(actual_value - expected_value) < 1e-9, (
                    f"{actual_run['run_id']}.{key}: rust_db={actual_value} "
                    f"python_expected={expected_value}"
                )
            else:
                assert actual_value == expected_value, (
                    f"{actual_run['run_id']}.{key}: rust_db={actual_value!r} "
                    f"python_expected={expected_value!r}"
                )
    print(f"roundtrip OK — Python inspect_journal đọc đúng {len(actual)} runs từ DB Rust tạo")


if __name__ == "__main__":
    main()
