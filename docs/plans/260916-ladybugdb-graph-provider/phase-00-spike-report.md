# Phase 0 Spike Report — LadybugDB Graph Provider

> **Date:** 2026-09-16
> **Verdict: GO** — all three critical blockers resolved; performance acceptable.

## 0a. Package Identity (B1) — RESOLVED

- `ladybug==0.20.4` exists on PyPI (LadybugDB, `ladybugdb.com`, successor of KuzuDB).
  Latest stable as of 2026-09-16; `requires_python >=3.10,<3.15` — project venv (3.12.14) compatible.
- Verified API surface: `ladybug.Database(path, read_only=False, ...)`, `ladybug.Connection(db)`,
  `conn.execute(cypher, params) -> QueryResult` with `has_next()/get_next()/get_column_names()`.
- `Database(read_only=True)` supported → sibling-instance fan-out can open read-only catalogs.
- **Decision: new `GraphProvider.LADYBUG = "ladybug"`** (aliases `ladybug`, `ladybugdb`, `ladybug-db`).
  `GraphProvider.KUZU` stays as placeholder. Rationale: LadybugDB is a fork with breaking changes;
  reusing KUZU would misidentify the dialect.
- Embedded engine requires the parent directory to exist before `lb.Database(path)` — driver must
  `mkdir(parents=True, exist_ok=True)` (same as FalkorDBLite).

## 0b. Property Inventory (B2) — RESOLVED via hybrid schema (Option C)

Complete inventory collected (see writer scan). Shape of the data:

- **Fixed-column writers** (`language_writer.py` `*_full` methods, `operations/*`, doc-tiny
  Document/Entity): stable per-label column lists. Core columns recur across labels:
  `id, name, file_path, start_line, end_line, code, comment, summary, note, project_id,
  project_id_normalized, project_name, language, repo, build_system, updated_at`.
- **Dynamic writers** (spring/mybatis/aspnet/servlet-jsp/topology/web-framework/database-schema,
  CallSite/BuildConfiguration/SemanticCoverage, `SET p += $props` in doc-tiny): heterogeneous row
  dicts → JSON spill column `_properties STRING`.
- Manifest `CODE_GRAPH_SCHEMA` (~150 labels) provides identity keys only; PK overrides needed for
  `Project(project_id)`, `Repository(name)`, `Workflow(workflow_id)`,
  `BuildConfiguration(config_fingerprint)`, `CallSite(site_id)`, `SemanticCoverage(fingerprint)`.
- Doc labels: `Document(id PK)`, `Entity(id PK)`, `Paragraph` (composite merge key
  `{source_id, paragraph_id}` → **no PK**, single-writer lease makes prop-merge safe).

Schema module: `code-tiny/tools/graph/schema/ladybug_schema.py` compiles
`CREATE NODE/REL TABLE IF NOT EXISTS` DDL. Type map by property name (INT64 lines/bytes/count,
DOUBLE confidence/scores, BOOL flags, STRING default). Multi-endpoint rel tables:
`CREATE REL TABLE R(FROM A TO B, FROM C TO D, props...)` — verified.

## 0c. Cypher Compat Matrix (verified on 0.20.4)

