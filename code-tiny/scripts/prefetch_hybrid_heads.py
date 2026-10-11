#!/usr/bin/env python3
"""Prefetch the BGE-M3 sparse head(s) a hybrid-retrieval setup needs.

Phase 1 needs exactly one artifact: ``sparse_linear.pt`` (~3.5 KB) next to
the cached model snapshot — the learned-lexical head that turns the encoder's
raw token states into sparse weights. The ColBERT head is intentionally NOT
downloaded (multi-vector is out of scope for phase 1).

The download goes through the repo's audited HF session: every request is
printed with purpose + destination, matching the local-first network
contract. Without it, a snapshot-complete cache would never reach the
network and the first hybrid encode would fail fast with instructions to
run this script.

Usage:
  python code-tiny/scripts/prefetch_hybrid_heads.py                # default model
  python code-tiny/scripts/prefetch_hybrid_heads.py --model BAAI/bge-m3
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

CODE_TINY = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_TINY.parent
for entry in (CODE_TINY, REPO_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))


def _configured_models() -> list[str]:
    """Models whose snapshots are likely in use (env first, default last)."""
    from tools.common.model_defaults import DEFAULT_CODE_EMBEDDING_MODEL

    candidates: list[str] = []
    for var in (
        "CODE_EMBEDDING_MODEL_PATH",
        "CODE_EMBEDDING_MODEL",
        "EMBEDDING_MODEL_PATH",
        "EMBEDDING_MODEL",
        "DOC_EMBEDDING_MODEL",
    ):
        value = str(os.environ.get(var, "")).strip()
        if value and value not in candidates and os.path.isdir(value) is False:
            candidates.append(value)
    if DEFAULT_CODE_EMBEDDING_MODEL not in candidates:
        candidates.append(DEFAULT_CODE_EMBEDDING_MODEL)
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        action="append",
        default=None,
        help="Model repo id to prefetch the head for (repeatable; default: "
             "resolved from env + the unified default).",
    )
    args = parser.parse_args()
    models = args.model or _configured_models()

    # Audited network session: every hub request prints purpose + destination.
    from tools.common import embedding_runtime

    embedding_runtime._enable_audit()
    from huggingface_hub import hf_hub_download

    from tools.common.embed_runtime import (
        HYBRID_SPARSE_HEAD_FILE,
        hybrid_head_available,
    )

    failures = 0
    for model in models:
        if os.path.isdir(model):
            print(f"[skip] {model}: local directory — copy {HYBRID_SPARSE_HEAD_FILE} "
                  f"into it manually if the head is missing.")
            continue
        if hybrid_head_available(model):
            print(f"[ok] {model}: {HYBRID_SPARSE_HEAD_FILE} already cached.")
            continue
        print(f"[fetch] {model}: downloading {HYBRID_SPARSE_HEAD_FILE}...")
        try:
            path = hf_hub_download(repo_id=model, filename=HYBRID_SPARSE_HEAD_FILE)
            print(f"[ok] {model}: cached at {path}")
        except Exception as exc:  # noqa: BLE001 - report every model's outcome
            failures += 1
            print(f"[fail] {model}: {exc}")
    if hybrid_enabled_check(models):
        print("[note] EMBED_HYBRID is on: ingest processes resolving the model "
              "through tools.common.embedding_runtime will fetch the head "
              "through the same audited path on first load if it was missed here.")
    return 1 if failures else 0


def hybrid_enabled_check(models: list[str]) -> bool:
    from tools.common.embed_runtime import hybrid_enabled

    return any(hybrid_enabled(model) for model in models)


if __name__ == "__main__":
    sys.exit(main())
