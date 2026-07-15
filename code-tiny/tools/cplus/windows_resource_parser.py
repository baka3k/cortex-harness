"""Compatibility entry points for Windows resource parsing.

The canonical implementation lives in :mod:`tools.cplus.rc_parser`.  Keep
these names for callers introduced before the richer Resource/UIControl graph
schema was added.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from tools.cplus.rc_parser import (
    decode_rc_bytes,
    extract_message_map_handlers,
    extract_resource_tokens,
    parse_rc_file,
    read_rc_text,
)


WINDOWS_RESOURCE_EXTENSIONS = (".rc", ".rc2")


def is_windows_resource_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in WINDOWS_RESOURCE_EXTENSIONS


def read_windows_resource_text(path: str) -> Tuple[str, str]:
    text, encoding, _lossy = read_rc_text(path)
    return text, encoding


def parse_windows_resource_file(path: str, root: str) -> Dict[str, Any]:
    return parse_rc_file(path, root)


__all__ = [
    "WINDOWS_RESOURCE_EXTENSIONS",
    "decode_rc_bytes",
    "extract_message_map_handlers",
    "extract_resource_tokens",
    "is_windows_resource_file",
    "parse_rc_file",
    "parse_windows_resource_file",
    "read_rc_text",
    "read_windows_resource_text",
]
