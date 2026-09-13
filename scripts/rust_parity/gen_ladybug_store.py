#!/usr/bin/env python3
"""Tạo LadybugDB store fixture bằng `ladybug` PyPI (Python side).

Chạy từ repo root:
    .venv/bin/python scripts/rust_parity/gen_ladybug_store.py

Output: rust/crates/cortex-graph-driver/tests/fixtures/ladybug_python_store/
(2 node File do Python ghi — Rust sẽ mở store này trong spike tests.)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import ladybug

REPO = Path(__file__).resolve().parents[2]
STORE = REPO / "rust" / "crates" / "cortex-graph-driver" / "tests" / "fixtures" / "ladybug_python_store"


def main() -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    # Ladybug 0.20.4 store là FILE (không phải directory như kuzu cũ) —
    # "one store file per named graph" theo contract của LadybugDriver.
    for suffix in ["", ".wal"]:
        candidate = Path(f"{STORE}{suffix}")
        if candidate.is_file():
            candidate.unlink()
        elif candidate.is_dir():
            shutil.rmtree(candidate)

    db = ladybug.Database(str(STORE))
    conn = ladybug.Connection(db)
    conn.execute("CREATE NODE TABLE File (id STRING, name STRING, PRIMARY KEY(id))")
    for node_id, name in [("py-file-1", "written-by-python"), ("py-file-2", "also-python")]:
        # ladybug raise RuntimeError khi query lỗi — không cần check is_success.
        conn.execute(
            f"CREATE (f:File {{id: '{node_id}', name: '{name}'}}) RETURN count(f)"
        )
    result = conn.execute("MATCH (f:File) RETURN f.id ORDER BY f.id")
    ids = [row[0] for row in result]
    assert ids == ["py-file-1", "py-file-2"], ids
    db.close()
    print(f"wrote {STORE.relative_to(REPO)} ({len(ids)} nodes)")


if __name__ == "__main__":
    main()
