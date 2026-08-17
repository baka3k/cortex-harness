# Red Team Findings & Resolutions

**Date:** 2026-08-17
**Reviewer:** Automated Red Team (adversarial analysis)
**Verdict:** 5 Critical, 6 High, 6 Medium — all resolved below

## Critical Issues → Resolutions

### C1. `create_storage()` missing `project_root` argument ✅ RESOLVED

**Fix:** `create_storage()` requires `project_root` parameter:
```python
def create_storage(
    targets: ProjectTargets,
    *,
    project_root: Path = Path.cwd(),
    resolved: Optional[ResolvedStorage] = None,
) -> StorageFactory:
```
**Applied to:** Phase 03

---

### C2. Actual MCP wrapper functions reject remote URLs ✅ RESOLVED

**Finding:** `get_code_qdrant_store()` (`code-tiny/tools/common/local_qdrant.py`)
and `get_document_qdrant_store()` (`doc-tiny/doc_local_qdrant.py`) actively raise
`RemoteQdrantUnsupportedError` when the locator looks like an HTTP URL.

**Fix:** Refactor these wrapper functions to use the factory internally:
- Remove `RemoteQdrantUnsupportedError` rejection.
- Accept `project_id` parameter, resolve backend via factory.
- Keep function signatures backward-compatible for callers.

**Applied to:** Phase 04 — added to file-change list.

---

### C3. `unified_mcp.py` integration mischaracterized ✅ RESOLVED

**Finding:** `unified_mcp.py` delegates to backend modules (`cplus_backend`,
`fast_backend`, `android_backend`) via `_load_module()`. Each backend module
independently creates its own driver and Qdrant store.

**Fix:** Updated Phase 04 file-change list to include:
- `code-tiny/mcp/fastmcp_server.py`
- `code-tiny/mcp/cplus/cplus_mcp.py`
- `code-tiny/mcp/java/java_mcp.py`
- `code-tiny/mcp/android/android_mcp.py`
- `code-tiny/mcp/services/impact_service.py`
- `code-tiny/mcp/services/explore_service.py`

**Applied to:** Phase 04

---

### C4. Remote client cache ignores API key differences ✅ RESOLVED

**Fix:** Cache key changed from `url` to `(url, api_key)` tuple:
```python
_remote_clients: dict[tuple[str, Optional[str]], QdrantClient] = {}
```
**Applied to:** Phase 02

---

### C5. Mixed backend mode raises on missing component ✅ RESOLVED

**Fix:** When `storage_backend == "remote"` but a specific component URI is None,
fall back to local for that component instead of raising:
```python
def get_falkordb_driver(self, graph_name):
    if self._mode == BackendMode.REMOTE and self._remote and self._remote.falkordb_uri:
        return FalkorDBDriver(uri=self._remote.falkordb_uri, ...)
    # Fall back to local
    return FalkorDBDriver(path=str(self._resolved.falkordb_path), ...)
```
**Applied to:** Phase 03

---

## High Risks → Resolutions

### H1. FalkorDBDriver DeprecationWarning on remote construction ✅ RESOLVED

**Fix:** Factory constructs remote FalkorDBDriver with `_suppress_deprecation=True`
kwarg. `FalkorDBDriver.__init__()` checks this flag before emitting warning.

### H2. `resolve_storage()` rejects legacy remote keys unconditionally ✅ RESOLVED

**Fix:** Drop the §1.6 legacy-key behavior change. Users must use the new `remote`
section. Simpler, avoids ambiguity. Legacy keys remain rejected.

### H3. `doc-tiny/graph_store.py` missing from file-change list ✅ RESOLVED

**Fix:** Added to Phase 04 file-change list.

### H4. Factory doesn't pass owner_id/instance_id to local FalkorDBDriver ✅ RESOLVED

**Fix:** Pass from `ResolvedStorage`:
```python
FalkorDBDriver(
    path=path, graph=graph_name,
    owner_id=self._resolved.code_owner_id,
    instance_id=self._resolved.instance_id,
)
```

### H5. No remote client lifecycle management ✅ RESOLVED

**Fix:** Add `atexit.register(reset_remote_clients)` and `reset_remote_clients()`
function mirroring the local pattern.

### H6. `_resolve_targets()` doesn't pass new fields to ProjectTargets ✅ RESOLVED

**Fix:** Add explicit handling in `_resolve_targets()`:
```python
storage_backend = match.get("storage_backend", "local")
remote_config = match.get("remote_config")
return ProjectTargets(..., storage_backend=storage_backend, remote_config=remote_config)
```

---

## Medium Risks → Resolutions

### M1. No Protocol for interface conformance → **Accept risk**

Lightweight approach: add type hints using `Union[LocalQdrantStore, RemoteQdrantStore]`
as `QdrantStore` type alias. Protocol can be added later if drift occurs.

### M2. `mcp_graph_rag.py` process-global singletons → **Fix in Phase 04**

Replace module-level singletons with per-project caching keyed by project_id.

### M3. `GraphDriverFactory` constructs from env → **Add to Phase 04 scope**

Add `code-tiny/tools/graph/core/factory.py` to file-change list.

### M4. BackendConnectionError inheritance → **Change to RuntimeError**

`BackendConnectionError` inherits from `RuntimeError` instead of `ConnectionError`.

### M5. `resolve_storage()` creates local paths in remote mode → **Accept trade-off**

Local paths are created but unused. This is acceptable — directories are lightweight
and `resolve_storage()` serves as the canonical config resolution entry point.

### M6. Phase 04 file list incomplete → **Expanded in Phase 04**

Full grep-based inventory added to Phase 04 acceptance criteria.

---

## Validation Decisions

| Question | Decision |
|----------|----------|
| Failover strategy | **Fail fast** with clear error, no retry |
| Credential management | **Plaintext in config JSON**, file should be `.gitignore`d |
| Cross-backend search | **Search each project separately, merge results** at application layer |
| Per-component backend | **Not now** — single `storage_backend` per project, fallback to local for missing component |
| Emergency rollback | Add `CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1` env var for force-local override |

---

## Open Questions for Implementation

1. **FalkorDB health check:** Use `redis-py` `PING` command via `falkordb` package.
2. **Empty collection warning:** Factory logs warning if remote backend has no data
   for a project that previously had local data (heuristic: collection exists but is empty).
3. **Config hot-reload:** `ProjectRegistry` reads config on every call (no cache),
   so changes take effect immediately for new requests. Module-level singletons
   in `mcp_graph_rag.py` must be refactored (see M2).
