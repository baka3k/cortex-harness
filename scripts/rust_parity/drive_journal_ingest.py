#!/usr/bin/env python3
"""Drive Python journal runtime với op-stream ingest THẬT (Track B, capture).

Thay cho ingest dogfood nặng: mô phỏng 1 run ingest gồm các op type thật của
`operation.py` — create-node (``files``/``functions``), update-node (batch
``files`` thứ hai merge lại id đã có), edge ops (``relations:HAS_FILE``,
``calls``, ``calls:site``) — đẩy qua `GraphWriteJournalRuntime` (điểm capture
duy nhất B1) với ``CORTEX_JOURNAL_SHADOW`` bật.

Sản phẩm trong ``--emit-dir``:
- ``capture/python.jsonl`` — JSONL capture (header + ops);
- ``python_store.sqlite3`` — store Python do runtime ghi thật (real clock).

Với ``--check``: build + replay JSONL qua bin Rust `replay_journal` rồi diff
DB-state với store Python — regression evidence cho phase-B2 (exit 1 khi lệch).

Chạy từ repo root:
    .venv/bin/python scripts/rust_parity/drive_journal_ingest.py \
        --emit-dir /tmp/journal-ingest --check
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))

from tools.graph.journal.config import JournalConfig  # noqa: E402
from tools.graph.journal.models import JOURNAL_SCHEMA_VERSION, RunMetadata  # noqa: E402
from tools.graph.journal.runtime import GraphWriteJournalRuntime  # noqa: E402
from tools.graph.journal.shadow import SHADOW_ENV  # noqa: E402

PROJECT = "demo-ingest"


def _rows_files(suffix: str) -> list[dict]:
    return [
        {
            "id": f"file-{suffix}-1",
            "file_path": f"src/module_{suffix}.py",
            "language": "python",
            "project_id": PROJECT,
            "project_id_normalized": PROJECT,
        },
        {
            "id": f"file-{suffix}-2",
            "file_path": f"src/helper_{suffix}.py",
            "language": "python",
            "project_id": PROJECT,
            "project_id_normalized": PROJECT,
        },
    ]


def _rows_functions() -> list[dict]:
    return [
        {
            "id": "fn-001",
            "name": "run_pipeline",
            "file_id": "file-a-1",
            "project_id": PROJECT,
            "project_id_normalized": PROJECT,
        },
        {
            "id": "fn-002",
            "name": "parse_args",
            "file_id": "file-a-1",
            "project_id": PROJECT,
            "project_id_normalized": PROJECT,
        },
    ]


# Op-stream mô phỏng 1 batch ingest thật: create-node → update-node →
# typed edge → call edges. Thứ tự phase: nodes trước, edges/calls sau.
def _batches() -> list[tuple[str, list[dict]]]:
    return [
        ("files", _rows_files("a")),  # create-node
        ("functions", _rows_functions()),  # create-node
        ("files", _rows_files("b")),  # update-node (merge lại plane File)
        ("relations:HAS_FILE", [  # typed relationship edge
            {
                "source_label": "Repository",
                "source_property": "name",
                "source_id": "repo-demo",
                "target_label": "File",
                "target_property": "id",
                "target_id": "file-b-1",
                "rel_type": "HAS_FILE",
                "project_id": PROJECT,
                "project_id_normalized": PROJECT,
            },
        ]),
        ("calls", [  # CALLS edge (call_edge reconciliation)
            {
                "caller_id": "fn-001",
                "callee_id": "fn-002",
                "count": 3,
                "call_type": "direct",
                "project_id": PROJECT,
                "project_id_normalized": PROJECT,
            },
        ]),
        ("calls:site", [  # CALLS edge with site identity
            {
                "caller_id": "fn-002",
                "callee_id": "fn-001",
                "site_id": "site-001",
                "props": {"line": 42},
                "project_id": PROJECT,
                "project_id_normalized": PROJECT,
            },
        ]),
    ]


def drive(emit_dir: Path) -> tuple[Path, Path]:
    """Chạy 1 run ingest mô phỏng qua journal runtime (capture bật)."""

    shadow_dir = emit_dir / "capture"
    os.environ[SHADOW_ENV] = str(shadow_dir)
    try:
        config = JournalConfig(
            mode="required",
            path=emit_dir / "python_store.sqlite3",
            metadata=RunMetadata(
                project_id=PROJECT,
                scope_id=PROJECT,
                source_revision="rev-1",
                source_snapshot="snap-1",
                physical_target="local",
                generation="gen-1",
                parser="python",
                parser_version="1",
                schema_fingerprint="sfp-demo",
                query_shape_version="language-writer-v1",
            ),
        )
        runtime = GraphWriteJournalRuntime(config)
        try:
            for sequence, (label, rows) in enumerate(_batches()):
                ticket = runtime.prepare(label=label, rows=rows, sequence=sequence)
                if not ticket.execute:
                    raise RuntimeError(
                        f"batch {sequence} ({label}) không claim được — thứ tự phase sai"
                    )
                # "Graph write" mô phỏng: ingest thật gọi driver Cypher ở đây;
                # journal chỉ cần count khớp expected để ack thành công.
                runtime.acknowledge(ticket, count=len(rows), elapsed_ms=1)
        finally:
            runtime.close()
    finally:
        os.environ.pop(SHADOW_ENV, None)

    capture = shadow_dir / "python_store.jsonl"
    if not capture.is_file():
        raise RuntimeError("capture không được sinh ra")
    header_line = capture.read_text(encoding="utf-8").splitlines()[0]
    if '"_header":true' not in header_line.replace(" ", ""):
        raise RuntimeError("header capture sai format")
    _version = JOURNAL_SCHEMA_VERSION  # noqa: F841 — document intent
    return capture, config.path


def check(emit_dir: Path, capture: Path, python_store: Path) -> int:
    """Replay capture qua CẢ HAI engine rồi diff DB-state (phase-B2 gate).

    So sánh công bằng là Rust-replay vs Python-replay (cùng JSONL, cùng ràng
    buộc strip `operation` vì manifest staging chưa ported sang Rust — xem
    phase-B2). Store Python live (ghi lúc drive, có manifest staging đầy đủ)
    được giữ lại làm tham chiếu, không đưa vào diff này.
    """

    sys.path.insert(0, str(REPO / "scripts" / "rust_parity"))
    from replay_journal_python import replay_capture

    cargo = shutil.which("cargo") or "cargo"
    subprocess.run(
        [
            cargo, "build", "--release", "-p", "cortex-graph-driver",
            "--bin", "replay_journal", "--manifest-path",
            str(REPO / "rust" / "Cargo.toml"),
        ],
        check=True,
    )
    rust_store = emit_dir / "rust_store.sqlite3"
    subprocess.run(
        [
            str(REPO / "rust" / "target" / "release" / "replay_journal"),
            "--input", str(capture),
            "--output", str(rust_store),
        ],
        check=True,
    )
    python_replay_store = emit_dir / "python_store_replay.sqlite3"
    stats = replay_capture(capture, python_replay_store)
    print(f"python replay: {stats['ops']} ops → {python_replay_store}")
    diff = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "rust_parity" / "diff_journal_stores.py"),
            str(python_replay_store),
            str(rust_store),
            "--strict-time",
        ],
    )
    _ = python_store  # live store chỉ là tham chiếu
    return diff.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-dir", required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="replay Rust + diff ngay sau khi drive (exit 1 khi lệch)",
    )
    arguments = parser.parse_args()
    emit_dir = Path(arguments.emit_dir).resolve()
    emit_dir.mkdir(parents=True, exist_ok=True)
    capture, python_store = drive(emit_dir)
    print(f"capture: {capture}")
    print(f"python store: {python_store}")
    if arguments.check:
        return check(emit_dir, capture, python_store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
