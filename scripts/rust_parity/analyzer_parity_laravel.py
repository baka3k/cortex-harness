#!/usr/bin/env python3
"""Phase 08 parity gate — laravel overlay (python vs rust). Xem
phase08_overlay_harness.py cho logic chung."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase08_overlay_harness import main_for

if __name__ == "__main__":
    sys.exit(main_for("laravel"))
