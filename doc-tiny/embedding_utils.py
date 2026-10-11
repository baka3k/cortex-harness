import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# The doc-side default embedding model. Keep this twin in sync with
# code-tiny's ``tools/common/model_defaults.DEFAULT_CODE_EMBEDDING_MODEL``
# (doc-tiny is a flat tree and cannot import from code-tiny).
DEFAULT_DOC_EMBEDDING_MODEL = "BAAI/bge-m3"

# Query-encode kwargs for the unified embedding model. Keep this marker in
# sync with code-tiny's ``tools/common/embed_runtime.QWEN3_EMBEDDING_MARKER``
# (doc-tiny is a flat tree and cannot import from code-tiny).
QWEN3_EMBEDDING_MARKER = "qwen3-embedding"

# Hybrid retrieval (learned-sparse BGE-M3 lexical weights) twins. Keep in
# sync with code-tiny's ``embed_runtime`` constants of the same names.
BGE_M3_EMBEDDING_MARKER = "bge-m3"
HYBRID_SPARSE_HEAD_FILE = "sparse_linear.pt"
HYBRID_SPARSE_VECTOR_NAME = "sparse"


def hybrid_enabled(model_name: str | None = None) -> bool:
    """Whether hybrid (dense+sparse) applies for ``model_name``.

    Same decision as code-tiny's ``embed_runtime.hybrid_enabled``: the
    ``EMBED_HYBRID`` env flag must be on AND the model must be bge-m3 —
    another family is a silent no-op (code side warns; this twin stays
    dependency-light and does not).
    """
    flag = os.getenv("EMBED_HYBRID", "")
    if str(flag).strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if model_name is None:
        return True
    return BGE_M3_EMBEDDING_MARKER in str(model_name).lower()


def sparse_lexical_weights_to_vector(
    lexical_weights: Dict[str, float] | Dict[int, float],
) -> Tuple[List[int], List[float]]:
    """Convert ``{token_id: weight}`` (FlagEmbedding shape) to Qdrant's
    ``(indices, values)`` SparseVector pair — positive weights only, sorted
    by token id. Mirrors code-tiny's encode post-process contract.
    """
    best: dict[int, float] = {}
    for token_id, weight in (lexical_weights or {}).items():
        value = float(weight)
        if value <= 0:
            continue
        key = int(token_id)
        if value > best.get(key, 0.0):
            best[key] = value
    indices = sorted(best)
    return indices, [best[index] for index in indices]


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
