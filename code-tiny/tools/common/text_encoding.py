"""Shared byte-decoding helpers for source files with mixed/legacy encodings.

Several parsers in this repo need to read source text that may be UTF-16
(Windows resource scripts), UTF-8, or a legacy Windows/Japanese code page
(CP932/Shift-JIS), such as Pro*C, shell, JP1, and INI files copied from
mainframe/UNIX batch environments. This module centralizes the decode
fallback chain first proven in ``tools/cplus/rc_parser.py`` so every caller
applies the same detection order.
"""

from __future__ import annotations

from typing import Tuple


def decode_source_bytes(data: bytes) -> Tuple[str, str, bool]:
    """Decode source bytes and return ``(text, encoding, lossy)``.

    Detection order: UTF-16 BOM, UTF-8 BOM, UTF-16 heuristic (many NUL bytes
    on alternating positions), then UTF-8, then CP932 (Shift-JIS), finally a
    lossy CP1252 fallback so callers never raise on malformed input.
    """

    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le"), "utf-16-le", False
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be"), "utf-16-be", False
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig", False

    sample = data[:512]
    if sample:
        odd_nuls = sample[1::2].count(0)
        even_nuls = sample[0::2].count(0)
        threshold = max(2, len(sample) // 8)
        if odd_nuls >= threshold:
            return data.decode("utf-16-le"), "utf-16-le", False
        if even_nuls >= threshold:
            return data.decode("utf-16-be"), "utf-16-be", False

    for encoding in ("utf-8", "cp932"):
        try:
            return data.decode(encoding), encoding, False
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace"), "cp1252", True
