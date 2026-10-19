# Phase 04 — live backfill + acceptance + docs (runs LAST, after 01–03)

## Goal

Prove both engine fixes (sha-join + full symbol universe) on the real
the live project deployment with a **single** reconcile, gate the plan's acceptance
criteria, and leave the docs/log honest.

## Tasks

1. **Live reconcile** — from `<project-dir>`:
   `dev sync link --project <id> --reconcile`. Expect: prior actives
   superseded, fresh set re-appended with SHAs over the FULL symbol
   universe. Record before/after telemetry (`sha_join`,
   `symbol_universe`, link counts) and `links.jsonl` growth in the
   session log.
2. **Full-universe gate** — the sync report's `symbol_universe` must
   equal the per-label server counts on the live graph (the live project: roughly
   100k+ once Field joins and truncation is gone; the exact number is
   whatever the graph holds). A `symbol_universe` containing a
   round 10000 is the regression signature of Phase 03's bug — fail on
   it.
3. **Incident regression** — `find_doc_for_code(name="grid_unit_col")`
   must now return ≥1 active record (the para-21 pairing from the
   Phase 03 evidence); same for `UNIT_COL_NO` / `UNIT_COL_NAME`
   multiple-file fanout.
4. **Byte-for-byte spot check** — script
   `link-tiny/tests/verify_record_shas.py` (standalone, read-only):
   sample ≥20 active records, recompute the expected doc/code SHA from
   the live baselines using the contract rules, and diff. Gate: 100% of
   sampled records match; ≥95% of ALL active records non-null on both
   sides; every miss is explained by a `sha_join` counter (missing file
   in inventory, ambiguous repo) — no unexplained nulls.
5. **Stale-drift live proof** — accept the unit-level proof from Phase 02
   test 4a as the drift gate (live code re-sync is heavy; pruning
   inventory entries violates the read-only rule — decision recorded
   here).
6. **Query-path regression** — link_mcp against the reconciled data:
   `trace_status` shows `sha_join` + the new universe counts;
   `find_code_for_doc` results carry non-null `source_sha` in their
   `doc`/`code` views.
7. **Docs + log** — README "Stale behavior" (edits now detected) and
   symbol-coverage notes; LINK_CONTRACT.md reviewed once more against
   the probe output; session log entry per hi-log conventions (index +
   dated file) covering BOTH root causes, join rules, reconcile numbers,
   before/after coverage.

## Files

- New: `link-tiny/tests/verify_record_shas.py`
- Touched: `link-tiny/README.md`, `docs/LINK_CONTRACT.md`,
  `docs/logs/2026-10-19-link-tiny-doc-code-traceability.md` (or a new
  dated log file + index line)

## Test scope

Mapped to acceptance rows 1–4 + the incident regression. Expected size
**M**: 1 live reconcile, 1 verifier script with exit-code gate, MCP
query checks, doc edits, log entry.
