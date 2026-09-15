# Phase-08 verification pass — handshake, drill, gates — 2026-09-15

[inline:log-writer]

## Context

`hi-craft --full` on `plans/260915-analyzer-layer-rust-cutover/phase-08.md`.
The cutover itself was already committed (`99e9092` flip+delete, `0e49def`
post-commit stubs), but the plan §2/§4/§5 gates were open: the `--version`
build-commit handshake existed only as a baked string (no orchestrator
check), the rollback drill was not run, the sync smoke was skipped, and the
report still said "commit hash TBD".

## Change

- **Handshake landed** (plan §2, red-team F5): `cortex-analyzer-framework`
  gained `build.rs` + `BUILD_COMMIT` + `print_version_probe()`
  (rust/crates/cortex-analyzer-framework/src/lib.rs:28); all 35 spawnable
  child binaries answer `--version` with `<name> <sha>`; cortex-sync
  `run_child` probes each binary once per process and refuses stale stamps
  with exit 126 (rust/crates/cortex-sync/src/orchestrator.rs:67).
- **Delegated-lane landmine fixed**: `_group_paths_by_framework` still
  imported six detector modules deleted by the cutover — any delegated sync
  with changed paths would ImportError. Now routed through
  `_optional_detector_class` (find_spec): absence → loud stderr note +
  strong-candidate heuristics; transitive breakage after `git revert`
  stays loud (code-tiny/tools/sync/incremental_sync.py:686).
- **Gates re-run**: workspace `cargo build --release` + `clippy -D warnings`
  clean (17 pre-existing lint-drift fixes in cortex-dev/cortex-mcp);
  `cargo test -p cortex-sync --lib` 74/74; pytest 1446 passed / 184 loud
  skips / 70 archived-behaviour failures — exactly the cutover baseline;
  sync smoke on live FalkorDB: native lane 5/5, delegated lane 19 primaries
  + overlays + topology all binaries, retired flip-matrix cells loud,
  stale-binary refusal verified live with a `deadbeef`-stamped child.
- **Rollback drill PASS** (plan §4): pre-cutover checkout (tag
  `pre-phase08-cutover`) takes over the post-cutover graph; node/rel diff 0
  after 2 cycles; stale vectors purged 42→23 on deletion.
- **Bookkeeping corrected**: umbrella plan Wave-D closure note and flutter
  plan `reference-only` flip had been *claimed* at cutover but never
  landed — both landed now; report updated with real hashes and actual LOC
  (78,414 deleted in `99e9092`, not the ~84,780 estimate).

## Impact

- Operators get a real stale-binary guard: a mixed-commit `cargo build`
  now fails loudly at spawn instead of silently drifting parity (med risk
  reduced to low).
- Delegated lane (`dev sync code` with cplus in the filter) works again
  post-cutover; without the fix it would crash on first incremental run.
- Known open items catalogued (plans/260915-analyzer-layer-rust-cutover/reports/phase08-followups.md):
  cplus default-required journal lane fails closed with a graph target
  (workaround `CORTEX_GRAPH_JOURNAL_MODE=shared-shadow`, verified green);
  go/csharp endpoint preflight on builtin targets + ladybug `SET +=` are
  pre-existing (bit-identical at pre-cutover). The only remaining phase-08
  gate is the 7-day dogfood sign-off.
- Side effects surfaced, not committed: a parallel vector-lane workstream
  (`cortex-embed/backend.rs`, `scripts/rust_mcp/*`,
  `plans/260915-2027-vector-lane-rust-port/`) sits uncommitted in the
  working tree — excluded from this commit deliberately.

## Decision

- Handshake placement in `run_child` (all 4 call sites are analyzer
  children; the Python delegate path bypasses it via raw `Command`) — one
  choke point, no per-site wiring.
- Deleted detectors degrade instead of being restored: restoring files
  would contradict the phase-08 delete list; the guard keeps the delegated
  lane functional and makes `git revert` restore full detection.
- Foreign workstream changes left uncommitted and unreviewed rather than
  folded in — different plan, different risk owner.

## References

- plan: ./plans/260915-analyzer-layer-rust-cutover/phase-08.md
- reports: ./plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md,
  ./plans/260915-analyzer-layer-rust-cutover/reports/phase08-rollback-drill.md,
  ./plans/260915-analyzer-layer-rust-cutover/reports/phase08-followups.md
- commits: 2531403 (this pass), 99e9092 + 0e49def (cutover)
- tag: pre-phase08-cutover (34f69b8, rollback point)
- previous log: ./docs/logs/2026-09-15-phase08-analyzer-cutover.md
