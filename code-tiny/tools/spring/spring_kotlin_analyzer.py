from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional, Sequence

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.spring import spring_analyzer


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--languages" not in args:
        args.extend(["--languages", "kotlin"])
    if "--dry-run" in args:
        return spring_analyzer.main(args)
    kotlin_script = os.path.join(_ROOT_DIR, "tools", "kotlin", "kotlin_analyzer.py")
    kotlin_args = _without_spring_only_args(args)
    if "--disable-message-scan" not in kotlin_args:
        kotlin_args.append("--disable-message-scan")
    rc = subprocess.run([sys.executable, kotlin_script] + kotlin_args).returncode
    if rc != 0:
        return int(rc)
    return spring_analyzer.main(args)


def _without_spring_only_args(args: List[str]) -> List[str]:
    cleaned: List[str] = []
    skip_next = False
    for item in args:
        if skip_next:
            skip_next = False
            continue
        if item in {"--spring-facts-output", "--languages"}:
            skip_next = True
            continue
        cleaned.append(item)
    return cleaned


if __name__ == "__main__":
    raise SystemExit(main())
