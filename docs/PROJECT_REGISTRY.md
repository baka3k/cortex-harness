---

# Project Registry & Naming Contract

Single source of truth for `project_id` → storage targets. Both `graph_mcp`
(`code-tiny`) and `mind_mcp` (`doc-tiny`) resolve every project-scoped
operation through `resolve_project_targets(project_id)`.

---

## Public API

```python
from tools.common.project_registry import (
    ProjectTargets,
    ProjectNotRegisteredError,
    list_registered_projects,
    resolve_project_targets,
    with_overrides,
)
```

`ProjectTargets` is a frozen dataclass with these fields:

| Field | Meaning |
| --- | --- |
| `project_id` | Canonical raw identifier (the value from the config). Stable across case variants of the same logical project. |
| `project_id_normalized` | `str(value).strip().casefold()`. Used as the comparison key everywhere. |
| `code_graph` | FalkorDB graph name (or Neo4j DB name) for the code side. |
| `code_qdrant_collection` | Qdrant collection for code embeddings. |
| `doc_graph` | FalkorDB graph name for the doc side. Disjoint label space from `code_graph`. |
| `doc_qdrant_collection` | Qdrant collection for doc paragraph vectors. |
| `parser_type` | Parser selected from `project.parser_type`, `project.parser`, or `code.env.PARSER_TYPE`; a per-call override takes precedence. |
| `provider` | Graph backend name (`"falkordb"` or `"neo4j"`). |
| `source` | Diagnostic only. `"registry"` when the project was found in config, `"env+defaults"` when seeded from env without a registry entry. Callers must not branch on this. |

---

## Naming Contract

Applied when a config entry does not specify the field:

| Concept | Rule |
| --- | --- |
| `project_id` raw | Preserved as identity/display; canonicalised to the registered form when a config entry matches. |
| `project_id_normalized` | `str(value).strip().casefold()`. Comparison key only. |
| Code graph | `== project_id` |
| Code Qdrant collection | `== project_id` |
| Doc graph | `== f"{project_id}_doc"` (separate graph; disjoint labels) |
| Doc Qdrant collection | `== f"{project_id}_doc"` |
| Point IDs / symbol IDs | Unchanged — raw `project_id` stays inside identity. |

Resolution precedence (lowest to highest): naming rule, env vars, config-file
values, per-call overrides. Env vars only contribute when no config file
describes any project — this stops a stray `NEO4J_DB` on the host from
silently shadowing an explicitly registered project.

---

## Lookup Semantics

* `resolve_project_targets("cortex")`,
  `resolve_project_targets("CORTEX")`, and
  `resolve_project_targets("CORText")` all return identical `ProjectTargets`.
  The lookup key is the casefold of the input.
* Case variants that are not casefold-equivalent are different projects.
  For example, `"CorTex".casefold()` is `"cortex"`, NOT `"cortext"` —
  the "t" and "T" collapse in Unicode case folding. Pick a spelling whose
  casefold round-trips identically.
* Whitespace around the input is trimmed. `None` or empty input raises
  `ProjectNotRegisteredError`.
* Two registry entries whose identifiers collapse to the same casefold key are
  rejected with `DuplicateProjectRegistrationError`; resolution never depends
  on filesystem iteration order.

> **Query-side scope rules (READ paths) live in
> [`docs/PROJECT_ID_QUERY_RULES.md`](./PROJECT_ID_QUERY_RULES.md)** — omit
> `project_id` to search every project; scoped queries are case-insensitive
> AND prefix/LIKE (`bank` matches `bank_android`, `bank_Cplus`). Read paths
> resolve through `resolve_project_scope_candidates` and the
> `STARTS WITH $project_id_normalized` Cypher convention; write/sync/delete
> paths keep exact `resolve_project_targets` equality.

---

## Read-Path Scope Resolution

```python
from tools.common.project_registry import resolve_project_scope_candidates
```

`resolve_project_scope_candidates(project_id, *, config_dir=None)` returns the
ordered list of matching `ProjectTargets` for a **read** query:

1. Exact case-insensitive match → one target.
2. Otherwise every registered project whose casefold id starts with the query
   id (`bank` → `bank_android`, `bank_Cplus`) — the scanner names per-target
   projects `{stem}_{platform}` off a shared stem.
