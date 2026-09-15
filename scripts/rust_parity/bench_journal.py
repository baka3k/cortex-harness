#!/usr/bin/env python3
"""Benchmark journal write: Rust replay vs Python replay trên cùng op-stream.

Sinh 3 size op-stream (100 / 1k / 10k ops) ở capture format Track B — mỗi
chu kỳ 4 op đúng như producer loop thật: ``create_artifact`` →
``enqueue_batch`` → ``claim_batch`` → ``ack_batch`` (job_id/artifact sha tính
bằng identity function thật của code-tiny nên stream replay được 100%). Sau
``open_run``/``close``, nếu size không chia hết còn pad bằng prefix chu kỳ.

Đo mỗi size REPEATS lần (lấy median):
- Rust: bin `replay_journal` (cortex-graph-driver, release) với ``--bench``;
- Python: `replay_journal_python.py` với ``--bench``.

In bảng markdown ra stdout (nguồn cho reports/bench-journal.md).

Chạy từ repo root:
    .venv/bin/python scripts/rust_parity/bench_journal.py [--emit-dir /tmp/journal-bench]
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
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))

from tools.graph.journal.identity import (  # noqa: E402
    canonical_json,
    deterministic_job_id,
    run_fingerprint,
    run_id,
)
from tools.graph.journal.models import (  # noqa: E402
    ArtifactRef,
    JOURNAL_SCHEMA_VERSION,
    OperationPhase,
    RunMetadata,
)
REPLAY_BIN = REPO / "rust" / "target" / "release" / "replay_journal"
PYTHON_REPLAY = REPO / "scripts" / "rust_parity" / "replay_journal_python.py"
SIZES = (100, 1_000, 10_000)
REPEATS = 3


def _metadata() -> RunMetadata:
    return RunMetadata(
        project_id="bench",
        scope_id="bench",
        source_revision="rev-1",
        source_snapshot="snap-1",
        physical_target="local",
        generation="gen-1",
        parser="python",
        parser_version="1",
        schema_fingerprint="sfp-bench",
        query_shape_version="language-writer-v1",
    )


def _artifact_ref(run: str, rows: list[dict]) -> ArtifactRef:
    hasher_total = bytearray()
    for row in rows:
        hasher_total += canonical_json(row) + b"\n"
    import hashlib

    digest = hashlib.sha256(bytes(hasher_total)).hexdigest()
    return ArtifactRef(digest, f"{run}/{digest}.jsonl", len(hasher_total), len(rows))


def _operation_payload(label: str) -> dict:
    """Operation metadata thật (shape từ capture executor-driven) để bench
    đo cả chi phí manifest staging — trước đây operation rỗng nên staging
    không chạy (đã che mất phần CPU-bound, xem re-evaluation phase-B3)."""
    if label == "relations:HAS_FILE":
        node_label = None
        reconciliation = "typed_relationship"
        operation_key = "graph-write/v1/relationships/relations:HAS_FILE"
    elif label == "calls":
        node_label = None
        reconciliation = "call_edge"
        operation_key = "graph-write/v1/calls/calls"
    else:
        node_label = "File" if label == "files" else "Function"
        reconciliation = "node_identity"
        operation_key = f"graph-write/v1/nodes/{label}"
    return {
        "label": label,
        "phase": "nodes" if node_label else "calls",
        "version": 1,
        "idempotent": True,
        "operation_key": operation_key,
        "reconciliation": reconciliation,
        "node_label": node_label,
        "identity_property": "id",
        "row_identity_property": "id",
        "row_properties_property": None,
        "mutation_kind": "merge",
        "query_fingerprint": None,
    }


def _batch_ops(run: str, fingerprint: str, sequence: int, cycle: int) -> list[dict]:
    """1 chu kỳ producer: create_artifact → enqueue → claim → ack."""

    # Chỉ node batches (files/functions) — relationship/calls staging đòi
    # endpoint identity thật giữa các node đã stage; row tổng hợp không có
    # → staging rejected đúng contract (admission gate). Node path là dominant
    # của ingest thật nên bench đại diện được.
    label = ("files", "functions")[cycle % 2]
    rows = [
        {
            "id": f"row-{cycle}-{ordinal}",
            "label": label,
            "payload": f"payload-{cycle}-{ordinal}" * 3,
            "project_id_normalized": "bench",
        }
        for ordinal in range(4)
    ]
    artifact = _artifact_ref(run, rows)
    spec = {
        "phase": OperationPhase.NODES if label in ("files", "functions") else OperationPhase.CALLS,
        "operation_key": f"graph-write/v1/nodes/{label}",
        "sequence": sequence,
        "artifact": dict(artifact.__dict__),
        "expected_count": len(rows),
        "required_barriers": [],
        "produced_barriers": [],
        "max_attempts": 5,
        "operation": _operation_payload(label),
    }
    job_id = deterministic_job_id(
        run_fingerprint_value=fingerprint,
        phase=spec["phase"],
        operation_key=spec["operation_key"],
        sequence=sequence,
        payload_sha256=artifact.sha256,
    )
    def op(name: str, args: dict) -> dict:
        return {"op": name, "args": args, "result": None}

    return [
        op("create_artifact", {"run_id": run, "rows": rows}),
        op("enqueue_batch", {"run_id": run, "spec": spec}),
        op("claim_batch", {"run_id": run, "lease_seconds": 60}),
        op("ack_batch", {"job_id": job_id, "elapsed_ms": 1}),
    ]


def generate_stream(target_ops: int, path: Path) -> None:
    metadata = _metadata()
    fingerprint = run_fingerprint(metadata)
    run = run_id(metadata)
    ops: list[dict] = [
        {"op": "open_run", "args": {"metadata": metadata.to_dict()}, "result": None}
    ]
    sequence = 0
    cycle = 0
    # open_run + close = 2 op; phần còn lại là chu kỳ 4-op (pad bằng prefix).
    budget = target_ops - 2
    while budget >= 4:
        ops.extend(_batch_ops(run, fingerprint, sequence, cycle))
        sequence += 1
        cycle += 1
        budget -= 4
    if budget:
        ops.extend(_batch_ops(run, fingerprint, sequence, cycle)[:budget])
    ops.append({"op": "close", "args": {}, "result": None})
    lines = [json.dumps(
        {
            "_header": True,
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "journal_config": {"mode": "required", "path": "bench.sqlite3",
                                "metadata": metadata.to_dict(), "limits": {}},
            "now_epoch": 1757764800.0,
        },
        ensure_ascii=False, separators=(",", ":"),
    )]
    for seq, op_value in enumerate(ops):
        op_value["_seq"] = seq
        lines.append(json.dumps(op_value, ensure_ascii=False, separators=(",", ":")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_bench_line(line: str) -> dict:
    payload = line.strip().removeprefix("bench: ")
    return dict(part.split("=", 1) for part in payload.split())


def run_engine(binary: list[str], stream: Path, store: Path) -> dict:
    completed = subprocess.run(
        [*binary, "--input", str(stream), "--output", str(store), "--bench"],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in completed.stdout.splitlines():
        if line.startswith("bench:"):
            return parse_bench_line(line)
    raise RuntimeError(f"không thấy bench line: {completed.stderr[-400:]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-dir", default=None)
    arguments = parser.parse_args()
    context = (
        tempfile.TemporaryDirectory()
        if arguments.emit_dir is None
        else _FixedDir(arguments.emit_dir)
    )
    with context as tmp:
        emit_dir = Path(tmp)
        emit_dir.mkdir(parents=True, exist_ok=True)
        if not REPLAY_BIN.is_file():
            subprocess.run(
                ["cargo", "build", "--release", "-p", "cortex-graph-driver",
                 "--bin", "replay_journal", "--manifest-path",
                 str(REPO / "rust" / "Cargo.toml")],
                check=True,
            )
        rows = []
        for size in SIZES:
            stream = emit_dir / f"stream_{size}.jsonl"
            generate_stream(size, stream)
            samples = {"rust": [], "python": []}
            for repeat in range(REPEATS):
                samples["rust"].append(run_engine(
                    [str(REPLAY_BIN)], stream, emit_dir / f"rust_{size}.sqlite3",
                ))
                samples["python"].append(run_engine(
                    [sys.executable, str(PYTHON_REPLAY)], stream,
                    emit_dir / f"python_{size}.sqlite3",
                ))
            row = {"size": size, "bytes": int(samples["rust"][0]["input_bytes"])}
            for engine in ("rust", "python"):
                elapsed = statistics.median(
                    float(sample["elapsed_s"]) for sample in samples[engine]
                )
                ops = int(samples[engine][0]["ops"])
                row[engine] = {
                    "elapsed_s": elapsed,
                    "ops_per_s": ops / elapsed,
                    "mib_per_s": (row["bytes"] / (1024 * 1024)) / elapsed,
                }
            rows.append(row)

        print("| ops | input bytes | Rust ops/s | Python ops/s | Rust MB/s | Python MB/s | Rust faster |")
        print("|---:|---:|---:|---:|---:|---:|---:|")
        for row in rows:
            print(
                f"| {row['size']} | {row['bytes']} "
                f"| {row['rust']['ops_per_s']:.0f} | {row['python']['ops_per_s']:.0f} "
                f"| {row['rust']['mib_per_s']:.2f} | {row['python']['mib_per_s']:.2f} "
                f"| {row['rust']['ops_per_s'] / row['python']['ops_per_s']:.1f}x |"
            )
    return 0


class _FixedDir:
    def __init__(self, path: str) -> None:
        self.path = path

    def __enter__(self) -> str:
        return self.path

    def __exit__(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
