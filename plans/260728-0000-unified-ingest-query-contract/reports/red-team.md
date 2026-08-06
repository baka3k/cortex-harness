# Red-Team Report — Unified Ingest/Query Contract

**Reviewer:** adversarial pass on `plans/260728-0000-unified-ingest-query-contract/`
**Date:** 2026-07-28
**Verdict:** **GO** — all 3 critical risks (C1, C2, C3) resolved via Validation
Interview decisions. Plan is implementable as written.

---

## Critical Risks (must fix before craft)

### C1. Doc entity backfill is underspecified and can silently lose relationships

**Status: RESOLVED** — Validation Interview chose "Drop + re-ingest". No
in-place entity-split algorithm is needed. Phase 04 now wipes the old doc graph
and re-ingests from source docs with `project_id` stamped on every node/payload.
The original analysis is retained below for context.

---

**Original analysis (now moot):**

The plan changes Entity ID from `uuid5(ent_type::name_norm)` to
`uuid5(project_id_norm::ent_type::name_norm)`. The backfill script is described
as: "derive `project_id` from `source_id` prefix ... `--dry-run` reports counts
and collisions; `--apply` writes `project_id_normalized`."

This **does not specify** what happens to the relationships of an existing
globally-merged Entity node when the project split occurs. Concretely: if
Entity `E_global` is referenced by `HAS_ENTITY` edges from Paragraphs in
projects A, B, and C (because today entities merge globally), the backfill
must:

1. Create three new per-project entities `E_A`, `E_B`, `E_C`.
2. Re-link every `HAS_ENTITY` edge from a Paragraph to the correct new entity
   based on the Paragraph's `project_id`.
3. Copy over `HAS_ENTITY` relationship properties (confidence, span) onto the
   new edges.
4. Decide what to do with `RELATED` edges between the old `E_global` and other
   global entities — those also need splitting per project pair, or explicitly
   dropping with user consent.
5. Only delete `E_global` once it has no remaining edges.

The plan's "dry-run reports collisions" wording does not cover this. A naïve
implementation that just stamps `project_id_normalized` on `E_global` will
leave one project "owning" a node that is still referenced by paragraphs from
other projects, breaking query isolation silently.

**Required fix.** Phase 04 must include an explicit "Entity split algorithm"
section with the 5 steps above, plus a test that ingests 3 projects sharing
one entity name and verifies all 3 new entities own only their project's
paragraph edges after backfill.

### C2. `dev.json` already couples code+doc graph — registry rule conflicts with current data

**Status: RESOLVED** — Validation Interview chose "code and doc tách graph
riêng". Naming rule is now `doc_graph == f"{project_id}_doc"` (separate from
code). Phase 06 updates `dev.json` accordingly. The original analysis is
retained below for context.

---

**Original analysis (now moot):**

**Location:** Plan Target Architecture (Naming Contract) + dev.json evidence.

**Problem.** The naming contract says `doc_qdrant_collection =
"{project_id}_doc"`. The current `dev.json` sets `doc.env.QDRANT_COLLECTION =
"cortext_doc"` (correct) **but** also sets `doc.env.FALKORDB_GRAPH = "cortext"`
(same as code). The plan explicitly blesses this: "doc_graph == project_id
(shared with code; disjoint labels)".

Now consider the plan's claim in Phase 04: "Entity nodes ... sharing one doc
graph cannot be queried per project today" and the fix "per-project entity
merge keys so two projects sharing one doc graph do not collapse entities".

But if `doc_graph == project_id` and projects are isolated by graph, then two
distinct projects never share a doc graph — they have graphs `"projA"` and
`"projB"` respectively. The "two projects sharing one doc graph" scenario is
either (a) impossible by the naming rule, or (b) possible only as an explicit
override that the plan does not document.

This is an **internal contradiction**: either per-project sharding means each
project has its own graph (in which case the per-project Entity ID change is
defensive belt-and-suspenders, not strictly required), or sharing is allowed
(in which case the naming rule `doc_graph == project_id` needs an override
mechanism and the plan should say so).

**Required fix.** Clarify in Phase 01 whether `doc_graph` follows
`code_graph == project_id` strictly, or whether code+doc of the same project
intentionally share a graph (which is what dev.json demonstrates). Then state
explicitly whether the per-project Entity ID change is (a) required for
isolation or (b) a defensive measure for the shared-graph override case.
Either answer is fine; the contradiction must be resolved.

### C3. No plan for the harness/orchestrator contract — silent break risk

**Location:** Not mentioned anywhere in plan or phases.

**Problem.** `harness/scripts/orchestrator.py:235-265` and
`harness/scripts/context_selector.py:563-625` call `graph_mcp` and `mind_mcp`
via URL with specific tool names and arg shapes. If this plan:

