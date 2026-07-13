from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional, Sequence

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.servlet_jsp import servlet_jsp_analyzer


_OVERLAY_VALUE_FLAGS = {
    "--servlet-jsp-preview-output",
    "--diagnostics-output",
    "--fail-on",
}
_OVERLAY_BOOLEAN_FLAGS = {"--quiet"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--dry-run" not in args:
        java_script = os.path.join(_ROOT_DIR, "tools", "java", "java_analyzer.py")
        java_args = _java_args(args)
        if "--disable-message-scan" not in java_args:
            java_args.append("--disable-message-scan")
        result = subprocess.run([sys.executable, java_script, *java_args], check=False)
        if result.returncode != 0:
            return int(result.returncode)
    return servlet_jsp_analyzer.main(args)


def _java_args(args: Sequence[str]) -> List[str]:
    cleaned: List[str] = []
    skip_next = False
    for item in args:
        if skip_next:
            skip_next = False
            continue
        if item in _OVERLAY_VALUE_FLAGS:
            skip_next = True
            continue
        if item in _OVERLAY_BOOLEAN_FLAGS:
            continue
        cleaned.append(item)
    return cleaned


if __name__ == "__main__":
    raise SystemExit(main())
