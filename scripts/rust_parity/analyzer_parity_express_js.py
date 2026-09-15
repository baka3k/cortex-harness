#!/usr/bin/env python3
"""Phase 08 parity gate — express_js overlay (python vs rust). Xem
phase08_overlay_harness.py cho logic chung."""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase08_overlay_harness import main_for

if __name__ == "__main__":
    sys.exit(main_for("express_js"))
