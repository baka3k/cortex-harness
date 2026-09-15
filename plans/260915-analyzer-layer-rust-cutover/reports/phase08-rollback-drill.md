# Phase 08 — Rollback drill (plan §4, red-team F9)

**Date:** 2026-09-15 · **Environment:** macOS arm64, FalkorDB (docker
`cortex-falkordb` @ 127.0.0.1:6379), local-mode Qdrant, Python 3.12 venv.

## Setup

1. **Scratch checkout** — git worktree `/tmp/phase08-drill` at tag
   `pre-phase08-cutover` (= `34f69b8`, the phase-07 tip immediately before
   the cutover commit `99e9092`). The tree contains the full Python analyzer
   plane deleted by the cutover.
2. **Post-cutover graph copy** — the drill subject graph `p08drill` was
   produced by the **post-cutover** stack (release `cortex-sync` @ HEAD,
   delegated lane, `CORTEX_GRAPH_JOURNAL_MODE=shared-shadow`, composite
   fixture of `tests/fixtures/*`): **874 nodes / 1434 rels** written by the
   Rust `analyzer-*` children. Post-cutover vector state exercised through
   the same run.

## Drill execution (pre-cutover Python plane over the post-cutover graph)

Invoked the **pre-cutover Python orchestrator** from the scratch checkout:

```bash
cd /tmp/phase08-drill/code-tiny
PYTHONPATH=$PWD QDRANT_CODE_PATH=/tmp/p08-drill-qdrant \
python tools/sync/incremental_sync.py \
  --root /tmp/p08-smoke/project --project-id p08drill --parsers go,shell \
  --graph-provider falkordb --falkordb-uri 127.0.0.1:6379 \
  --falkordb-graph p08drill --qdrant-url http://127.0.0.1:6333 ...
```

`CORTEX_RUST_ANALYZER` unset — at `34f69b8` this resolves to the Python
entries (`tools/go/go_analyzer.py`, `tools/shell/shell_analyzer.py`), which
is exactly the reverted fleet.

| Run | Mode | Result | Nodes | Rels | Vectors (go) |
|---|---|---|---|---|---|
| post-cutover baseline | full (Rust) | — | 874 | 1434 | 0 (disabled) |
| drill run 1 | full (Python) | cleanup of Rust-written state | **833** | **1430** | 42 (success) |
| drill run 2 | incremental, no changes | **diff 0** vs run 1 | **833** | **1430** | 42 (success) |
| drill run 3 | incremental, deleted `geometry/geometry.go` | stale vectors purged | 833\* | 1430\* | **23** (−19, matches deleted functions) |

**Convergence criterion — PASS.** Node/rel diff = 0 after 2 sync cycles.
**Stale-vector cleanup — PASS.** Deleting a go file dropped the collection
from 42 → 23 vectors in the next cycle; the surviving 9 functions re-embed
and the orphans are removed.

\* The go *analyzer child* exits 3 on this composite input (see finding 2),
so its per-file deletion writes do not run; graph stability is therefore
also (trivially) preserved for that lane. Vector cleanup happens in the
orchestrator-level embedding/vector-sync plane, which is unaffected.

## Findings

1. **Schema-index contract is fail-closed (desired).** The reverted Python
   plane validates relation endpoints against `CODE_GRAPH_SCHEMA`
   (`tools/graph/writer/query_contract.py:29`) and refuses to write when a
   label lacks an identity index — it never silently corrupts a graph it
   does not fully understand. Rollback is loud, not silent (red-team S3/S4
   compliant).
2. **Pre-existing (rollback-neutral) go strictness.** On the composite
   fixture the pre-cutover Python go lane fails on
   `target label 'ExternalModule' has no required id index` — validation
   against the in-code manifest, triggered by the analyzer's own row data
   for this input. This is **identical behavior at the pre-cutover commit**
   (same code, same input — the drill literally runs `34f69b8` code); it is
   not caused by the cutover or by graph state. Tracked in
   `reports/phase08-followups.md`.
3. **Vector runtime mode.** The reverted plane requires local-mode Qdrant
   (`QDRANT_CODE_PATH`); remote URLs are rejected by design. Operators
   rolling back must point the plane at the local runtime (runbook note).
4. **Go builtins under strict endpoint preflight (Rust, phase-07 era).**
   With a live graph target, the Rust `analyzer-go` / `analyzer-csharp`
   lanes fail endpoint preflight on builtin-type targets (e.g. `float64`).
   Both crates are bit-identical pre/post cutover (`git diff 34f69b8..HEAD`
   on `analyzer-go`, `analyzer-csharp`, `cortex-graph-writer` is empty) —
   pre-existing, not a cutover regression. Tracked in
   `reports/phase08-followups.md`.

## Verdict

The `git revert` rollback path is **real and convergent**: the pre-cutover
Python plane takes a post-cutover graph, cleans it to its own steady state
(diff 0 across cycles), and manages vectors correctly. Failures on the way
are loud and pre-date the cutover. Phase-08 ops sign-off for the rollback
gate: **PASS** (dogfood 7-day gate remains open separately).
