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
| `parser_type` | Reserved — populated by Phase 03. Currently `None`. |
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
* `code.env.QDRANT_COLLECTION` and `doc.env.QDRANT_COLLECTION` map directly to
  the Qdrant collection fields. Omitting them triggers the naming rule.
* `code.env.GRAPH_PROVIDER` selects the backend (`"falkordb"` or `"neo4j"`).

The loader reads the entire `config/` directory on every call. There is no
in-process cache. Config files are small (one project per file) and the cost
is negligible; revisit if profiling shows a real bottleneck.

---

## Errors

* `ProjectNotRegisteredError` — raised when a caller asks for a project_id
  that does not exist in the config and the env cannot seed an ad-hoc
  project. The exception carries `project_id` and a sorted `known` list of
  every registered project.
* `ValueError` from `with_overrides` — raised when an unknown field name is
  passed (guards against typos).

---

## Out of Scope for Phase 01

* Calling the registry from anywhere outside this module. Phase 02 (graph_mcp
  queries) and Phase 05 (mind_mcp queries) replace the existing env-default
  resolution paths with `resolve_project_targets(...)`. Phase 06 aligns the
  launchers. Phases 03 and 04 close ingest-side `project_id_normalized` gaps.
* Caching the resolved targets. Per-call file read is an accepted trade-off.