- adds a required `project_id` arg to every project-scoped tool on both
  servers, and
- changes the default `collection`/graph behavior from env-derived to
  registry-derived,

then any harness code or external client that currently calls these tools
without `project_id` will start receiving `ProjectScopeRequiredError` instead
of data. The plan does not mention auditing the harness layer at all.

The same applies to `harness/scripts/context_selector.py` which builds context
once per session using these MCP endpoints — it must now pass `project_id`
through, or the session-context flow breaks.

**Update after `search_full` flag addition.** The plan now adds a `search_full`
escape hatch that lets callers opt into a cross-project query without
`project_id`. This materially reduces the break risk: existing harness callers
that want unscoped results can pass `search_full=true` rather than being forced
to thread `project_id` everywhere. However the audit step is still required —
each harness call site must be classified as (a) project-scoped (pass
`project_id`), (b) intentionally cross-project (pass `search_full=true`), or
(c) ambiguous (default to `search_full=true` for backward compat during
transition). The plan should still add this audit step to Phases 02 and 05 and
list `harness/scripts/orchestrator.py` + `harness/scripts/context_selector.py`
in Expected File Areas.

**Required fix.** Add to Phase 02 and Phase 05 an explicit step: "Audit
`harness/scripts/*` and classify each MCP call site as project-scoped or
cross-project; add the appropriate arg. `search_full=true` is the recommended
backward-compat path for ambiguous call sites." Add
`harness/scripts/orchestrator.py` and `harness/scripts/context_selector.py` to
Expected File Areas.

---

## Notable Risks (should fix before craft)

### N1. Registry has no caching story — per-call file read is a latency tax

**Location:** Phase 01.

Every MCP tool now calls `resolve_project_targets(project_id)` on every
request. Phase 01 does not mention caching. Reading `.cortext-harness/config/
*.json` from disk on every MCP call adds latency, especially for the doc
server which is already embedding-heavy.

**Recommendation.** Specify an in-memory cache invalidated by file mtime
(`watchdog` or stat-on-miss) or a TTL. Keep the API synchronous; cache is an
implementation detail. Note this in Phase 01 deliverables.

### N2. `activate_project` precedence is ambiguous in edge cases

**Status: RESOLVED** — Validation Interview chose "Bỏ hẳn active_project".
`activate_project` and `active_project` are removed entirely. Every call must
carry `project_id` or `search_full=true`. No precedence ambiguity remains.

---

**Original analysis (now moot):**

**Location:** Phase 02.

The plan says "per-call wins; `activate_project` is optional default." But
consider:

- `activate_project("projA")` was called, then the server received
  `tool(project_id=None)` — should use A. ✓ (plan covers this)
- `activate_project("projA")` was called, then the server received
  `tool(project_id="")` — empty string. Should this be treated as None (use
  A) or as an explicit error? Plan does not say.
- `activate_project("projA")` was called, then `activate_project(None)` — is
  that a valid "clear default" operation? Plan does not say.
- Multi-session: if the server process is shared across MCP clients, one
  client's `activate_project` affects another's default. Plan acknowledges
  state is in-process but does not address multi-tenant safety.

**Recommendation.** Document the precedence table explicitly:
`explicit project_id arg` > `active_project default` > `error`. Treat
`project_id=""` as `None` (consistent with `normalize_project_id`). Document
that `activate_project(None)` clears the default. Add a note that the in-process
default is single-tenant — multi-client deployments should always pass
`project_id` explicitly.

### N3. Scoped reset is destructive and lacks a guardrail

**Location:** Phase 04 (`0_reset_all.py --project-id`) and Phase 03 (code
equivalent if added).

A `--project-id` reset that deletes only that project's nodes/points is
safer than a full wipe, but still destructive and easy to invoke by mistake
(e.g. typos: `--project-id corteht` creates an empty-graph no-op, but
`--project-id cortex` wipes real data).

**Recommendation.** Require `--confirm` or `--force` for any scoped reset
that matches an existing project; print "about to delete N nodes and M points
from graph X / collection Y, proceed? (y/N)" otherwise. Add `--dry-run` that
prints counts without deleting. Same pattern for any code-side scoped reset.

### N4. Fixture tests for Phase 07 may inherit the long-embedding problem

**Location:** Phase 07 fixture projects.

Plan 260719-01 explicitly skipped full embedding sync for acceptance and used
recording-driver + pure-extraction fixtures instead. Phase 07 here says
"ingest `proj_alpha` and `proj_beta` through both `dev sync code` and
`dev sync doc`" — if those ingests run real embeddings, the test suite will
be slow or flaky on CI. The plan does not say whether embedding is mocked,
pinned, or real.

**Recommendation.** Specify in Phase 07: fixture ingests use a pinned/mock
embedding provider OR a tiny model; the live smoke (`scripts/smoke_unified_
contract.py`) is the only place real embeddings run, and it is opt-in.

