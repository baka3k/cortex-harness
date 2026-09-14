#!/usr/bin/env python
"""Persistent bge-m3 embedding sidecar for the Rust mind server (phase 13).

Plan B of the phase-13 decision record: query embedding stays on torch
(sentence-transformers, doc-tiny's embedder) behind a subprocess boundary;
the ONNX `ort` port is a later spike and is intentionally NOT implemented.

Protocol: newline-delimited JSON over stdin/stdout.
    request : {"texts": ["..."], ...}
    response: {"dimension": 1024, "vectors": [[...], ...]}

Model/device resolution mirrors `doc-tiny/embedding_utils.py` +
`mcp_graph_rag.get_embedder` (same env vars: EMBEDDING_MODEL_PATH /
EMBEDDING_MODEL / EMBEDDING_DEVICE, default model BAAI/bge-m3, cpu) so the
vectors are bit-identical to the live Python server's in-process encoder.

Startup banner is written to stderr, never stdout (stdout is protocol-only).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_MODEL = "BAAI/bge-m3"


def resolve_embedding_model(model: str | None, default: str) -> tuple[str, bool]:
    """doc-tiny/embedding_utils.resolve_embedding_model (verbatim logic)."""
    if model:
        selected = model
    else:
        selected = os.getenv("EMBEDDING_MODEL_PATH") or os.getenv("EMBEDDING_MODEL") or default
    local_files_only = Path(selected).exists()
    offline = os.getenv("HF_HUB_OFFLINE") or os.getenv("TRANSFORMERS_OFFLINE")
    if (
        local_files_only
        or (offline and offline.strip().lower() not in {"0", "false", "no"})
        or (os.getenv("EMBEDDING_LOCAL_ONLY", "").strip().lower() not in {"", "0", "false", "no"})
    ):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    return selected, local_files_only


def resolve_embedding_device(device: str | None) -> str:
    if device:
        return device
    env_device = os.getenv("EMBEDDING_DEVICE")
    if env_device:
        return env_device
    return "cpu"


def main() -> int:
    from sentence_transformers import SentenceTransformer

    state: dict = {"model": None, "dimension": 0, "selected": "", "device": ""}

    def ensure_model(model_name: str | None, device: str | None):
        if state["model"] is not None:
            return state["model"]
        selected, local_files_only = resolve_embedding_model(model_name, DEFAULT_MODEL)
        resolved_device = resolve_embedding_device(device)
        instance = SentenceTransformer(
            selected, local_files_only=local_files_only, device=resolved_device
        )
        state["model"] = instance
        state["dimension"] = int(instance.get_sentence_embedding_dimension())
        state["selected"] = selected
        state["device"] = resolved_device
        print(
            f"[embed-worker] model={selected} device={resolved_device} "
            f"dim={state['dimension']}",
            file=sys.stderr,
            flush=True,
        )
        return instance

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            instance = ensure_model(req.get("model"), req.get("device"))
            texts = req.get("texts") or []
            vectors = instance.encode(texts).tolist() if texts else []
            payload = {"dimension": state["dimension"], "vectors": vectors}
            sys.stdout.write(json.dumps(payload) + "\n")
            sys.stdout.flush()
        except Exception as exc:  # noqa: BLE001 - protocol-level error reply
            sys.stdout.write(
                json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n"
            )
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
