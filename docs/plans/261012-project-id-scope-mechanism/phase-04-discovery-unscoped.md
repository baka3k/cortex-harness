# Phase 04 — Discovery-based unscoped search-all + cap

## Objective

"Search all" stops meaning "whatever the local directory's registry lists" and means "everything the shared stores actually hold" — listing `*_doc` shards live from each probed store, so results are **identical no matter which project directory the server was started from** (operator amendment A1). Hits are attributed per collection; an unbounded fan-out is capped with a visible warning.

## Changes

1. **Unscoped target builder** (`_qdrant_search_targets`, no-scope branch, `doc-tiny/mcp_graph_rag.py:347-355`): keep registered-project pins (declared collections), then **discovery**: for each fallback store (remote `QDRANT_URL` + instance store, existing `_fallback_doc_qdrant_stores()`), `list_collection_names()` and add every shard matching the doc convention (`*_doc`, minus already-pinned names). Instance-store tail keeps `collection_names=None` semantics ("whatever the store has") for non-`*_doc` collections it may hold.
2. **Graph side mirrors it**: `_graph_store_candidates` unscoped branch (`:245-256`) already fans over registered projects' doc graphs — extend with `list_graphs()`-discovered `*_doc` graphs (existence-safe by construction: listed names only). Dedupe by graph name.
3. **Fan-out cap**: `MAX_SCOPE_TARGETS` constant (default 12, env override `SCOPE_MAX_TARGETS` — value is an open question, plan.md) applied to the merged unscoped target list, registry pins first (deterministic order: sorted config files → sorted discovered names). Truncation emits `scope.warnings[]` (`"fan-out capped at 12 targets; N shards not searched"`) and `scope` reports `targets_total` vs `targets_searched`.
4. **Truthful counts**: `per_collection_hits` comes from the actual `present`/hits bookkeeping inside `qdrant_search_entity_payload` (`:419-429` — today's `probed`/`present` sets are computed and discarded; Phase 01 threads them out). `collections_searched` (requested names) keeps contract meaning; discovery detail lives in `scope.discovered_collections`.
5. **Listings are read live every call** (no TTL cache in this phase — REDACTED_PROJECT-scale is sub-millisecond; cap + cache is a measured follow-up if ever needed). Registered pins may keep the existing store-handle cache; discovery listing must not be served from `_qdrant_stores` cache handles created for absent shards (Phase 03 discipline).
6. Registered-project results keep their project filter; discovered shards are searched **without** a project filter but with full per-collection attribution (an unregistered shard's points carry their own `project_id_normalized` payload — attribution is data-driven, so mixed-project answers are visible, never silent).

## Acceptance criteria

- AC1 (directory-independence): same store contents, two different CWDs/config dirs (registry A knows project X only; registry B knows Y only) → identical unscoped `collections_searched`/`per_collection_hits`; both find the unregistered data shard.
- AC2: shard created between two calls is picked up by the next unscoped call (live listing).
- AC3: >12 targets → cap warning + `targets_searched == 12`, registry pins present in the searched set (pins-first order).
- AC4: `per_collection_hits` equals actual hit counts per collection; passages carry collection attribution.
- AC5: no-store/unreachable store in the unscoped tail → warning, remaining stores still searched (partial results never silently dropped).
- AC6: existing registered-pin behavior (filters, single-driver graph fan-out — `tests/test_unified_contract_doc_paths.py:535,598`) unchanged.

## Test scope

- New: `tests/test_unscoped_discovery.py` — AC1..AC6 (two-config-dir fixtures; fake stores with listing call logs; cap via `SCOPE_MAX_TARGETS` monkeypatch).
- Run: `doc-tiny/tests/test_mcp_project_id_optional.py:436` (unscoped fan-out), `tests/test_unified_contract_doc_paths.py`, merge-gate set.
- **Size: L**
