"""Pytest conftest: install phase-08 retired-module stubs before any test imports run.

Phase-08 (analyzer-layer-rust-cutover) retired the Python analyzer entry
points. Without stubs, tests that do ``from tools.cobol import cobol_analyzer``
fail at pytest collection (ImportError). The helper installed here puts
``None``-returning placeholder modules in ``sys.modules`` so test modules
load; tests are then skipped loudly by :func:`phase08_retired` on each
TestCase class that depends on retired Python state.

This is the "loud-skip, no silent importorskip" disposition required by
the phase-08 audit.
"""
from __future__ import annotations

from cortex_harness._phase08_skip import install_stubs

install_stubs()