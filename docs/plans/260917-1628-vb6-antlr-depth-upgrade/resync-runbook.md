# Re-sync Runbook — VB6 ANTLR depth upgrade (plan 260917-1628, phase 5.4)

`PARSE_CACHE_VERSION` bumped to `vb-family-v2026-09-17-4` (AD-05: -2 phase-01
hydrated planes + arity/dictionary enrichment, -3 phase-02 controls[] +
keep-designer + event wiring, -4 phase-04 comment extraction). Old caches
miss automatically. This runbook re-syncs every already-ingested VB6 project
and verifies the new planes end to end.

## 1. Merge

The PR carries the version bump — nothing extra to do at merge time. Confirm:

```bash
.venv/bin/python -c "from tools.vb.vb_common import PARSE_CACHE_VERSION as v; print(v)"
# expected: vb-family-v2026-09-17-4
```

First run after merge rebuilds the worker jar via Maven (the source stamp
changed; one-time, cached afterwards). CI/dev-doctor note unchanged: java
required for engine=antlr, mvn only for the rebuild.

## 2. Full (non-incremental) sync per project

```bash
.venv/bin/python -m tools.vb.vb6_analyzer \
  --dialect vb6 --root <project-root> \
  --project-id <id> --project-name <name> \
  --vb6-parser-engine auto \
  --verbose
```

Watch the mandatory summary line (format unchanged):

```text
[vb6][summary] engine=antlr files=N fallback=K callsites=C resolved=R possible=P rate=xx.x%
```

What is NEW in payloads after this sync (all observable, no schema change):

- `enums[]` / `constants[]` / `events[]` populated (were always empty).
- `declares[]` — ANTLR-only plane (regex payloads have none; their
  `parse_meta.declares_regex_support=false` records the asymmetry).
- `controls[]` on .frm/.ctl/.pag — designer control tree (parents before
  children).
- `functions[]` += `min_arity` / `has_optional_args` / `has_paramarray`,
  `vb6_event` / `vb6_control_type` on wired handlers (e.g. `cmdGo.Click`).
- `calls[]` += `dictionary_call` rows with `default_member=true`
  (POSSIBLE_CALLS tier only, never strict CALLS).
- `comment` / `summary` / `note` populated from adjacent comments → Qdrant
  embedding text enriched automatically (`note or code`).
- `parse_meta.module_attributes` (lowercased keys, e.g.
  `vb_predeclaredid: "True"`).

## 3. Verify each project

- Qdrant point payloads: sample a known event handler
  (`node_type=function`, `vb6_event` non-empty) and a form class point
  (`controls` list present).
- Dictionary rows landed (Cypher):

```cypher
MATCH ()-[r:POSSIBLE_CALLS]->()
WHERE r.project_id_normalized = toLower('<project-id>')
  AND r.call_type = 'dictionary_call'
RETURN count(*) AS dictionary_rows;
```

- POSSIBLE_CALLS status histogram (same query as the 1200 runbook) — the
  vocabulary is unchanged; dictionary rows appear as `external` or
  `late_bound`.
- Interface dispatch: previously-`unresolved` member calls on interface-
  typed receivers should now be `ambiguous` (multiple implementers) or
  `name_resolved` (unique implementer).

## 4. Guards / rollback

- Engine rollback (exact pre-change behavior):

```bash
.venv/bin/python -m tools.vb.vb6_analyzer ... --vb6-parser-engine regex
```

- Designer-strip fallback (only if keep-designer misbehaves on a real .frm —
  NOT observed on the fixture corpus; when active the summary prints a strip
  notice and affected payloads carry `parse_meta.designer_stripped=true`
  with empty `controls[]`):

```bash
VB6_ANTLR_STRIP_DESIGNER=1 .venv/bin/python -m tools.vb.vb6_analyzer ...
```

Both guards are env-level: no code change, no rebuild.