3. No registry match → `[]`; read callers fall back to the raw id via the
   naming convention — but only after an **existence gate** (2026-10): the
   `{project_id}_doc` convention shard is touched only when FalkorDB's
   `GRAPH.LIST` or a Qdrant listing actually shows it. When nothing shows
   it, the mind query degrades to `fallback_all` with a warning (or raises
   under `strict_scope=true`); see `docs/PROJECT_ID_QUERY_RULES.md` R6a.
   Blank/`None` input also yields `[]` (unscoped query).

`resolve_project_targets` remains the exact-match resolver for write paths and
storage provisioning.

---

## Config File Format

Registry input: every `*.json` file under `.cortext-harness/config/`.
The loader reuses the existing `dev.json` shape:

```json
{
  "active": true,
  "project": {"code": "cortext", "name": "cortext"},
  "code": {"env": {"FALKORDB_GRAPH": "cortext", "QDRANT_COLLECTION": "cortext"}},
  "doc":  {"env": {"FALKORDB_GRAPH": "cortext_doc", "QDRANT_COLLECTION": "cortext_doc"}}
}
```

* `project.code` is the registry key (used as the source of truth for the
  raw `project_id`). The current `dev.json` uses `"cortext"` as `project.code`
  even though the human-readable name is "Cortex Harness" — this is intentional
  to keep shard names short.
* `code.env.FALKORDB_GRAPH` and `doc.env.FALKORDB_GRAPH` map directly to the
  graph fields on `ProjectTargets`. Omitting them triggers the naming rule.
* `doc.env.QDRANT_COLLECTION_DOC` (primary) or `doc.env.QDRANT_COLLECTION`
  (legacy, still honored) names the doc Qdrant collection; when both are
  set, `QDRANT_COLLECTION_DOC` wins. `code.env.QDRANT_COLLECTION` maps to
  the code collection. Omitting them triggers the naming rule.
* `code.env.GRAPH_PROVIDER` selects the backend (`"falkordb"`, `"neo4j"`, or the embedded `"ladybug"` option).

The loader reads the entire `config/` directory on every call. There is no
in-process cache. Config files are small (one project per file) and the cost
is negligible; revisit if profiling shows a real bottleneck.

### Doc-side facade (2026-10)

`doc-tiny/project_contract.py` is a **delegating facade** over this
registry: the doc side keeps its own config parser and exact/prefix
candidate matching (the registry source stays patchable, and doc-tiny
still runs standalone without `code-tiny` on `sys.path`), but matched
entries are resolved through `project_registry._resolve_targets`, so the
naming contract and the dual-key precedence exist in exactly one place.
Both trees return equal `ProjectTargets` for the same config
(`tests/test_registry_facade_agreement.py` pins this).

### Registry is per-directory (by design)

The registry is discovered by walking up from the current working
directory (`CORTEX_HARNESS_CONFIG_PATH` overrides), and many projects each
carry their own `dev.json` while the graph/Qdrant stores are shared.
Consequences:

* A shard can be **visible on a shared store but absent from this
  directory's registry** — that is normal, not corruption. Unscoped
  (search-all) queries are discovery-based and find it regardless;
  `dev doctor`'s **scope drift** report lists such shards (`[doctor][warn]`)
  so they can be registered or cleaned up deliberately.
* Starting the MCP servers from different directories must not change
  unscoped results — this is pinned by `tests/test_unscoped_discovery.py`.

---

## Errors

* `ProjectNotRegisteredError` — raised when a caller asks for a project_id
  that does not exist in the config and the env cannot seed an ad-hoc
  project. The exception carries `project_id` and a sorted `known` list of
  every registered project.
* `DuplicateProjectRegistrationError` — raised when two config files register
  identifiers that collide after trimming and case folding.
* `ValueError` from `with_overrides` — raised when an unknown field name is
  passed (guards against typos).

---

## Out of Scope for Phase 01

* Calling the registry from anywhere outside this module. Phase 02 (graph_mcp
  queries) and Phase 05 (mind_mcp queries) replace the existing env-default
  resolution paths with `resolve_project_targets(...)`. Phase 06 aligns the
  launchers. Phases 03 and 04 close ingest-side `project_id_normalized` gaps.
* Caching the resolved targets. Per-call file read is an accepted trade-off.
