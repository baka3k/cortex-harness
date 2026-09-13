"""
rust_bridge.py
──────────────
Loader seam cho PyO3 extension ``cortex_retrieval_py`` (workspace ``rust/``).

Rust port của retrieval brain (plans/260913-1715-rust-retrieval-graph-port)
cùng API với bản Python tham chiếu; module này là điểm duy nhất quyết định
chạy Rust hay Python để mọi call site (intent classifier, BM25 ranker, query
understanding) không cần biết sự khác biệt.

Resolution mode — env ``CORTEX_RETRIEVAL_RUST``:
  ``0`` / ``off`` / ``python``   Luôn dùng Python (rollback tức thì).
  ``1`` / ``on`` / ``require``   Bắt buộc Rust — raise nếu extension thiếu.
  unset / ``auto`` (mặc định)    Dùng Rust nếu load được, fallback Python.

Extension được tìm theo thứ tự:
  1. ``CORTEX_RETRIEVAL_PY_PATH`` — đường dẫn file trực tiếp.
  2. ``<repo>/scripts/rust_parity/cortex_retrieval_py.so`` — artifact của
     ``make rust-pyo3`` / ``bash scripts/rust_parity/build_pyo3.sh``.
  3. Import thường qua ``sys.path`` (nếu đã cài nơi khác).

Mọi wrapper trả về ``None`` khi Rust không khả dụng — caller fallback Python.
Lỗi runtime từ Rust (ValueError…) được propagate nguyên vẹn: parity gate đảm
bảo semantics giống Python nên lỗi là lỗi thật, không nuốt.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

ENV_OVERRIDE = "CORTEX_RETRIEVAL_RUST"
ENV_EXTENSION_PATH = "CORTEX_RETRIEVAL_PY_PATH"
_PARITY_DIR = Path(__file__).resolve().parents[3] / "scripts" / "rust_parity"

_OFF_VALUES = frozenset({"0", "false", "off", "python"})
_REQUIRE_VALUES = frozenset({"1", "true", "on", "require", "rust"})

_extension: Optional[ModuleType] = None
_resolved = False


def _repo_parity_dir() -> Path:
    return _PARITY_DIR


def _import_from_parity_dir() -> Optional[ModuleType]:
    """Import the extension built by ``make rust-pyo3`` (not on sys.path)."""
    so_path = _repo_parity_dir() / "cortex_retrieval_py.so"
    if not so_path.is_file():
        return None
    parity_str = str(_repo_parity_dir())
    if parity_str not in sys.path:
        sys.path.append(parity_str)
    try:
        return importlib.import_module("cortex_retrieval_py")
    except ImportError:
        return None


def load_extension() -> Optional[ModuleType]:
    """Return the PyO3 module, or ``None`` in auto mode when unavailable.

    Result is cached for the process lifetime; call ``reset_cache()`` in
    tests after changing the environment.
    """
    global _extension, _resolved
    if _resolved:
        return _extension

    mode = os.environ.get(ENV_OVERRIDE, "").strip().lower()
    if mode in _OFF_VALUES:
        _extension, _resolved = None, True
        return None

    explicit = os.environ.get(ENV_EXTENSION_PATH, "").strip()
    module: Optional[ModuleType] = None
    try:
        if explicit:
            explicit_path = str(Path(explicit).expanduser().resolve().parent)
            if explicit_path not in sys.path:
                sys.path.append(explicit_path)
            module = importlib.import_module("cortex_retrieval_py")
        else:
            try:
                module = importlib.import_module("cortex_retrieval_py")
            except ImportError:
                module = _import_from_parity_dir()
    except Exception as exc:  # broken build / missing symbols
        if mode in _REQUIRE_VALUES:
            raise RuntimeError(
                f"{ENV_OVERRIDE}={mode!r} but cortex_retrieval_py failed to "
                f"load: {exc}. Build it via `make rust-pyo3`."
            ) from exc
        logger.debug("[rust-bridge] extension load failed: %s", exc)
        module = None

    if module is None and mode in _REQUIRE_VALUES:
        raise RuntimeError(
            f"{ENV_OVERRIDE}={mode!r} but cortex_retrieval_py was not found "
            f"(looked in sys.path and {_repo_parity_dir()}). "
            "Build it via `make rust-pyo3`."
        )

    _extension = module
    _resolved = True
    if module is not None:
        logger.debug("[rust-bridge] cortex_retrieval_py active (rust path)")
    return _extension


def available() -> bool:
    """Return True when the Rust extension is active in this process."""
    return load_extension() is not None


def reset_cache() -> None:
    """Drop the cached resolution (tests / env changes at runtime)."""
    global _extension, _resolved
    _extension, _resolved = None, False


# ─────────────────────────────────────────────────────────────────────────────
# Thin wrappers — None ⇒ caller falls back to the Python implementation.
# ─────────────────────────────────────────────────────────────────────────────


def classify_query(query: str) -> Optional[str]:
    rust = load_extension()
    if rust is None:
        return None
    return rust.classify_query(query)


def classify_query_explain(query: str) -> Optional[Dict[str, Any]]:
    """Same shape as the Python explain dict (adds ``query`` on top)."""
    rust = load_extension()
    if rust is None:
        return None
    intent, matched = rust.classify_query_explain(query)
    return {"query": query, "intent": intent, "matched": matched}


def get_weight_profile(intent: str) -> Optional[Dict[str, float]]:
    rust = load_extension()
    if rust is None:
        return None
    return dict(rust.get_weight_profile(intent))


def bm25_score(
    documents: Sequence[Dict[str, Any]],
    query: str,
    text_field: str = "text",
    id_field: str = "id",
) -> Optional[Dict[str, float]]:
    """Stateless BM25 over *documents* → ``{id: score}`` (same as
    ``BM25Ranker.build_index`` + ``score``).  Document ids must be strings —
    the public contract of ``BM25Ranker``."""
    rust = load_extension()
    if rust is None:
        return None
    payload = json.dumps(list(documents), ensure_ascii=False)
    return json.loads(rust.bm25_score(payload, query, text_field, id_field))


def query_understanding(text: str) -> Optional[Dict[str, Any]]:
    """Same keys as ``QueryUnderstanding.to_dict()``."""
    rust = load_extension()
    if rust is None:
        return None
    return json.loads(rust.query_understanding(text))
