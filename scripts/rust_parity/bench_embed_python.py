#!/usr/bin/env python3
"""Python-side throughput reference for the phase-01 benchmark gate.

Runs the production ingest embedder over the same corpus the Rust golden fixture
uses, with the CPU thread count pinned to match `SessionConfig`, so the two
numbers in `plans/260914-1706-onnx-embedding-spike/reports/phase01-jina-parity.md`
are comparable. Model load time is reported separately and excluded from
throughput, which is the same convention the Rust probe uses (warm-up first).

Usage:
    .venv/bin/python scripts/rust_parity/bench_embed_python.py
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "rust" / "crates" / "cortex-embed" / "tests" / "fixtures" / "jina_golden.json"
MODEL = "jinaai/jina-embeddings-v3"
THREADS = int(os.environ.get("BENCH_THREADS", "4"))


def main() -> int:
    sys.path.insert(0, str(REPO / "code-tiny"))
    sys.path.insert(0, str(REPO / "code-tiny" / "tools" / "common"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    import torch

    torch.set_num_threads(THREADS)
    import embed_runtime as er

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    texts = [case["text"] for case in payload["cases"] if case["lane"] == "ingest"]
    print(f"[bench-python] texts={len(texts)} threads={THREADS} device=cpu")

    started = time.perf_counter()
    model = er.get_sentence_transformer(MODEL, device="cpu")
    load_seconds = time.perf_counter() - started
    print(f"[bench-python] model load: {load_seconds:.2f}s")

    for batch in (8, 32, 128):
        model.encode(texts[:2], batch_size=batch, convert_to_numpy=True,
                     normalize_embeddings=True)  # warm-up
        started = time.perf_counter()
        vectors = model.encode(
            texts,
            batch_size=batch,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        seconds = time.perf_counter() - started
        print(
            f"[bench-python] torch batch={batch:3} texts={len(vectors)} "
            f"time={seconds:.2f}s throughput={len(vectors) / seconds:.1f} texts/s"
        )

    # Query-side latency (single text, warm) — the number the P95 gate cares about.
    latencies = []
    for text in texts[:40]:
        started = time.perf_counter()
        model.encode([text], convert_to_numpy=True, normalize_embeddings=True)
        latencies.append(time.perf_counter() - started)
    latencies.sort()
    print(
        f"[bench-python] single-text encode p50={statistics.median(latencies) * 1000:.1f}ms "
        f"p95={latencies[int(len(latencies) * 0.95) - 1] * 1000:.1f}ms n={len(latencies)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
