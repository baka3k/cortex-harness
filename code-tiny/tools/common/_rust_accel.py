"""Shared Rust acceleration module for cortex_extract native extension.

Centralizes the `cortex_extract` import and feature detection so every
production analyzer shares a single fallback path. When the native extension
is unavailable (not built / not on path), analyzers gracefully degrade to
their pure-Python tree-sitter implementations.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

try:
    import cortex_extract as _rust_mod  # type: ignore[import-not-found]
    _AVAILABLE = True
except ImportError:
    _rust_mod = None  # type: ignore[assignment]
    _AVAILABLE = False

# Module-level handle: re-exported so analyzers can call cortex_extract.extract_<lang>()
cortex_extract = _rust_mod

# Track which languages have already emitted a fallback warning.
_warned: set[str] = set()


def is_available() -> bool:
    """Return True if the cortex_extract native extension is importable."""
    return _AVAILABLE


def warn_fallback(lang: str) -> None:
    """Emit a one-time warning per language when falling back to pure Python.

    The native extension is the fast path (~7x faster). When it is unavailable
    or raises, we fall back to the pure-Python walker. Logging once per
    language keeps the output readable on large batches.
    """
    if lang not in _warned:
        _warned.add(lang)
        if not _AVAILABLE:
            logger.warning(
                "cortex_extract native extension is unavailable; "
                "%s analyzer using pure-Python extraction (~7x slower).",
                lang,
            )
        else:
            logger.warning(
                "cortex_extract.extract_%s raised; falling back to pure-Python "
                "extraction for this file.",
                lang,
            )


def extract(lang: str, path: str, root: str) -> Optional[Dict[str, Any]]:
    """Try the Rust fast path for ``lang``.

    Returns the payload dict on success, or ``None`` if the extension is
    unavailable or raised — callers should then run the pure-Python walker.
    """
    if not _AVAILABLE:
        return None
    fn_name = f"extract_{lang}"
    fn = getattr(_rust_mod, fn_name, None)
    if fn is None:
        return None
    try:
        return fn(path, root)
    except Exception:
        warn_fallback(lang)
        return None


def materialize_list(items: Optional[Iterable[Dict[str, Any]]], dataclass_type: type) -> List[Any]:
    """Convert a list of ``dict`` payloads into ``dataclass_type`` instances.

    The Rust side serializes symbols as plain dicts (mirroring
    ``dataclasses.asdict``). This helper rebuilds proper dataclass instances
    so the consumer-side parse functions can keep their existing tuple-based
    contracts without changing every downstream call site.

    Unknown extra keys in the payload are ignored; missing keys fall back
    to the dataclass default (if any) so partial payloads stay safe.
    """
    if not items:
        return []
    field_names = {f.name for f in dataclasses.fields(dataclass_type)}
    results: List[Any] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        kwargs = {k: entry[k] for k in field_names if k in entry}
        try:
            results.append(dataclass_type(**kwargs))
        except TypeError:
            # Defensive: skip entries the dataclass cannot construct (schema drift).
            continue
    return results


def materialize_dataclass(payload: Optional[Dict[str, Any]], dataclass_type: type) -> Any:
    """Convert a single dict payload into a ``dataclass_type`` instance."""
    if not isinstance(payload, dict):
        return dataclass_type()
    field_names = {f.name for f in dataclasses.fields(dataclass_type)}
    kwargs = {k: payload[k] for k in field_names if k in payload}
    try:
        return dataclass_type(**kwargs)
    except TypeError:
        return dataclass_type()