### N5. Migration plan is `in_progress` — concurrency hazard on shared files

**Location:** Cross-plan dependency section.

At review time this plan was blocked by `neo4j-to-falkordb-migration`; that
migration and this contract are now completed together with the local-storage
cutover.
Both plans touch: `code-tiny/scripts/setup_constraints.py`,
`code-tiny/tools/graph/writer/language_writer.py` (via provider-neutral
contract), `code-tiny/mcp/unified_mcp.py`, and `doc-tiny/graph_store.py`. If
implementation starts before migration completes, merge conflicts are likely
on Phase 03 (graph_mcp ingest) and Phase 06 (launcher/config).

**Recommendation.** Phase 03's `setup_constraints.py` rewrite and Phase 06's
`harness_config.py` rewrite should be sequenced strictly after the migration
plan's Phase 03 (schema migration) and Phase 04 (cypher/service migration)
land. Phase 01, 02, 04, 05 can proceed in parallel since they touch
non-overlapping files. Note this ordering in the plan.

---

## Minor issues (nice to fix, not blocking)

### M1. Per-project Entity ID loses cross-document entity merge within the same project

Current global merge `uuid5(ent_type::name_norm)` is actually useful for RAG:
the same entity mentioned in two documents of the same project is one node,
enabling cross-document relation traversal. The plan's per-project key
`uuid5(project_id_norm::ent_type::name_norm)` preserves this within a project
(prefix is constant), so no regression — **but the plan should state this
explicitly** to reassure reviewers that single-project multi-doc RAG still
works.

### M2. Config file collision detection absent

If `.cortext-harness/config/` ever contains two files declaring projects that
casefold to the same key (e.g. "Cortex" and "cortex"), the registry must
either reject or pick one deterministically. Phase 01 does not address this.
Add a "duplicate key detection" step in the loader.

### M3. `dev.py:833` change is a breaking change for existing ingested data

Phase 06 changes doc ingest collection from `project["name"]` to
`{project_id}_doc`. Any project already ingested with the old name will have
its vectors orphaned in the old collection after the change. The plan should
note a one-time rename/migration step or document that existing doc vectors
must be re-ingested.

### M4. `list_qdrant_collections` semantics change

Phase 05 says `list_qdrant_collections` accepts `project_id`. Today it lists
ALL collections on the server. Restricting it to one project is a behavior
change that could break discovery flows. Clarify whether the tool gains a
`project_id` arg (filtered) or stays global with a new scoped sibling tool.

### M5. Phase 03 backfill extension reuses existing script but does not version it

`backfill_project_scope_keys.py` already exists. Phase 03 "extends" it to cover
Field/Alias/Template/FunctionType and CALLS. If the existing script's tests
assert specific behaviors, extending it may break them. Safer: add a new
`backfill_writer_scope_gaps.py` or version the existing one. Note in Phase 03.

---

## What the plan gets right

- The 3 scope-challenge decisions are crisp and internally consistent (once
  C2 is resolved).
- Reusing `project_scope.py` primitives instead of inventing new ones is
  correct — the comparison-key contract is already shipped and tested.
- Keeping `graph_mcp` and `mind_mcp` separate avoids a risky server merge and
  preserves domain expertise.
- The 12 divergences are concretely evidenced with file:line refs; this is
  unusually well-grounded for a plan of this scope.
- Per-phase acceptance criteria are testable and narrow.
- The `ProjectTargets` dataclass is the right abstraction — frozen, focused,
  easy to test.
- Bidirectional cross-plan dependency update was applied correctly.

---

## Summary table

| ID | Severity | Phase | Status | Resolution |
| --- | --- | --- | --- | --- |
| C1 | Critical | 04 | **RESOLVED** | Drop + re-ingest (no entity-split algorithm) |
| C2 | Critical | 01 | **RESOLVED** | code/doc tách graph riêng (`doc_graph = "{pid}_doc"`) |
| C3 | Critical | 02, 05 | **RESOLVED** | `search_full` flag + harness audit step in plan |
| N1 | Notable | 01 | **RESOLVED** | No cache — accepted trade-off (per Validation Interview) |
| N2 | Notable | 02 | **RESOLVED** | Bỏ hẳn `active_project` (per Validation Interview) |
| N3 | Notable | 03, 04 | Open | Add `--confirm`/`--dry-run` to scoped reset during craft |
| N4 | Notable | 07 | Open | Specify embedding strategy for fixtures during craft |
| N5 | Notable | all | Open | Phase sequencing vs migration — note in plan |
| M1-M5 | Minor | various | Open | Implementation-time judgement calls |

**Recommendation:** **GO** — all critical and most notable risks resolved.
N3-N5 and M1-M5 can be addressed during `/hi-craft` implementation.
