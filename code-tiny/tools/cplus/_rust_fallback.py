"""Rust extension fallback wrapper for cplus_analyzer.

If `cortex_extract` is importable (built via maturin / prebuilt wheel), use it
for the CPU-bound extraction layer. Otherwise fall back to the existing
`parse_c_family_file()` Python implementation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import cortex_extract as _rust_extract  # noqa: F401
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


def is_rust_available() -> bool:
    return _RUST_AVAILABLE


def extract_cplus(path: str, root: str, is_cpp: Optional[bool] = None) -> Dict[str, Any]:
    """Single-file extraction; returns a dict matching the ParseResult schema."""
    if not _RUST_AVAILABLE:
        raise RuntimeError("cortex_extract is not built; use _python_parse_c_family_file() instead")
    if is_cpp is None:
        return _rust_extract.extract_cplus(path, root)
    return _rust_extract.extract_cplus_force_cpp(path, root, is_cpp)


def extract_cplus_batch(
    paths: List[str],
    root: str,
    threads: int = 0,
) -> List[Dict[str, Any]]:
    """Multi-threaded batch extraction; returns a list of payload dicts."""
    if not _RUST_AVAILABLE:
        raise RuntimeError("cortex_extract is not built; use _python_parse_c_family_file() instead")
    return _rust_extract.extract_cplus_batch(paths, root, threads)


def warn_fallback() -> None:
    """Log a one-time warning when running on the Python fallback path."""
    if not _RUST_AVAILABLE:
        logger.warning(
            "cortex_extract native extension is unavailable; falling back to pure Python "
            "extraction (~7x slower). Build with `maturin develop` in rust-analyzer-core/."
        )
