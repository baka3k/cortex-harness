# Phase 03 — Device auto-detect — report

Date: 2026-08-30

## What shipped

- `embed_runtime.resolve_device`: `None`/blank/`"auto"` auto-detects —
  darwin → MPS when `torch.backends.mps.is_available()`, elsewhere →
  CUDA when `torch.cuda.is_available()`, otherwise CPU. Explicit values
  keep validate+fallback-CPU semantics; an explicit `cpu` is never
  upgraded. Every resolution logs `embed device resolved: <device>
  (source=auto|env|param)`. Torch-less import degrades gracefully
  (`resolve_device` returns `None`, `get_embedder` raises with context;
  MCP boot survives).
- `cortex_harness/dev.py`: `_normalize_embed_device("auto")` now resolves
  to a concrete device inside the launcher (red team #4) — `"auto"` never
  reaches an analyzer CLI. New `_embed_device_cli_arg(env)` helper is the
  single choke point for `--device` (code sync) and `--embedding-device`
  (doc sync); both call sites converted. dev-init default
  `code.env.device` changed `"cpu"` → `"auto"` (doc side inherits).
- Preload guard: `_preload_embedder_on_startup` in all 4 backends swallows
  load failures with a printed warning — a broken accelerator can no
  longer kill server boot; the per-call CPU retry recovers embed.
- `explore_service._make_embedder`: `SentenceTransformer(model_name,
  device=embed_runtime.resolve_device())` — one device policy everywhere.

## Gate evidence (G2)

Benchmark `scoped-cold` through unified dispatch, same fixture corpus
(`reports/phase03.json` vs `baseline.json`):

| Stage | Baseline (CPU) | Phase 03 (auto → MPS) | Ratio |
|-------|----------------|------------------------|-------|
| embed p50 | 45.0 ms | 18.0 ms | **2.5× (G2 ≥ 2× achieved)** |
| scoped-cold wall p50 | 45.9 ms | 18.5 ms | 2.5× |
| scoped-repeat wall p50 | 0.9 ms | 0.9 ms | unchanged (G1 intact) |

Auto-detect log verified: `embed device resolved: mps (source=auto)`.

Numeric tolerance CPU vs MPS (acceptance): max cosine distance over 10
query/corpus comparisons = **1.1e-16** (float noise, tolerance 1e-3);
top-5 overlap 10/10 identical.

## Tests

- `tests/test_embed_runtime.py::AutoDetectDeviceTests` — platform matrix
  {darwin, win32, linux} × {mps, cuda, none} × {None, "auto", explicit
  "cpu", explicit "mps" off-darwin}; env override wins; torch-missing
  behavior.
- `tests/test_dev_sync_windows_remote.py` — `_normalize_embed_device
  ("auto")` returns a concrete device with platform-correct expectations;
  `_embed_device_cli_arg` never yields `"auto"` (negative assertion).
- `PreloadGuardTests` — preload failure swallowed (server continues),
  disabled preload short-circuits.

## Notes / deferred

- Full end-to-end analyzer sync on a real project was not executed in
  this environment; the crash-avoidance contract (`"auto"` can never
  reach analyzer CLIs) is enforced and tested at `_embed_device_cli_arg`,
  which both sync plumbing call sites use exclusively.
- Windows/CUDA covered by unit tests only (no GPU machine in scope), per
  plan acceptance.
- `EMBED_DEVICE=cpu` opt-out verified by test; changing env requires a
  process restart (documented behavior, not a runtime switch).
