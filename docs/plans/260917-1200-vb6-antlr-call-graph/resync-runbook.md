# Re-sync Runbook — VB6 ANTLR engine (plan 260917-1200, phase 05)

`PARSE_CACHE_VERSION` bumped to `vb-family-v2026-09-17-1` in the same change
that altered payloads (AD-08): old caches miss automatically. This runbook
re-syncs every already-ingested VB6 project and verifies the result.

## 1. Merge

The final PR already carries the version bump — nothing extra to do at merge
time. Confirm with:

```bash
.venv/bin/python -c "from tools.vb.vb_common import PARSE_CACHE_VERSION as v; print(v)"
# expected: vb-family-v2026-09-17-1
```

## 2. Full (non-incremental) sync per project

For every project VB6 already ingested, run a FULL sync — the cache misses on
version, so payloads rebuild under the ANTLR engine:

```bash
.venv/bin/python -m tools.vb.vb6_analyzer \
  --dialect vb6 --root <project-root> \
  --project-id <id> --project-name <name> \
  --vb6-parser-engine auto \
  --verbose
```

Notes:

- First run builds the worker jar via Maven (one-time, cached afterwards).
- Watch the mandatory summary line (printed without `--verbose` too):

```text
[vb6][summary] engine=antlr files=N fallback=K callsites=C resolved=R possible=P rate=xx.x%
```

## 3. Verify each project

- `rate` ≥ 80% on resolvable-heavy projects (fixture measures 100%; legacy
  codebases with heavy late binding will be lower — judge vs the regex
  baseline, which captured ~62% of sites and 0 correct cross-module edges).
- Spot-check callers of 2–3 functions via graph_mcp: `find_callers` /
  `trace_flow` on representative procedures.
- Count POSSIBLE_CALLS per status (Cypher):

```cypher
MATCH ()-[r:POSSIBLE_CALLS]->()
WHERE r.project_id_normalized = toLower(normalize('<project-id>'))
RETURN r.resolution_status AS status, count(*) AS edges ORDER BY edges DESC;
```

- Verify the AD-09 property after a later incremental sync: incoming edges
  into a changed file still exist:

```cypher
MATCH (caller:Function)-[c:CALLS]->(callee:Function {file_path: 'modUtil.bas'})
RETURN caller.id, callee.id LIMIT 5;
```

- Placeholder hygiene (F8): external_symbol count must not grow between two
  consecutive syncs:

```cypher
MATCH (f:Function {kind: 'external_symbol', project_id: '<project-id>'})
RETURN count(f);
```

## 4. Rollback

If the data looks wrong, re-run with the regex engine — results equal the
pre-change behavior exactly while you investigate:

```bash
.venv/bin/python -m tools.vb.vb6_analyzer ... --vb6-parser-engine regex
```

(`regex` never invokes the worker; the parse cache version stays the new one
for both engines, so switching engines back and forth re-parses cleanly.)
