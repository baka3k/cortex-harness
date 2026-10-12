# Phase 02 — Registry facade unification + dual-key fix

## Objective

One parser for `.cortext-harness/config/*.json`. Doc-side `project_contract` becomes a delegating facade over code-side `tools.common.project_registry` (the import-guard precedent already exists: `doc_local_qdrant.py:55-65`, `mcp_graph_rag.py:49-63`). The doc-collection env key is read with explicit dual-key precedence, killing the live drift where `QDRANT_COLLECTION_DOC` (what dev.json writes) is read by **neither** resolver.

## Changes

1. **Fix the dual key in code-side `_doc_qdrant()`** (`code-tiny/tools/common/project_registry.py:387`): precedence `doc_env["QDRANT_COLLECTION_DOC"]` → `doc_env["QDRANT_COLLECTION"]` (legacy reader key) → `{project_id}_doc`. Mirror the same order in the doc-side facade. Both keys documented; `QDRANT_COLLECTION` marked legacy-compat in docs (Phase 06).
2. **`doc-tiny/project_contract.py` delegates, keeps its exports**: `resolve_project_targets`, `resolve_doc_candidates`, `list_registered_projects`, `ProjectTargets`, `ProjectNotRegisteredError`, `DuplicateProjectRegistrationError` remain importable from `project_contract` (tests patch these symbols directly — `tests/test_unified_contract_doc_paths.py:105,265,582`, `doc-tiny/tests/test_mcp_project_id_optional.py:170-171` — so the facade must preserve names and semantics). Bodies delegate to `tools.common.project_registry` behind the existing try/ImportError guard; standalone fallback keeps the current doc-side parsing (fixing its key order too).
3. **Canonicalization stays**: case variants collapse to the registered form (`project_contract.py:206-208` behavior preserved by delegation — verify code-side `_resolve_targets` already canonicalizes; it does, `:312-320`).
4. **Dead code**: remove unreachable duplicate return `doc-tiny/mcp_graph_rag.py:296-297`.
5. Prefix expansion (`resolve_doc_candidates`) stays behavior-identical this phase (R4 removal deferred per plan.md non-goals).

## Acceptance criteria

- AC1: fixture config where `doc.env` sets **only** `QDRANT_COLLECTION_DOC: "custom_coll"` → both `resolve_project_targets` (code-side) and doc-side facade resolve `doc_qdrant_collection="custom_coll"` (today: code-side → `custom_coll`… actually today code-side reads `QDRANT_COLLECTION` too — after fix both read the new key first).
- AC2: fixture with legacy `QDRANT_COLLECTION: "legacy_coll"` only → resolves `legacy_coll` (no regression).
- AC3: fixture with both keys → `QDRANT_COLLECTION_DOC` wins; a warning is not required but resolution is deterministic.
- AC4: agreement test — for a corpus of fixture configs (incl. malformed JSON file in dir), `project_contract.resolve_project_targets` and `project_registry.resolve_project_targets` return equal `ProjectTargets` for every registered id, and `list_registered_projects` matches.
- AC5: all existing tests that patch doc-side symbols still pass unmodified (facade contract).

## Test scope

- New: `tests/test_registry_facade_agreement.py` — AC1..AC4 (fixture configs under tmp_path; both trees on pytest path via `pyproject.toml:40-42`).
- Run: `doc-tiny/tests/test_project_contract.py`, `code-tiny/tools/common/test_project_registry.py`, `tests/test_qdrant_project_scope.py`, merge-gate set.
- **Size: M**
