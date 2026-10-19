# link-tiny — tree rules

link-tiny builds and serves doc↔code traceability links. It is a third
pipeline tree parallel to `code-tiny/` and `doc-tiny/`.

## Hard rules

1. **Engine ≠ server.** `link_engine.py` never imports `mcp_server.py`;
   `mcp_server.py` never imports `link_engine.py`. Shared logic lives in
   `link_store.py` / `normalizers.py` / `registry_facade.py`.
2. **One-way dependencies.** No tree outside link-tiny may import link-tiny.
   link-tiny imports the code-side registry through `registry_facade.py`
   (guarded sys.path + try/except, the `doc-tiny/project_contract.py`
   pattern) and `cortex_harness.mcp_contract` for the v2 envelope. The
   harness package is shared ground; other trees are not.
3. **Append-only.** `links.jsonl` records are never rewritten or removed in
   place. Drift appends copies with `status: "stale"`. Deletes are logical.
4. **Provenance is mandatory.** Every record carries `provenance` and
   `score`. Records missing either are rejected at the store boundary.
5. **Stable identity, never graph-internal ids.** Code side is
   `(path, name, start_line, end_line, parser_type, source_sha)`; doc side
   is `(doc_id, paragraph_id, source_sha)`. Paths are stored POSIX-normalized.
6. **Read-only access to foreign stores.** The engine opens graphs through
   `code-tiny/tools/graph/read_only_opener.py` — zero `StorageLease`
   acquisition, no writes, no Qdrant access. `link_mcp` reads
   `links.jsonl` from disk and delegates content over HTTP; it never opens
   a graph.
7. **Write-path split.** Only `dev sync link` (the engine, via
   `link_store`) writes link data. Nothing else in the repo writes or
   rewrites link artifacts, and nothing in `manifest.json` describes them.
8. **Envelope v2, registry-first.** Every tool result is produced by
   `cortex_harness.mcp_contract.result_envelope_json`; every project-scoped
   call resolves through the registry facade (`resolve_project_targets`).
   Graph/collection names come from the registry (or, only in the
   standalone fallback when the code registry is not importable, the
   naming contract + env seeding — never as a primary source).
9. **No fuzzy matching in Phase 1.** Provenance is `exact-name` only;
   semantic matching is gated on the coverage telemetry in `state.json`
   (see `docs/LINK_CONTRACT.md`).
