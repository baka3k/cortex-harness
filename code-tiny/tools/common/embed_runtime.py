"""Process-wide embedding runtime shared by every MCP backend.

One process loads each ``(model, device)`` pair exactly once and reuses one
query-vector LRU cache, no matter how many backend modules (or importlib
aliases of them) are running. Backends keep their module-level helper names
as thin delegates because tests patch those names; the state lives here.

Distinct from :mod:`tools.common.embedding_runtime` — that module owns the
Hugging Face network-audit / snapshot-cache resolution and must never be
merged with this one.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("tools.common.embed_runtime")

try:  # pragma: no cover - exercised via the ImportError branch in tests
    import torch
except ImportError:  # pragma: no cover - torch-less environments
    torch = None  # type: ignore[assignment]


_QUERY_VECTOR_CACHE: "OrderedDict[Tuple[str, str], List[float]]" = OrderedDict()
_QUERY_SPARSE_CACHE: "OrderedDict[Tuple[str, str], Tuple[List[int], List[float]]]" = OrderedDict()
_EMBEDDER_CACHE: Dict[Tuple[str, str], Tuple[Any, Any, Any]] = {}
_SENTENCE_TRANSFORMER_CACHE: Dict[Tuple[str, Optional[str]], Any] = {}
_SPARSE_HEAD_CACHE: Dict[Tuple[str, Optional[str]], Any] = {}
_EMBEDDER_LOCK = threading.Lock()
_ST_LOCK = threading.Lock()

DEFAULT_QUERY_CACHE_SIZE = 512

# Model-family marker for the unified embedding policy. ``prompt_name`` is a
# SentenceTransformer *config key* — the actual instruction text lives in the
# model's own ST config and is never duplicated here.
QWEN3_EMBEDDING_MARKER = "qwen3-embedding"
QWEN3_QUERY_PROMPT_NAME = "query"
BGE_M3_EMBEDDING_MARKER = "bge-m3"

# Hybrid retrieval (dense + learned-sparse BGE-M3 lexical weights). The
# sparse head is a separate 3.5 KB Linear next to the model weights — it is
# never bundled with the ST snapshot cache and resolves on its own path.
HYBRID_ENV_FLAG = "EMBED_HYBRID"
HYBRID_SPARSE_HEAD_FILE = "sparse_linear.pt"
HYBRID_SPARSE_VECTOR_NAME = "sparse"
# FlagEmbedding encodes both queries and passages at 512 tokens for bge-m3.
_SPARSE_MAX_LENGTH = 512
_XLMR_SPECIAL_TOKEN_NAMES = ("cls_token_id", "eos_token_id", "pad_token_id", "unk_token_id")


def _env_flag_enabled(raw: Optional[str]) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def hybrid_enabled(model_name: Optional[str] = None) -> bool:
    """Whether sparse encode participates for ``model_name``.

    Requires the ``EMBED_HYBRID`` env flag **and** a bge-m3-family model;
    any other family makes hybrid a no-op (one warning) so an operator who
    flips the flag under Qwen3 gets silence-free degradation, not sparse
    vectors from a model that cannot produce them.
    """
    if not _env_flag_enabled(os.environ.get(HYBRID_ENV_FLAG)):
        return False
    if model_name is None:
        return True
    if BGE_M3_EMBEDDING_MARKER in str(model_name).lower():
        return True
    _warn_hybrid_family_unsupported(model_name)
    return False


_HYBRID_FAMILY_WARNED: set = set()


def _warn_hybrid_family_unsupported(model_name: str) -> None:
    if model_name in _HYBRID_FAMILY_WARNED:
        return
    _HYBRID_FAMILY_WARNED.add(model_name)
    logger.warning(
        "[embed] EMBED_HYBRID=1 has no effect for %r: learned-sparse encode is "
        "bge-m3-only (the sparse_linear.pt head belongs to that family).",
        model_name,
    )


def model_policy(model_name: str) -> Dict[str, Any]:
    """Single model-family policy: how to load and how to encode queries.

    Returns ``{trust_remote_code, backend, query_prompt, normalize}``:

    * ``qwen3-embedding`` (anywhere in the name, local snapshots included via
      their basename) → SentenceTransformer backend, last-token pooling comes
      from the model config, query side uses the ``query`` prompt and
      normalized vectors.
    * ``bge-m3`` → SentenceTransformer backend with the model's own pooling
      config, bare query encode (no prompt) normalized to match the ingest
      contract.
    * ``jina`` (or an explicit ``JINA_MODEL_PATH`` match) → legacy AutoModel
      backend with ``trust_remote_code=True``; encoding behavior unchanged.
    * anything else → SentenceTransformer default config, no query prompt,
      no normalization change (query-side dispatch keeps the legacy
      AutoModel path so unrecognized names behave exactly as before).
    """
    raw_name = str(model_name or "").strip()
    name = raw_name.lower()
    jina_path = os.environ.get("JINA_MODEL_PATH")
    if "jina" in name or (
        jina_path and os.path.normpath(jina_path) == os.path.normpath(raw_name)
    ):
        return {
            "trust_remote_code": True,
            "backend": "automodel",
            "query_prompt": None,
            "normalize": False,
        }
    if QWEN3_EMBEDDING_MARKER in name:
        return {
            "trust_remote_code": False,
            "backend": "st",
            "query_prompt": QWEN3_QUERY_PROMPT_NAME,
            "normalize": True,
        }
    if BGE_M3_EMBEDDING_MARKER in name:
        return {
            "trust_remote_code": False,
            "backend": "st",
            "query_prompt": None,
            "normalize": True,
        }
    return {
        "trust_remote_code": False,
        "backend": "st",
        "query_prompt": None,
        "normalize": False,
    }


def effective_model_identity(model_name: str) -> str:
    """The code-side model identity a query embedder resolves to.

    Every MCP query chain checks ``CODE_EMBEDDING_MODEL_PATH`` first, so
    ingest-side marker stamping must apply the same override — otherwise a
    local-path setup produces permanent phantom mismatch warnings, and a
    PATH swap re-embeds under a stale stamp (silent mix).
    """
    path = os.environ.get("CODE_EMBEDDING_MODEL_PATH")
    return str(path) if path else str(model_name)


def _policy_uses_st_query(policy: Dict[str, Any]) -> bool:
    """Whether a policy selects the SentenceTransformer query-encode path.

    Only families that declare a query prompt (Qwen3) or query-side
    normalization (BGE-M3) are rerouted; unrecognized names keep the legacy
    AutoModel query path so no existing behavior changes silently.
    """
    return bool(
        policy.get("backend") == "st"
        and (policy.get("query_prompt") or policy.get("normalize"))
    )


_MIN_TRANSFORMERS_FOR_QWEN3 = (4, 51)


def _check_qwen3_transformers_floor(model_name: str) -> None:
    """Fail fast with an actionable message on an old transformers install.

    Qwen3-Embedding needs the Qwen3 architecture (transformers >= 4.51);
    an older install otherwise dies mid-load with a confusing architecture
    error instead of this one. Non-Qwen3 names (bge-m3, local snapshots of
    other families routed through the shared ST query path) pass through —
    they predate the Qwen3 architecture and run on any supported version.
    """
    if QWEN3_EMBEDDING_MARKER not in str(model_name or "").lower():
        return
    try:
        import transformers

        version = tuple(
            int(part)
            for part in str(transformers.__version__).split(".")[:2]
            if part.isdigit()
        )
    except Exception:  # noqa: BLE001 - version parse failure → let the load try
        return
    if len(version) < 2:
        return
    if version < _MIN_TRANSFORMERS_FOR_QWEN3:
        raise RuntimeError(
            f"Model {model_name!r} (Qwen3-Embedding) requires transformers >= "
            f"4.51.0; found {transformers.__version__}. Upgrade with: "
            f"pip install 'transformers>=4.51,<4.56' (see requirements.txt)."
        )

_CUDA_ERROR_MARKERS = (
    "no kernel image is available for execution on the device",
    "invalid device function",
    "no cuda kernels are available",
    "cuda error",
)
# MPS/Metal failures surface with varying messages ("MPS backend out of
# memory", "Metal ...", device-placeholder errors); a false positive only
# costs one safe CPU retry, so the match stays broad (red team #7).
_MPS_ERROR_MARKERS = ("mps", "metal")


def _is_embed_cpu_fallback_enabled() -> bool:
    raw = os.environ.get("EMBED_FALLBACK_TO_CPU", "1")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def is_cuda_runtime_error(exc: BaseException) -> bool:
    """Legacy predicate kept for callers that only care about CUDA errors."""
    message = str(exc).lower()
    return "cuda" in message and any(marker in message for marker in _CUDA_ERROR_MARKERS)


def _is_accelerator_runtime_error(exc: BaseException) -> bool:
    """Whether a RuntimeError looks like a CUDA *or* MPS/Metal execution failure."""
    message = str(exc).lower()
    if "cuda" in message and any(marker in message for marker in _CUDA_ERROR_MARKERS):
        return True
    return any(marker in message for marker in _MPS_ERROR_MARKERS)


def _should_trust_remote_code(model_name: str) -> bool:
    return bool(model_policy(model_name)["trust_remote_code"])


def _auto_detect_device() -> str:
    """Pick the best accelerator for this platform: macOS→MPS, else CUDA."""
    if sys.platform == "darwin":
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            return "mps"
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def resolve_device(device_name: Optional[str] = None) -> Any:
    """Resolve an embed device request to a ``torch.device``.

    ``None``/blank/``"auto"`` auto-detects (macOS: MPS when available;
    elsewhere: CUDA when available; otherwise CPU). Explicit ``cuda*``/
    ``mps`` requests fall back to CPU with a warning when the platform
    backend is unavailable. An explicit ``cpu`` is never silently upgraded
    to an accelerator.
    """
    if torch is None:
        return None
    explicit = device_name if device_name is not None else os.environ.get("EMBED_DEVICE")
    source = "auto"
    if device_name is not None:
        source = "param"
        raw_device = str(device_name).strip() or "auto"
    else:
        source = "env" if (explicit or "").strip() else "auto"
        raw_device = (explicit or "auto").strip() or "auto"
    if raw_device.lower() == "auto":
        resolved = _auto_detect_device()
        logger.info("embed device resolved: %s (source=auto)", resolved)
        return torch.device(resolved)
    normalized = raw_device.lower()
    if normalized.startswith("cuda") and not torch.cuda.is_available():
        logger.warning("[embed] CUDA requested but unavailable; falling back to CPU.")
        logger.info("embed device resolved: cpu (source=%s fallback)", source)
        return torch.device("cpu")
    if normalized.startswith("mps"):
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is None or not mps_backend.is_available():
            logger.warning("[embed] MPS requested but unavailable; falling back to CPU.")
            logger.info("embed device resolved: cpu (source=%s fallback)", source)
            return torch.device("cpu")
    logger.info("embed device resolved: %s (source=%s)", raw_device, source)
    return torch.device(raw_device)


def get_embedder(model_name: str, device_name: Optional[str] = None) -> Tuple[Any, Any, Any]:
    """Return ``(tokenizer, model, device)``, loading at most once per key.

    Model families whose policy selects the SentenceTransformer query path
    (Qwen3) load through :func:`get_sentence_transformer` and come back as
    ``(None, st_model, device)`` — startup preload then warms the exact
    object the query path uses instead of a duplicate AutoModel copy.
    """
    if torch is None:
        raise RuntimeError(
            "torch is not installed; the embedding runtime cannot load models."
        )
    device = resolve_device(device_name)
    policy = model_policy(model_name)
    if _policy_uses_st_query(policy):
        _check_qwen3_transformers_floor(model_name)
        model = get_sentence_transformer(
            model_name,
            device=str(device) if device is not None else None,
            trust_remote_code=policy["trust_remote_code"],
            local_files_only=_st_local_files_only(model_name),
        )
        return None, model, device
    cache_key = (model_name, str(device))
    with _EMBEDDER_LOCK:
        cached = _EMBEDDER_CACHE.get(cache_key)
        if cached is not None:
            return cached
        from transformers import AutoModel, AutoTokenizer

        trust_remote_code = policy["trust_remote_code"]
        # Complete cache → load fully offline; miss/partial → online resolve.
        local_only = _st_local_files_only(model_name)
        with _offline_hub_load_guard(local_only):
            tokenizer = AutoTokenizer.from_pretrained(
                model_name, trust_remote_code=trust_remote_code, local_files_only=local_only
            )
            model = AutoModel.from_pretrained(
                model_name, trust_remote_code=trust_remote_code, local_files_only=local_only
            )
        try:
            model.to(device)
        except RuntimeError as exc:
            if (
                str(device) != "cpu"
                and _is_accelerator_runtime_error(exc)
                and _is_embed_cpu_fallback_enabled()
            ):
                logger.warning("[embed] Accelerator model load failed (%s). Retrying on CPU.", exc)
                device = torch.device("cpu")
                model.to(device)
                cache_key = (model_name, str(device))
            else:
                raise
        model.eval()
        _EMBEDDER_CACHE[cache_key] = (tokenizer, model, device)
        return tokenizer, model, device


def mean_pool(last_hidden: Any, mask: Any) -> Any:
    mask = mask.unsqueeze(-1).type_as(last_hidden)
    summed = (last_hidden * mask).sum(dim=1)
    counts = mask.sum(dim=1).clamp(min=1)
    return summed / counts


def encode_texts(
    model: Any,
    texts: List[str],
    device: Any,
    policy: Optional[Dict[str, Any]] = None,
) -> Optional[List[List[float]]]:
    if not hasattr(model, "encode"):
        return None
    kwargs: Dict[str, Any] = {}
    if policy:
        if policy.get("query_prompt"):
            kwargs["prompt_name"] = policy["query_prompt"]
        if policy.get("normalize"):
            kwargs["normalize_embeddings"] = True
    try:
        if kwargs:
            encoded = model.encode(texts, device=str(device), **kwargs)
        else:
            encoded = model.encode(texts, device=str(device))
    except TypeError as exc:
        # Only a kwarg-support gap may retry bare; anything else is a real
        # error that must surface.
        if "unexpected keyword argument" not in str(exc).lower():
            raise
        encoded = model.encode(texts)
    if isinstance(encoded, torch.Tensor):
        return encoded.detach().cpu().tolist()
    if hasattr(encoded, "tolist"):
        return encoded.tolist()
    return [list(vec) for vec in encoded]


def embed_query_with_model(
    tokenizer: Any,
    model: Any,
    device: Any,
    text: str,
    policy: Optional[Dict[str, Any]] = None,
) -> List[float]:
    encoded = encode_texts(model, [text], device, policy=policy)
    if encoded is not None:
        return encoded[0]
    with torch.no_grad():
        encoded = tokenizer(
            [text],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        encoded = {key: value.to(device) for key, value in encoded.items()}
        outputs = model(**encoded)
        embedding = mean_pool(outputs.last_hidden_state, encoded["attention_mask"]).cpu().tolist()[0]
    return embedding


def _query_cache_maxsize() -> int:
    raw = str(os.environ.get("MCP_QUERY_EMBED_CACHE", str(DEFAULT_QUERY_CACHE_SIZE))).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_QUERY_CACHE_SIZE


def _query_cache_get(model_name: str, text: str) -> Optional[List[float]]:
    maxsize = _query_cache_maxsize()
    if not maxsize:
        return None
    key = (model_name, text)
    with _EMBEDDER_LOCK:
        cached = _QUERY_VECTOR_CACHE.get(key)
        if cached is not None:
            _QUERY_VECTOR_CACHE.move_to_end(key)
        return None if cached is None else list(cached)


def _query_cache_put(model_name: str, text: str, vector: List[float]) -> None:
    maxsize = _query_cache_maxsize()
    if not maxsize:
        return
    key = (model_name, text)
    with _EMBEDDER_LOCK:
        _QUERY_VECTOR_CACHE[key] = list(vector)
        _QUERY_VECTOR_CACHE.move_to_end(key)
        while len(_QUERY_VECTOR_CACHE) > maxsize:
            _QUERY_VECTOR_CACHE.popitem(last=False)


def _evict_st_cache(model_name: str, device: Optional[str] = None) -> None:
    """Drop the cached SentenceTransformer for ``(model, device)``.

    Evicting only the failing key keeps a working CPU copy cached when the
    accelerator is broken; a persistent accelerator failure then costs one
    failed attempt per process instead of two full reloads per query.
    """
    with _ST_LOCK:
        if device is None:
            keys = [key for key in _SENTENCE_TRANSFORMER_CACHE if key[0] == model_name]
        else:
            keys = [(model_name, str(device))]
        for key in keys:
            _SENTENCE_TRANSFORMER_CACHE.pop(key, None)


def _embed_query_st(
    text: str,
    model_name: str,
    device_name: Optional[str],
    policy: Dict[str, Any],
) -> List[float]:
    """Query encode via SentenceTransformer (model-config pooling families).

    Accelerator failures evict the SentenceTransformer cache and reload on
    CPU with the same prompt/normalize policy; the AutoModel mean-pool path
    is never entered for these models (Qwen3 last-token and BGE-M3 CLS
    pooling differ from mean-pooling).
    """
    _check_qwen3_transformers_floor(model_name)
    encode_kwargs: Dict[str, Any] = {}
    if policy.get("query_prompt"):
        encode_kwargs["prompt_name"] = policy["query_prompt"]
    if policy.get("normalize"):
        encode_kwargs["normalize_embeddings"] = True

    def _encode(model: Any) -> Any:
        encoded = model.encode([text], **encode_kwargs)
        if hasattr(encoded, "tolist"):
            encoded = encoded.tolist()
        return encoded[0]

    device = resolve_device(device_name)
    model = get_sentence_transformer(
        model_name,
        device=str(device) if device is not None else device_name,
        trust_remote_code=policy["trust_remote_code"],
        local_files_only=_st_local_files_only(model_name),
    )
    try:
        return list(_encode(model))
    except RuntimeError as exc:
        if _is_accelerator_runtime_error(exc) and _is_embed_cpu_fallback_enabled():
            logger.warning(
                "[embed] Accelerator inference failed (err=%s). Flushing the "
                "SentenceTransformer cache and retrying on CPU.",
                exc,
            )
            _evict_st_cache(model_name, device)
            model_cpu = get_sentence_transformer(
                model_name,
                device="cpu",
                trust_remote_code=policy["trust_remote_code"],
                local_files_only=_st_local_files_only(model_name),
            )
            return list(_encode(model_cpu))
        raise


def embed_query(text: str, model_name: str, device_name: Optional[str] = None) -> List[float]:
    """Embed one query string with a process-wide vector cache.

    Cache hits return a copy so callers can mutate the result without
    corrupting the cached entry. Inference failures that look like
    accelerator (CUDA/MPS/Metal) runtime errors evict the cache entry and
    retry once on CPU when ``EMBED_FALLBACK_TO_CPU`` is enabled — through
    the same backend that served the first attempt.
    """
    cached = _query_cache_get(model_name, text)
    if cached is not None:
        return cached
    policy = model_policy(model_name)
    if _policy_uses_st_query(policy):
        vector = _embed_query_st(text, model_name, device_name, policy)
    else:
        tokenizer, model, device = get_embedder(model_name, device_name)
        try:
            vector = embed_query_with_model(tokenizer, model, device, text, policy=policy)
        except RuntimeError as exc:
            # Some models (e.g. jinaai/jina-embeddings-v3 via trust_remote_code)
            # escalate to the accelerator inside their own encode() regardless of
            # the configured device, so the error can surface on any device.
            if _is_accelerator_runtime_error(exc) and _is_embed_cpu_fallback_enabled():
                logger.warning(
                    "[embed] Accelerator inference failed (configured device=%s, err=%s). "
                    "Flushing cache and retrying on CPU.",
                    device,
                    exc,
                )
                _EMBEDDER_CACHE.pop((model_name, str(device)), None)
                _EMBEDDER_CACHE.pop((model_name, "cpu"), None)
                tokenizer_cpu, model_cpu, device_cpu = get_embedder(model_name, device_name="cpu")
                vector = embed_query_with_model(tokenizer_cpu, model_cpu, device_cpu, text, policy=policy)
            else:
                raise
    _query_cache_put(model_name, text, vector)
    return list(vector)


def query_sparse_only(
    text: str,
    model_name: str,
    device_name: Optional[str] = None,
) -> Optional[Tuple[List[int], List[float]]]:
    """Sparse half of the query pair, or ``None`` when hybrid can't run.

    Companion to :func:`query_vector_pair` for callers whose dense encode
    must stay on their own (stub-able) seam — the backend ``_embed_query``
    call sites. Sparse caches by ``(model, text)`` exactly like the pair.

    Reader-side soft: flag off / non-bge family / a missing sparse head all
    degrade to ``None`` (dense-only search) with a warning — a query must
    never fail because hybrid retrieval is misconfigured. Ingest writers
    use :func:`encode_sparse` directly and fail closed.
    """
    if not hybrid_enabled(model_name):
        return None
    sparse = _query_sparse_cache_get(model_name, text)
    if sparse is None:
        try:
            sparse = encode_sparse(model_name, [text], device=resolve_device(device_name))[0]
        except RuntimeError as exc:
            logger.warning(
                "[embed] hybrid sparse encode unavailable (%s); query degrades "
                "to dense-only. Fix: python code-tiny/scripts/prefetch_hybrid_heads.py",
                exc,
            )
            return None
        _query_sparse_cache_put(model_name, text, sparse)
    return sparse


def query_vector_pair(
    text: str,
    model_name: str,
    device_name: Optional[str] = None,
) -> Tuple[List[float], Optional[Tuple[List[int], List[float]]]]:
    """``(dense, sparse | None)`` for query paths.

    Sparse rides along only when hybrid is active for the model family and
    the flag is on; every other configuration returns ``None`` so callers
    keep the byte-parallel dense-only behavior. Dense always goes through
    :func:`embed_query` (LRU + CPU fallback intact).
    """
    if hybrid_enabled(model_name):
        dense, sparse = embed_query_pair(text, model_name, device_name)
        return dense, sparse
    return embed_query(text, model_name, device_name), None


_TOKENIZER_FILE_CANDIDATES = (
    "tokenizer.json", "tokenizer.model", "spiece.model", "vocab.txt", "vocab.json",
)


def _hybrid_head_path(model_source: str) -> Optional[Path]:
    """Locate ``sparse_linear.pt`` next to the model — strictly offline.

    Local directories resolve directly; hub names resolve through
    ``snapshot_download(local_files_only=True)``, so the probe itself is a
    cheap local answer and never a download. ``None`` = head absent (the
    caller decides: fail fast at encode, or a soft capability answer).
    """
    source = str(model_source or "")
    if os.path.isdir(source):
        root = Path(source)
    else:
        try:
            from huggingface_hub import snapshot_download

            root = Path(snapshot_download(source, local_files_only=True))
        except Exception:  # noqa: BLE001 - uncached model → no head locally
            return None
    head = root / HYBRID_SPARSE_HEAD_FILE
    return head if head.is_file() else None


def hybrid_head_available(model_source: str) -> bool:
    """Whether the learned-sparse head is cached beside ``model_source``."""
    return _hybrid_head_path(model_source) is not None


def _sparse_head_missing_error(model_source: str, expected: Path) -> RuntimeError:
    return RuntimeError(
        f"EMBED_HYBRID is enabled but the BGE-M3 sparse head is missing for "
        f"{model_source!r}: expected {HYBRID_SPARSE_HEAD_FILE} at {expected}. "
        f"Run: python code-tiny/scripts/prefetch_hybrid_heads.py (downloads the "
        f"~3.5KB head from BAAI/bge-m3 into the cached snapshot). No network "
        f"was touched by this load."
    )


def _load_sparse_head(model_source: str, device: Any) -> Any:
    """Load the sparse Linear head (fp32-cast) with its own cache.

    Keyed separately from the SentenceTransformer cache so an operator
    dropping in the head file later can be picked up by resetting this one
    cache; the head weights are fp16 on the hub and are cast to fp32 — the
    reference implementation runs the encoder in fp32 and the head follows
    (fp16 matmul on CPU/MPS loses the magnitude fidelity the golden test
    pins).
    """
    if torch is None:
        raise RuntimeError("torch is not installed; sparse encode is unavailable.")
    cache_key = (str(model_source), str(getattr(device, "type", device)))
    with _ST_LOCK:
        cached = _SPARSE_HEAD_CACHE.get(cache_key)
        if cached is not None:
            return cached
    head_path = _hybrid_head_path(model_source)
    if head_path is None:
        raise _sparse_head_missing_error(
            model_source, Path(model_source) / HYBRID_SPARSE_HEAD_FILE
            if os.path.isdir(str(model_source))
            else Path(HYBRID_SPARSE_HEAD_FILE)
        )
    state = torch.load(str(head_path), map_location="cpu", weights_only=True)
    weight = state["weight"].float()          # [1, hidden]
    bias = state["bias"].float().reshape(1)   # [1]
    head = (weight.to(device), bias.to(device))
    with _ST_LOCK:
        _SPARSE_HEAD_CACHE[cache_key] = head
    return head


def reset_sparse_head_cache() -> None:
    """Test helper: drop cached sparse heads."""
    with _ST_LOCK:
        _SPARSE_HEAD_CACHE.clear()


def _st_encoder(model: Any) -> Tuple[Any, Any]:
    """Extract ``(tokenizer, encoder)`` under a SentenceTransformer.

    Standard ST layout: the first module is
    ``sentence_transformers.models.Transformer`` exposing ``.tokenizer`` and
    the underlying ``.auto_model`` (XLM-RoBERTa for bge-m3). ``encode()``
    hides token-level hidden states behind pooling, so the sparse path
    forwards through the raw encoder — the dense path is never touched
    (byte-parallel dense guarantee).
    """
    first = model[0]
    tokenizer = getattr(first, "tokenizer", None)
    encoder = getattr(first, "auto_model", None)
    if tokenizer is None or encoder is None:
        raise RuntimeError(
            "The loaded SentenceTransformer does not expose a token-level "
            "encoder (model[0].tokenizer/.auto_model); sparse encode requires "
            "the standard sentence-transformers Transformer module layout."
        )
    return tokenizer, encoder


def encode_sparse(
    model_source: str,
    texts: List[str],
    device: Any = None,
    *,
    model: Any = None,
) -> List[Tuple[List[int], List[float]]]:
    """BGE-M3 learned lexical weights per text: ``(indices, values)``.

    Reference math (FlagEmbedding ``BGEM3FlagModel``): raw-token forward →
    ``relu(last_hidden_state @ Wᵀ + b)`` → keep ``w > 0``, drop the
    cls/eos/pad/unk special-token ids, keep the max weight per repeated
    token id. Indices come back sorted ascending so output is deterministic.
    Runs its own token-level forward pass — the dense encode path is not
    shared, so enabling hybrid leaves dense vectors byte-identical.

    ``model`` accepts a pre-loaded SentenceTransformer (callers that built
    their own instance, e.g. livingdoc / doc-tiny, avoid a second full
    model load); it must be the same family as ``model_source``.
    """
    if not texts:
        return []
    resolved = resolve_device(device)
    if model is None:
        model = get_sentence_transformer(
            model_source,
            device=str(resolved) if resolved is not None else None,
            trust_remote_code=model_policy(model_source)["trust_remote_code"],
            local_files_only=_st_local_files_only(model_source),
        )
    tokenizer, encoder = _st_encoder(model)
    weight, bias = _load_sparse_head(model_source, resolved)
    special_ids = {
        tokenizer.convert_tokens_to_ids(tokenizer.special_tokens_map[name])
        for name in ("cls_token", "eos_token", "pad_token", "unk_token")
        if name in getattr(tokenizer, "special_tokens_map", {})
    }
    results: List[Tuple[List[int], List[float]]] = []
    batch_size = 16
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            batch = [str(text or "") for text in texts[start:start + batch_size]]
            features = tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=_SPARSE_MAX_LENGTH,
                return_tensors="pt",
            )
            features = {key: value.to(resolved) for key, value in features.items()}
            hidden = encoder(**features).last_hidden_state.float()
            token_weights = torch.relu(
                hidden @ weight.transpose(0, 1) + bias
            ).squeeze(-1)
            for row_ids, row_mask, row_weights in zip(
                features["input_ids"].tolist(),
                features["attention_mask"].tolist(),
                token_weights.tolist(),
            ):
                best: Dict[int, float] = {}
                for token_id, active, value in zip(row_ids, row_mask, row_weights):
                    if not active or token_id in special_ids or value <= 0:
                        continue
                    if value > best.get(token_id, 0.0):
                        best[token_id] = value
                indices = sorted(best)
                results.append((indices, [best[index] for index in indices]))
    return results


def _query_sparse_cache_get(model_name: str, text: str) -> Optional[Tuple[List[int], List[float]]]:
    maxsize = _query_cache_maxsize()
    if not maxsize:
        return None
    key = (model_name, text)
    with _EMBEDDER_LOCK:
        cached = _QUERY_SPARSE_CACHE.get(key)
        if cached is not None:
            _QUERY_SPARSE_CACHE.move_to_end(key)
            return (list(cached[0]), list(cached[1]))
    return None


def _query_sparse_cache_put(
    model_name: str, text: str, sparse: Tuple[List[int], List[float]]
) -> None:
    maxsize = _query_cache_maxsize()
    if not maxsize:
        return
    key = (model_name, text)
    with _EMBEDDER_LOCK:
        _QUERY_SPARSE_CACHE[key] = (list(sparse[0]), list(sparse[1]))
        _QUERY_SPARSE_CACHE.move_to_end(key)
        while len(_QUERY_SPARSE_CACHE) > maxsize:
            _QUERY_SPARSE_CACHE.popitem(last=False)


def embed_query_pair(
    text: str,
    model_name: str,
    device_name: Optional[str] = None,
) -> Tuple[List[float], Tuple[List[int], List[float]]]:
    """``(dense, sparse)`` query encode — the hybrid query payload.

    Dense rides the exact :func:`embed_query` path (same LRU, same CPU
    fallback, unchanged signature); sparse is computed once per
    ``(model, text)`` in its own cache. Non-hybrid configurations should
    keep calling :func:`embed_query` — this helper unconditionally computes
    the sparse half when the model family supports it, and raises through
    :func:`encode_sparse`'s fail-fast when the head file is missing.
    """
    dense = embed_query(text, model_name, device_name)
    key_model = model_name
    sparse = _query_sparse_cache_get(key_model, text)
    if sparse is None:
        sparse = encode_sparse(model_name, [text], device=resolve_device(device_name))[0]
        _query_sparse_cache_put(key_model, text, sparse)
    return dense, sparse


def _snapshot_complete(snapshot: str) -> bool:
    """Whether a cached hub snapshot can serve a load without any download.

    ``snapshot_download(local_files_only=True)`` only proves the revision
    folder exists — an interrupted prefetch passes it with the weights
    missing, and an offline load would then die mid-constructor instead of
    falling back online. Gate the offline fast path on the files the
    constructor hard-requires (config, weights, tokenizer).
    """
    root = Path(snapshot)
    if not (root / "config.json").is_file():
        return False
    if not any(root.glob("*.safetensors")) and not any(root.glob("pytorch_model*.bin")):
        return False
    return any((root / name).is_file() for name in _TOKENIZER_FILE_CANDIDATES)


@contextmanager
def _offline_hub_load_guard(active: bool) -> Iterator[None]:
    """Force hub-offline while loading a complete cached snapshot.

    SentenceTransformer threads ``local_files_only`` into its own module
    configs, but the transformers tokenizer/processor layer still probes
    optional artifacts (tokenizer_config, preprocessor_config,
    processor_config) online — turning every cached load on an offline
    host into a HEAD-retry storm. Both libraries read offline mode once
    at import, so flip their captured flags for the load only and
    restore them afterwards.
    """
    if not active:
        yield
        return
    import huggingface_hub.constants as hub_constants
    import transformers.utils.hub as transformers_hub

    hub_previous = getattr(hub_constants, "HF_HUB_OFFLINE", False)
    transformers_previous = getattr(transformers_hub, "_is_offline_mode", False)
    hub_constants.HF_HUB_OFFLINE = True
    transformers_hub._is_offline_mode = True
    try:
        yield
    finally:
        hub_constants.HF_HUB_OFFLINE = hub_previous
        transformers_hub._is_offline_mode = transformers_previous


def _st_local_files_only(model_name: str) -> bool:
    """Offline-safe probe mirroring ``resolve_embedding_cache`` semantics.

    True when the name is a local directory or a complete Hugging Face
    snapshot is already cached — the ST load then never touches the
    network. The probe itself always runs with ``local_files_only=True`` so
    a cache miss is a cheap local answer, never a download. Kept lazy and
    dependency-tolerant: :mod:`tools.common.embedding_runtime` (the audit
    module) stays untouched and unimported.
    """
    if os.path.isdir(model_name):
        return True
    try:
        from huggingface_hub import snapshot_download

        snapshot = snapshot_download(str(model_name), local_files_only=True)
    except Exception:  # noqa: BLE001 - any miss/failure → let ST resolve remotely
        return False
    return _snapshot_complete(str(snapshot))


def get_sentence_transformer(
    model_name: str,
    device: Optional[str] = None,
    trust_remote_code: Optional[bool] = None,
    local_files_only: Optional[bool] = None,
    cache_folder: Optional[str] = None,
) -> Any:
    """Return a cached ``SentenceTransformer`` keyed by ``(model, device)``.

    ``trust_remote_code=None`` follows :func:`model_policy`; an explicit
    boolean wins. ``local_files_only=None`` follows
    :func:`_st_local_files_only`: a complete cached snapshot loads with zero
    network traffic (a cached model must never ping huggingface.co), while a
    miss or partial snapshot keeps the normal online resolve. An explicit
    boolean wins. ``cache_folder`` is forwarded to the constructor when
    provided (offline snapshot loads and the analyzer HF-cache-permission
    fallback). The cache ignores
    ``trust_remote_code``/``local_files_only``/``cache_folder`` variations
    — the first load for a ``(model, device)`` key wins.
    """
    cache_key = (model_name, str(device) if device is not None else None)
    # Every ingest path loads through here, so the floor check must live at
    # the loader, not only on the query reroute (non-Qwen3 names pass
    # through the check untouched).
    _check_qwen3_transformers_floor(model_name)
    with _ST_LOCK:
        cached = _SENTENCE_TRANSFORMER_CACHE.get(cache_key)
        if cached is not None:
            return cached
        from sentence_transformers import SentenceTransformer

        if trust_remote_code is None:
            trust_remote_code = model_policy(model_name)["trust_remote_code"]
        if local_files_only is None:
            local_files_only = _st_local_files_only(model_name)
        kwargs: Dict[str, Any] = {
            "trust_remote_code": trust_remote_code,
            "local_files_only": local_files_only,
        }
        if cache_folder is not None:
            kwargs["cache_folder"] = cache_folder
        with _offline_hub_load_guard(local_files_only):
            model = SentenceTransformer(
                model_name,
                device=str(device) if device is not None else None,
                **kwargs,
            )
        _SENTENCE_TRANSFORMER_CACHE[cache_key] = model
        return model


def reset_caches() -> None:
    """Clear every cache (test helper)."""
    with _EMBEDDER_LOCK:
        _QUERY_VECTOR_CACHE.clear()
        _QUERY_SPARSE_CACHE.clear()
        _EMBEDDER_CACHE.clear()
    with _ST_LOCK:
        _SENTENCE_TRANSFORMER_CACHE.clear()
        _SPARSE_HEAD_CACHE.clear()
