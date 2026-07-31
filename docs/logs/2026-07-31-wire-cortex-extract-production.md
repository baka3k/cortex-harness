# Wire cortex_extract into Production Analyzers — 2026-07-31

## Context
The Rust native extension (`cortex_extract`) had 11 language walkers built and
differential-tested but **none were used in production** — every analyzer ran
pure-Python tree-sitter extraction unconditionally. This plan connected the
fast path into every `parse_*_file()` so `sync code` benefits from the ~7×
Rust speedup across the codebase. Triggered by plan
`plans/260731-2300-wire-cortex-extract-production/plan.md` (parent
260731-1700-multi-language-rust-extraction, phase 3).

## Change
**Phase 1 — Shared module (pre-existing)**
- `code-tiny/tools/common/_rust_accel.py` already existed with
  `is_available()`, `warn_fallback()`, and a single `extract(lang, path, root)`
  helper.
- Added generic dataclass reconstruction helpers
  `materialize_list(items, dataclass_type)` and
  `materialize_dataclass(payload, dataclass_type)` to the same module so
  Family-B tuple adapters can rebuild proper dataclass instances from
  Rust payload dicts.

**Phase 2 — Family A wiring (pre-existing)**
- `code-tiny/tools/go/go_analyzer.py:831` — `parse_go_file()` fast-path wrapper
- `code-tiny/tools/rust/rust_analyzer.py:815` — `parse_rust_file()` wrapper
- `code-tiny/tools/swift/swift_analyzer.py:880` — `parse_swift_file()` wrapper
- All three rename original body to `_python_parse_*_file()` and fall back on
  `extract()` returning `None`.

**Phase 3 — Family B wiring (this session)**
- `code-tiny/tools/csharp/csharp_analyzer.py:662` — 7-tuple wrapper
- `code-tiny/tools/php/php_analyzer.py:733` — 6-tuple wrapper
- `code-tiny/tools/delphi/delphi_analyzer.py:1077` — 9-tuple wrapper
- `code-tiny/tools/java/java_analyzer.py:752` — 9-tuple wrapper
- `code-tiny/tools/cplus/cplus_analyzer.py:1975` — 15-tuple wrapper
- `code-tiny/tools/ts/ts_analyzer.py:210` — 12-tuple wrapper
- Each wraps its `parse_*_file()` with a try-cortex_extract → dict → tuple
  adapter that reconstructs dataclass instances via `_rust_accel`.
  On any failure, one-time `warn_fallback()` is logged and pure-Python
  path is invoked.

**Phase 4 — Smoke test**
- `tests/test_production_wiring.py` (NEW, 283 lines) — minimal in-memory
  fixtures per language, validates each analyzer returns the expected
  shape (dict for Family A, n-tuple for Family B) and that the Rust
  fast path is exercised end-to-end.

## Impact
- **All 9 production analyzers** now hit Rust extraction by default.
- Risk: LOW — Rust walkers are differential-tested against the Python
  walkers; tuple adapters match dataclass field positions exactly.
- Fallback: graceful — when `cortex_extract` is missing or raises, the
  pure-Python path still runs unchanged.
- Speed: every `sync code` invocation benefits from the ~7× speedup
  measured for the Rust walker across 11 languages.

## Decision
- Used a centralized `_rust_accel.py` instead of inlining the try/except in
  every analyzer — single source of truth for availability check + logging.
- Wrote `materialize_list`/`materialize_dataclass` helpers instead of
  hand-coding tuple unpacking per analyzer — generic, schema-drift-safe.
- Chose lazy `from tools.common._rust_accel import …` inside each wrapper
  instead of top-level import — avoids forcing every analyzer subprocess
  to load `cortex_extract` at startup; the wrapper runs only on the hot
  path.
- Did NOT touch `ts_backend_analyzer.py` or `sql_analyzer.py` — they have
  no Rust equivalent (regex / backend-specific).

## References
- plan: `./plans/260731-2300-wire-cortex-extract-production/plan.md`
- commit: `4251c79` (Phase 4 smoke test)
- parent plan: `./plans/260731-1700-multi-language-rust-extraction/`
- tests: `tests/test_production_wiring.py`, `tests/test_go_differential.py`,
  `tests/test_rust_differential.py`
