# Phase 01: Quality contract, gold corpus, and baseline

## Context

Current counters mix file-level `has_error`, explicit `ERROR` nodes, and
unreported `MISSING` nodes. Before changing fallback behavior, define one stable
quality vocabulary and measure which error cohorts actually damage extraction.

## Requirements

- Define the versioned `ParseQuality` schema and four quality tiers.
- Capture source-span and structural-context damage, not only raw node counts.
- Build a reviewed 100-file stratified C/C++ corpus without proprietary source.
- Record current Tree-sitter extraction yield, latency, and memory baselines.
- Freeze candidate comparison and quarantine semantics before recovery work.

## Architecture

Create `code-tiny/tools/common/parse_quality.py` as a serialization and policy
contract with no graph, Qdrant, CLI, or libclang dependency. Analyzer-specific
adapters populate it; deterministic pure functions classify quality and compare
candidate summaries.

## Related files

- `code-tiny/tools/common/parse_quality.py` (new)
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/common/legacy_encoding.py`
- `tests/test_parse_quality_contract.py` (new)
- reviewed fixture corpus under the existing tests convention

## Implementation steps

1. Define enums/dataclasses or typed dictionaries for tier, backend, damage,
   semantic yield, retry stage, candidate outcome, and report aggregate.
2. Traverse Tree-sitter nodes once to record `ERROR` and `MISSING` locations,
   damaged byte spans, parent structural kinds, and bounded signatures.
3. Normalize all paths relative to the validated repository root; omit raw source
   and host paths from default serialization.
4. Select 100 fixtures stratified by source/header, size, encoding, grammar,
   macro/generated status, Pro*C/resource type, and compile-context availability.
5. Record clean/recovered symbol, type, call, and include yields plus p50/p95
   latency and peak RSS.
6. Freeze the whole-file candidate comparison tuple and provisional tier rules.

## Todo

- [x] Add and version the common quality schema.
- [x] Add structural damage and semantic-yield collectors.
- [x] Build and review the stratified fixture manifest.
- [x] Capture baseline quality, latency, and RSS results.
- [x] Approve deterministic candidate and quarantine rules.

## Risks

Raw node count or one repository's dialect distribution can produce misleading
thresholds. Keep structural features explicit and version all policy decisions.

## Success criteria

Every fixture produces a deterministic quality record; file/node counts
reconcile; no default record leaks source or absolute paths; candidate ordering
and tier classification are fully covered by pure unit tests.