| Construct | Status | Driver strategy |
|---|---|---|
| `CREATE NODE/REL TABLE IF NOT EXISTS` | ✅ | schema preflight (via `create_indexes`/`inspect_indexes` hooks) |
| `MERGE` + `ON CREATE SET` / `ON MATCH SET` | ✅ | — |
| `STARTS WITH` + `$param` | ✅ | — |
| `($project_id IS NULL OR n.x STARTS WITH $project_id_normalized)` | ✅ **native** | none — works when each param has one consistent type (which the codebase does) |
| `$end` as param/alias name | ❌ reserved word | driver renames reserved-word params (`$end`→`$__lb_end`) in text+params |
| `[t IN $list \| toLower(t)]` | ❌ | rewrite → `WITH $list AS list` + client-side lowercase of param |
| `any()/NOT any()` over param list | ✅ | — |
| `SET n += row` / `SET n += coalesce(row.props,{})` / `SET p += $props` | ❌ (26+ occurrences) | rewrite → explicit typed-column SETs + `_properties = row.__spill`; params get `__spill` JSON |
| `SET n = node` / `SET r = edge.properties` (base `batch_write_nodes/edges`) | ❌ | override both methods in driver with schema-aware UNWIND+MERGE+explicit SET |
| `CALL { WITH row MATCH ... RETURN v LIMIT 1 }` | ❌ (4 occurrences, 2 functions) | rewrite → flat `MATCH ...` (cardinality-guard equivalence verified) |
| `FOREACH (x IN xs \| DETACH DELETE x)` | ❌ (4 cleanup queries) | rewrite → `UNWIND xs AS x ... DETACH DELETE x` |
| `datetime()` | ❌ (46 occurrences) | param substitution `$__ladybug_now` (same as FalkorDB driver) |
| `labels(n)` / `labels(n)[0]` | ⚠️ list works, indexed returns `''` | rewrite `labels(x)[0]` → `label(x)` |
| `label(n)`, `properties(n)` | ✅ | — |
| `{.prop}` map projection | ❌ | 0 occurrences in scope |
| `REMOVE n.prop` | n/a | 0 occurrences; `SET n.prop = NULL` verified |
| var-length `[:R*1..k]`, path `p=`, `length(p)`, undirected | ✅ | — |
| `MATCH (n)` unlabeled cross-table scan | ✅ | — |
| `CALL show_tables()` | ✅ | catalog inspection (list_databases / inspect_indexes) |
| `collect()`, `IN list`, `CASE WHEN`, `coalesce`, `CONTAINS`, `size()`, `OPTIONAL MATCH`, `ORDER BY/SKIP/LIMIT $param` | ✅ | — |
| Value shapes | nodes `{_ID,_LABEL,cols...}`, rels `{_SRC,_DST,_LABEL,_ID,cols...}`, paths `{_NODES,_RELS}` | driver normalizes to FalkorDB-compatible dicts (merge `_properties` JSON, `_LABEL`→`_type` etc.) |
| Result columns | `RETURN n.id` → key `"n.id"`, aliases preserved | matches FalkorDB header behavior |

**False alarm during spike:** consecutive `MATCH ... WHERE $p ... MATCH` was initially believed
broken — actually caused solely by `$end` being a reserved word. Clean retest: params in any
clause position parse fine (non-reserved names).

## 0d. Benchmark (macOS arm64, embedded, batch 500)

| Metric | LadybugDB | FalkorDBLite | Ratio |
|---|---|---|---|
| Open / schema DDL | 68 ms | 2,083 ms | ladybug ~30× |
| Node upsert (MERGE) | 15,057 rows/s | 4,982 rows/s | ladybug ~3× |
| Edge upsert (MATCH+MERGE) | 23,518 rows/s | 1,730 rows/s | ladybug ~13.6× |
| Point lookup by PK | 0.6 ms | 0.9 ms | comparable |
| Unscoped count (5K nodes) | 3.9 ms | 0.4 ms | falkor faster |
| Traversal `*1..3` | 6.5 ms | 0.9 ms | falkor faster |
| Scoped edge query LIMIT 100 | 1.4 ms | 0.5 ms | falkor faster |

Conclusion: columnar engine wins batch ingest decisively; point/traversal reads are single-digit
ms slower but acceptable for MCP query workloads. No mitigation needed for v1.

## Go/No-Go

**GO.** All blockers have verified solutions:

1. B1 → real package, verified API, `LADYBUG` enum decision locked.
2. B2 → hybrid typed-columns + `_properties` JSON spill; schema compiler fed by inventory.
3. B3 → CALL-subquery flat rewrite verified byte-for-byte semantics (count-mismatch behavior
   preserved — unmatched rows simply don't produce a MERGE).
4. F6 → benchmark acceptable.

Residual risks (accepted): `Paragraph` PK-less merge (single-writer lease protects); unlabeled
`MATCH (n)` scans across ~150 tables may be slow on very large graphs (point lookups by id stay
PK-indexed); no secondary indexes (PK only) — `find_nodes_by_ids` uses `IN` scan (columnar scans
are fast; verified single-digit ms at 5K nodes).
