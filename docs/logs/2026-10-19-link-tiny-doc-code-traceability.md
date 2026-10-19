# link-tiny doc↔code traceability (Phase 1) — 2026-10-19

[inline:log-writer]

## Context

Plan `docs/plans/261019-1126-link-tiny-doc-code-traceability/plan.md`
(hi-plan --full, hi-predict CAUTION with binding mitigations R1–R3):
code graph and doc graph shared only `project_id` — no edge, link, or tool
connected a document to the code implementing it. Phase 1 ships exact-name
linking plus a read-only MCP facade; fuzzy matching is gated on coverage
telemetry, not guessed.

## Change

- **New tree `link-tiny/`** (parallel to code-tiny/doc-tiny):
  `registry_facade.py` (delegates to `code-tiny/tools/common/project_registry.py`
  via the `project_contract.py` guarded-import pattern + owns the
  `{instance}/links/{project_id}/` layout, path-segment-safe),
  `normalizers.py` (exact-name matching with word-tuple keys, stoplist RT-5,
  `build_match_index` for O(1) token lookup), `link_store.py` (append-only
  `links.jsonl` + atomic `state.json`, portalocker writer lock, torn-tail
  tolerance, `latest_records` canonical view), `link_engine.py` (delta →
  candidates → score → stale → write; doc SHAs from
  `.cortext-harness/sync-state/*.json`, code SHAs from `.cache/inventories`),
  `link_worker.py` (per-project worker subprocess), `mcp_server.py`
  (`link_mcp` :8790, `find_code_for_doc` / `find_doc_for_code` /
  `trace_status`, envelope v2 via `cortex_harness.mcp_contract`,
  project-scoped sensitive-filtered snippets, degrade on upstream failure).
- **`code-tiny/tools/graph/read_only_opener.py`** — the plan's sanctioned
  RT-3 bounded exception: one additive lease-free read-only opener (zero
  `StorageLease`; FalkorDBLite via `_open_local_falkordb`, LadybugDB via
  `read_only=True`), pinned by `tests/test_read_only_opener_no_lease.py`.
  Remote pools build their own `ConnectionPool` with
  `MaintNotificationsConfig(enabled=False)`.
- **Registration, additive only:** `cortex_harness/dev.py` (LINK_TINY,
  MCP_SERVICES link-tiny entry with `default: False`, `sync link` group +
  `_link_process_scope` (advisory lock only — no MCP pause needed for
  lease-free reads), `--server link` choice, `_mcp_env_from_config` branch,
  `mcp start/add` skip opt-in servers); `cortex_harness/sync_processes.py`
  (`SyncOwner` widened, `_is_link_worker`, matcher table);
  `scripts/mcp-lifecycle.py` (SERVERS entry `default: False` keeps
  `--server all` = code + doc, `requirement_files`, process markers).

## Impact

- New capability: `dev sync link [--dry-run|--project|--reconcile|stop]`,
  `dev start --server link` (:8790, opt-in — `all` unchanged).
- Zero behavioral change to `dev sync code/doc`, graph_mcp, mind_mcp —
  reviewer verified every diff hunk additive; full suite failure set is
  byte-identical to pristine HEAD (20 pre-existing failures, incl.
  `install-devkit` Makefile drift from c675b74).
- Live coverage checkpoint on `cortext` (remote FalkorDB): 502 docs /
  9,655 symbols → 414 docs linked (82.47%), 2,115 symbols referenced
  (21.91%), 12,882 exact-name links, delta no-op on second run; full build
  ~8s after the match-index fix (per-token linear scan was unbounded).
  This is the Phase-3 fuzzy-gate input
  (`docs/plans/.../reports/coverage-checkpoint-20261019.md`).

## Decision

- **Exact-name only in Phase 1** (scope challenge #1) — fuzzy matching
  gated on `state.json.coverage` telemetry; stoplist prevents RT-5
  ambiguity explosion.
- **Lease-free reads + no MCP pause** for link syncs — RT-3 bounded
  exception (one opener) instead of pausing servers; `dev sync link` uses
  only the `sync-link.lock` advisory lock.
- **redis-py 8 `CLIENT MAINT_NOTIFICATIONS`** probe fails against OSS
  FalkorDB and poisons clients created after a `close()` in-process — the
  opener disables the probe on its own pools (discovered live, first run
  crashed flakily).
- **Review cycle 1 (9 findings → 0):** project-scoped snippet fetch (was
  cross-project leak), redact-before-truncate (R2), `is_safe_project_id`
  path gate, lifecycle process markers, `default: False` on MCP_SERVICES
  consumers, top_k clamp ≤100 + snippet budget 20, stale-baseline scope
  doc, docs corrections. Reviewer re-verdict: **9.5/10, auto-approve**.

## References

- plan: ./docs/plans/261019-1126-link-tiny-doc-code-traceability/plan.md
- contract: ./docs/LINK_CONTRACT.md
- commit: c021eb2
- related: mem 260828-1508 sibling-read pattern, 261013-0000 envelope v2
