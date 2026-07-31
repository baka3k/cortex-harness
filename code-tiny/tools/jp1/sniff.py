from __future__ import annotations

from functools import lru_cache
import os
import re

from tools.common.legacy_encoding import decode_legacy_bytes


@lru_cache(maxsize=4096)
def _sniff(path: str, modified_ns: int, size: int) -> bool:
    del modified_ns, size
    try:
        with open(path, "rb") as handle:
            text = decode_legacy_bytes(handle.read(65536)).text
    except OSError:
        return False
    nonblank = [line.strip() for line in text.splitlines() if line.strip()]
    if not any(re.match(r"^unit=", line, re.IGNORECASE) for line in nonblank[:5]):
        return False
    return any(line.startswith("{") or line == "{" for line in nonblank[:10]) and any(
        re.match(r"^ty\s*=", line, re.IGNORECASE) for line in nonblank[:50]
    )


def is_jp1_file(path: str) -> bool:
    if not path.lower().endswith(".txt"):
        return False
    try:
        stat = os.stat(path)
    except OSError:
        return False
    return _sniff(os.path.realpath(path), stat.st_mtime_ns, stat.st_size)