#!/usr/bin/env python3
"""Fetch the official bge-m3 ONNX graph into `.cache/embed/BAAI--bge-m3/`.

Unlike jina-embeddings-v3 (which must be self-exported — see
`plans/260914-1706-onnx-embedding-spike/findings.md` C3), BAAI/bge-m3 publishes a
clean `onnx/model.onnx`: inputs are exactly `input_ids` + `attention_mask` and it
emits both `token_embeddings` and `sentence_embedding`, so no export tooling is
needed here.

The files are copied out of the HF cache (not symlinked) so the graph stays pinned
to the recorded snapshot revision even if the cache is pruned.

Usage:
    .venv/bin/python scripts/rust_parity/fetch_bge_onnx.py
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
import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODEL = "BAAI/bge-m3"
# G7 (plan 260915-analyzer-layer-rust-cutover phase-06): pin the HF snapshot
# the on-disk artifact was copied from — the same revision recorded in
# rust/crates/cortex-embed/src/model.rs `model_pin`. snapshot_download takes no
# `revision` argument otherwise, so a cache refresh could copy different bytes
# under the same name. trust_remote_code is NOT used here: bge-m3 ships an
# official onnx/ subtree, nothing executes.
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
PATTERNS = ["onnx/model.onnx", "onnx/model.onnx_data"]
OUT = REPO / ".cache" / "embed" / "BAAI--bge-m3"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="redownload even if present")
    args = parser.parse_args()

    graph = OUT / "model.onnx"
    if graph.is_file() and not args.force:
        print(f"[bge] already fetched: {graph.relative_to(REPO)}")
        return 0

    from huggingface_hub import snapshot_download

    snapshot = snapshot_download(MODEL, revision=MODEL_REVISION, allow_patterns=PATTERNS)
    source = Path(snapshot)
    if source.name != MODEL_REVISION:
        raise SystemExit(
            f"[bge] resolved snapshot {source.name} != pinned revision {MODEL_REVISION}"
        )
    OUT.mkdir(parents=True, exist_ok=True)
    files = {}
    for pattern in PATTERNS:
        src = source / pattern
        if not src.is_file():
            raise SystemExit(f"[bge] missing {pattern} in snapshot {snapshot}")
        dest = OUT / Path(pattern).name
        shutil.copy2(src, dest)
        files[dest.name] = {"bytes": dest.stat().st_size, "sha256": sha256(dest)}

    meta = {
        "model": MODEL,
        "revision": MODEL_REVISION,
        "snapshot": source.name,
        "origin": "official Hugging Face onnx/ subtree (no local export)",
        "pooling": "cls",
        "normalize": True,
        "max_token_length": 8192,
        "dimension": 1024,
        "files": files,
    }
    (OUT / "metadata.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    total = sum(item["bytes"] for item in files.values()) / 1e9
    print(f"[bge] fetched {OUT.relative_to(REPO)} ({total:.2f} GB), snapshot {source.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
