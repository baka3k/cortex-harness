from __future__ import annotations

import asyncio
import os
import sys
from typing import List, Optional

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.vb.vb_analyzer_base import main as _main


async def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv or sys.argv[1:])
    if "--dialect" not in args:
        args = ["--dialect", "vba", *args]
    return await _main(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
