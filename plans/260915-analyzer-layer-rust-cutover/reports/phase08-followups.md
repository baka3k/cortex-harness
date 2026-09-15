# Phase 08 — Follow-ups surfaced during verification (2026-09-15)

Open items discovered by the phase-08 verification pass (post-commit
stub fix `0e49def` + handshake commit). None block the cutover commit:
every failure mode below is **loud / fail-closed**, and the pre-existing
ones are provably identical at the pre-cutover commit.

## 1. Legacy-17 embedding regression (accepted at cutover)

The legacy 17 parsers emit `vectors=0 vector_status=disabled` because the
phase-06 embedding carve-out kept that lineage on Python children, which
the cutover deleted. **Accepted risk** (user chose "follow plan
literally", overriding the shared-7 delete-list narrowing).
Restoring = port embedding-input artifact emission to each Rust binary.

## 2. cplus journal lane (post-cutover regression — loud)

Trigger: any sync whose parser filter includes `cplus` with changed files
AND a graph target. `_journal_mode_for_lane` defaults the cplus lane to
`shared-required` journaling; the Rust `analyzer-cplus` does not implement
the Python-plane SQLite journal, so `finalize_journal_from_env` raises
"required graph journal was not created by the analyzer" and the run fails
closed. Workaround until fixed: `CORTEX_GRAPH_JOURNAL_MODE=shared-shadow`
(shadow mode verified green end-to-end for the cplus lane in the phase-08
smoke). Real fix = journal plane in Rust, or a journal-aware carve-out
routing cplus through the retained Python clang plane (needs restoring a
cplus entry script — contradicts the phase-08 delete list, hence deferred).

## 3. go / csharp endpoint preflight on builtin targets (pre-existing)

With a live graph target, `analyzer-go` fails
`relationship endpoint preflight failure for relations:Alias:ALIASES:Type`
(target `float64` has no node), `analyzer-csharp` fails similarly on exit 3.
`git diff 34f69b8..HEAD` over `rust/crates/analyzer-go`,
`rust/crates/analyzer-csharp`, `rust/crates/cortex-graph-writer` is empty —
bit-identical pre/post cutover, i.e. a phase-07-era gap that only shows on
the strict graph-write path (parity gates ran artifact-level). Fix: seed
builtin type nodes, or exempt builtin targets in the preflight.

## 4. Ladybug `SET n += row` gap (pre-existing, environment-dependent)

The embedded ladybug provider rejects the graph-writer's `SET n += row`
Cypher (`Parser exception: expected rule oC_SingleQuery`), so graph writes
through `--graph-provider ladybug` fail for labels using map-projection
SET. Untouched since phase-06; macOS runs use FalkorDB. Fix: expand the
map projection into explicit SET assignments in `cortex-graph-writer`.

## 5. Retrieval-py link failure on macOS (pre-existing, environment)

`cargo build --release -p cortex-retrieval-py` fails at link time
(undefined `_PyBytes_*` symbols; pyo3 0.22 `extension-module`+`abi3-py312`
on this host). Fails identically at `pre-phase08-cutover`; CI (Linux) is
green. Local workspace gates therefore run
`--workspace --exclude cortex-retrieval-py`.

## 6. cortex-dev / cortex-mcp clippy drift (fixed in phase-08 verification)

16 `cortex-dev` + 1 `cortex-mcp` lints (collapsible_if, print_with_newline,
manual_clamp, manual_is_multiple_of, needless_lifetimes, type_complexity,
redundant_pattern_matching, unnecessary_to_owned, only_used_in_recursion)
surfaced when the clippy gate was widened from `-p cortex-sync` to the full
workspace (clippy 1.97). Fixed mechanically in the handshake commit.
