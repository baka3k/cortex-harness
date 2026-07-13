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
        args.extend(["--languages", "both"])
    if "--dry-run" in args:
        return spring_analyzer.main(args)

    base_args = _without_spring_only_args(args)
    if "--disable-message-scan" not in base_args:
        base_args.append("--disable-message-scan")
    for script_name in _child_scripts_for_args(args):
        script_path = os.path.join(_ROOT_DIR, "tools", "java" if script_name.startswith("java") else "kotlin", script_name)
        rc = subprocess.run([sys.executable, script_path] + base_args).returncode
        if rc != 0:
            return int(rc)
    return spring_analyzer.main(args)


def _child_scripts_for_args(args: List[str]) -> List[str]:
    language = "both"
    for idx, item in enumerate(args):
        if item == "--languages" and idx + 1 < len(args):
            language = args[idx + 1]
            break
    if language == "java":
        return ["java_analyzer.py"]
    if language == "kotlin":
        return ["kotlin_analyzer.py"]
    return ["java_analyzer.py", "kotlin_analyzer.py"]


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
