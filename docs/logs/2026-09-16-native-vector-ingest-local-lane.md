# Native vector-ingest local lane — engine + writer/reader wire + flip — 2026-09-16

## Context

Plan `plans/260916-1154-native-vector-ingest-local/plan.md` (rev2, absorbing
red-team-rev1). Since sync-plane phase-06 the native embedding driver served
remote HTTP only; every local branch failed closed — and since phase-08 the
python analyzer children don't embed, so the local code-vector lane received
ZERO writes (frozen legacy pickle data). This plan makes the Rust JSON engine
(`LocalQdrantStore`, `cortex-local-store.json`) the owner of the local code
lane: writer (cortex-sync embedding pass) + reader (cortex-mcp code lane),
atomically, with mind/doc lane untouched.

## Change

### Phase-01 — engine hardening (`cortex-storage/src/qdrant.rs`)

- Filter grammar completed: `must_not` (nested filters) + `has_id` +
  `should`/`min_should` — `stale_filter`'s `{"must_not":[{"has_id":[…]}]}`
  previously silently ignored → local deletes would have eaten kept points
  (F3a). `point_matches` split into `filter_matches`/`condition_matches`/
  `field_condition_matches`.
- `get_collection_info` now carries REST-like `result.config.params.vectors`
  (F3b) — the cortex-sync drift guard (`vector_sizes`) works on local stores;
  fixed pre-existing `normalize_vectors_config` mis-store of the anonymous
  `{"vectors":{"size":…}}` body.
- Persist-mode bulk: `upsert_deferred`/`delete_deferred`/`flush()` kill the
  O(n²)-between-ops serialization (F5); gate = bytes + wall-time
  (`flush_bytes_written`), not flush counts (red-team H4).
- `LocalQdrantReader`: lock-free reader (D4) — no flock, (mtime,len)-
  revalidated snapshots; writer durability is `write_atomic` rename so no
  torn reads. Concurrent writer+reader test pins it.
- Legacy pickle guard (compound key: `collection/` OR root `storage.sqlite`,
  AND JSON absent → loud refusal with re-index recipe); JSON present →
  re-index window allows coexistence.
- `create_payload_index` idempotent; memory-lean typed streaming parse
  (no Value tree) — reader peak RSS 71MB on a 118MB store.

### Phase-02 — writer wire (`cortex-sync/src/vector_store.rs`)

- `NativeStore::{Remote, Local, Unsupported}` + `VectorWriteOps` trait seam;
  `sync_vector_documents` runs unchanged over both engines (local path =
  deferred upserts + 1 closing flush).
- Hatch D5: `CORTEX_VECTOR_BACKEND=rust` opt-in; unset/`=python` → FROZEN
  zero-writes with honest reason strings (no fake python-children delegate —
  red-team C1/H2). Orchestrator match arms extended
  (`orchestrator.rs:2093-2100`, generic `finish_native_embedding_pass<S>`).

### Phase-03 — reader wire (`cortex-mcp/src/graph/vector_lane.rs`)

- Local arms → `LocalQdrantReader` (per-root process cache) when native;
  `lane_hit_shape` post-strips `text` + drops null `vector` (python
  `PayloadSelectorExclude` parity); no `version` field (documented divergence).
- Sidecar rollback guard (`vector_sidecar.rs`): JSON store + quarantined
  `collection/` → LOUD refusal, never a silent empty pickle serve (H5).
- Reader root derivation unified through `cortex_storage::resolve_storage`
  (review fix — writer/reader share config-file/relative-home/instance
  normalization).

### Phase-04 — parity harness (`scripts/rust_mcp/`)

- `local_twin_parity.py` (twin-capture: JSON engine vs real pickle store via
  the real MCP sidecar worker): max score-diff **2.220e-16** (tolerance 1e-6).
- `local_parity_matrix.py`: 8/8 legs green ×2 consecutive (twin, stale-rename,
  drift, scope, explore-seeds via REAL `merge_hits` probe, tune-env inert,
  guards) + `--rss` leg (118MB store, peak 71MB < 300MB).
- Probes: `cortex-storage/examples/local_vector_probe.rs`,
  `cortex-sync/examples/vector_writer_probe.rs`,
  `cortex-mcp/examples/{vector_lane_probe,sidecar_guard_probe}.rs`.

### Phase-05 — flip + drill

- Flip = one constant: `LOCAL_NATIVE_DEFAULT = true`
  (`cortex-storage/src/qdrant.rs`) in its own commit (`b17f5ba`) —
  `git revert` restores opt-in-only.
- `vector_local_drill.py` 3/3 legs: (A) `=python` pre-quarantine = stale
  sidecar read + frozen writer; (B) post-quarantine = loud refusal (no empty
  serve); (C) native convergence across re-runs.
- Runbook §0c (`docs/cutover-runbook.md`): hatch table, quarantine-FIRST
  re-index recipe (guard enforces the order — plan rev2's "quarantine sau
  re-index" only fits fresh-root), rollback paths.
- Cross-plan: vector-lane plan supersede-note confirmed; python-legacy-cleanup
  disposition A3 updated (code-lane python vector closure → retirable after
  dogfood).

### Review cycles

Code review rev1: 0 Critical / 0 High / 7 Medium @ 8.5 → fix cycle 1 resolved
all 7 (reader-root unification, failed-pass `discard()`→`reload_from_disk`
with pin tests, honest twin-sizes + real merge_hits legs, RSS scope relabel,
Euclid ascending, empty-ids wipe refusal). Re-review: **9.5/10 auto-approve**;
2 actionable Lows polished, remainder recorded in phase04-parity.md.

## Impact

- Local code-vector lane: zero-writes (frozen since phase-08) → native
  ingest + native lock-free search. Users must re-index ONCE per instance
  (quarantine `collection/` → `.legacy-pickle.bak`, then
  `dev sync code --full-scan`) — the legacy data was stale by definition.
- Mind/doc lane and remote lane: untouched (sidecar lives on for mind).
- `CORTEX_VECTOR_BACKEND=python` = rollback (writer frozen + guarded sidecar).
- Risk: **medium** pre-dogfood (data-format ownership swap mitigated by
  twin-parity 2.2e-16, drill, quarantine-first guard, 1-commit revert);
  drops to low after dogfood.

## Decision

- Q1 native JSON store (not qdrant-server/Edge) keeps zero-infra; Q2 atomic
  writer+reader flip (both move together — no read-own-writes gap); D4
  lock-free reader over shared-lease (writer `LOCK_EX|LOCK_NB` unchanged —
  long-lived MCP reader would otherwise block every sync); D5 hatch = frozen
  semantics, never resurrect the dead python-children write path.
- Two-commit flip (impl `1a83091` then flip `b17f5ba`) so revert is 1 commit.
- `embedding_backend: "python-children"` summary label left as-is — the
  sync-plane golden fixture pins it; relabeling belongs to a fixture-refresh
  (noted in phase05-drill.md).

## References

- plan: `plans/260916-1154-native-vector-ingest-local/plan.md` (status: executed)
- reports: `reports/phase01-engine.md`, `reports/phase04-parity.md`, `reports/phase05-drill.md`
- runbook: `docs/cutover-runbook.md` §0c
- commits: `1a83091` (phase-01..04), `b17f5ba` (flip!), `6c47af1` (review fixes),
  `73d86fe` (Low polish), `0340fc0`+`38dc7f2` (plan artifacts)
- gates: cargo test storage+sync+mcp = 251 green / 0 failed; matrix 8/8 ×2;
  drill 3/3; twin 2.2e-16
- pending: dogfood ≥1 cycle hoặc user waiver (reports/phase05-drill.md)
