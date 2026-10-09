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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("tools.common.embed_runtime")

try:  # pragma: no cover - exercised via the ImportError branch in tests
    import torch
except ImportError:  # pragma: no cover - torch-less environments
    torch = None  # type: ignore[assignment]


_QUERY_VECTOR_CACHE: "OrderedDict[Tuple[str, str], List[float]]" = OrderedDict()
_EMBEDDER_CACHE: Dict[Tuple[str, str], Tuple[Any, Any, Any]] = {}
_SENTENCE_TRANSFORMER_CACHE: Dict[Tuple[str, Optional[str]], Any] = {}
_EMBEDDER_LOCK = threading.Lock()
_ST_LOCK = threading.Lock()

DEFAULT_QUERY_CACHE_SIZE = 512

# Model-family marker for the unified embedding policy. ``prompt_name`` is a
# SentenceTransformer *config key* — the actual instruction text lives in the
# model's own ST config and is never duplicated here.
QWEN3_EMBEDDING_MARKER = "qwen3-embedding"
QWEN3_QUERY_PROMPT_NAME = "query"


def model_policy(model_name: str) -> Dict[str, Any]:
    """Single model-family policy: how to load and how to encode queries.

    Returns ``{trust_remote_code, backend, query_prompt, normalize}``:

    * ``qwen3-embedding`` (anywhere in the name, local snapshots included via
      their basename) → SentenceTransformer backend, last-token pooling comes
      from the model config, query side uses the ``query`` prompt and
      normalized vectors.
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

    Only families that declare a query prompt are rerouted; unrecognized
    names keep the legacy AutoModel query path so no existing behavior
    changes silently.
    """
    return bool(policy.get("backend") == "st" and policy.get("query_prompt"))


_MIN_TRANSFORMERS_FOR_QWEN3 = (4, 51)


def _check_qwen3_transformers_floor(model_name: str) -> None:
    """Fail fast with an actionable message on an old transformers install.

    Qwen3-Embedding needs the Qwen3 architecture (transformers >= 4.51);
    an older install otherwise dies mid-load with a confusing architecture
    error instead of this one.
    """
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
        tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=trust_remote_code)
        model = AutoModel.from_pretrained(model_name, trust_remote_code=trust_remote_code)
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
    """Query encode via SentenceTransformer (last-token pooling family).

    Accelerator failures evict the SentenceTransformer cache and reload on
    CPU with the same prompt/normalize policy; the AutoModel mean-pool path
    is never entered for these models (their pooling differs).
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

        snapshot_download(str(model_name), local_files_only=True)
        return True
    except Exception:  # noqa: BLE001 - any miss/failure → let ST resolve remotely
        return False


def get_sentence_transformer(
    model_name: str,
    device: Optional[str] = None,
    trust_remote_code: Optional[bool] = None,
    local_files_only: Optional[bool] = None,
    cache_folder: Optional[str] = None,
) -> Any:
    """Return a cached ``SentenceTransformer`` keyed by ``(model, device)``.

    ``trust_remote_code=None`` follows :func:`model_policy`; an explicit
    boolean wins. ``local_files_only`` and ``cache_folder`` are forwarded
    to the constructor when provided (offline snapshot loads and the
    analyzer HF-cache-permission fallback); ``None`` keeps
    SentenceTransformer's own default. The cache ignores
    ``trust_remote_code``/``local_files_only``/``cache_folder`` variations
    — the first load for a ``(model, device)`` key wins.
    """
    cache_key = (model_name, str(device) if device is not None else None)
    if QWEN3_EMBEDDING_MARKER in str(model_name).lower():
        # Every ingest path loads through here, so the floor check must
        # live at the loader, not only on the query reroute.
        _check_qwen3_transformers_floor(model_name)
    with _ST_LOCK:
        cached = _SENTENCE_TRANSFORMER_CACHE.get(cache_key)
        if cached is not None:
            return cached
        from sentence_transformers import SentenceTransformer

        if trust_remote_code is None:
            trust_remote_code = model_policy(model_name)["trust_remote_code"]
        kwargs: Dict[str, Any] = {"trust_remote_code": trust_remote_code}
        if local_files_only is not None:
            kwargs["local_files_only"] = local_files_only
        if cache_folder is not None:
            kwargs["cache_folder"] = cache_folder
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
        _EMBEDDER_CACHE.clear()
    with _ST_LOCK:
        _SENTENCE_TRANSFORMER_CACHE.clear()
