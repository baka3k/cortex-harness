import os
import sys
from pathlib import Path
from typing import Dict, Tuple

# The doc-side default embedding model. Keep this twin in sync with
# code-tiny's ``tools/common/model_defaults.DEFAULT_CODE_EMBEDDING_MODEL``
# (doc-tiny is a flat tree and cannot import from code-tiny).
DEFAULT_DOC_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"

# Query-encode kwargs for the unified embedding model. Keep this marker in
# sync with code-tiny's ``tools/common/embed_runtime.QWEN3_EMBEDDING_MARKER``
# (doc-tiny is a flat tree and cannot import from code-tiny).
QWEN3_EMBEDDING_MARKER = "qwen3-embedding"


def st_query_encode_kwargs(model_name: str | None) -> Dict[str, object]:
    """SentenceTransformer encode kwargs for the *query* side.

    Qwen3-Embedding requires its ``query`` prompt (the instruction text
    lives in the model's own ST config) and normalized vectors; other
    models keep the bare encode. Only ever pass the result to queries —
    documents/paragraphs are embedded bare.
    """
    if QWEN3_EMBEDDING_MARKER in str(model_name or "").lower():
        return {"prompt_name": "query", "normalize_embeddings": True}
    return {}


def _offline_enabled() -> bool:
    offline = os.getenv("HF_HUB_OFFLINE") or os.getenv("TRANSFORMERS_OFFLINE")
    if offline and offline.strip().lower() not in {"0", "false", "no"}:
        return True
    local_only = os.getenv("EMBEDDING_LOCAL_ONLY")
    if local_only and local_only.strip().lower() not in {"0", "false", "no"}:
        return True
    return False


def resolve_embedding_model(model: str | None, default: str) -> Tuple[str, bool]:
    if model:
        selected = model
    else:
        # DOC_EMBEDDING_MODEL is what `dev init` persists for the doc
        # pipeline (dev.py:766); EMBEDDING_MODEL stays the legacy override.
        selected = (
            os.getenv("EMBEDDING_MODEL_PATH")
            or os.getenv("EMBEDDING_MODEL")
            or os.getenv("DOC_EMBEDDING_MODEL")
            or default
        )
    local_files_only = Path(selected).exists()
    if local_files_only or _offline_enabled():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    return selected, local_files_only


def resolve_embedding_device(device: str | None) -> str:
    if device:
        return device
    env_device = os.getenv("EMBEDDING_DEVICE")
    if env_device:
        return env_device
    # Auto-detect like the code side: MPS on macOS, CUDA elsewhere, CPU as
    # the last resort. Qwen3-0.6B on CPU is painfully slow for queries.
    try:
        import torch

        if sys.platform == "darwin":
            mps = getattr(torch.backends, "mps", None)
            if mps is not None and mps.is_available():
                return "mps"
            return "cpu"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # torch missing/broken → legacy CPU default
        pass
    return "cpu"
