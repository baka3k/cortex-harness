# Phase 04 — Consumer guardrails and documentation

**Goal:** agent-facing guidance reflects the fixed contract and adds
transitional guardrails (dedup trust, parsers_failed as advisory signal,
prefer scoped calls for deep traces). All guardrail content is net-new —
verified: no skill file currently mentions fanout/parsers_failed/dedup.

**Rollout note:** sections 4.1-4.3 edit **user-home files**
(`~/.agents/skills/...`, `~/.claude/CLAUDE.md`) — they are machine-local
configuration, not repo artifacts; they ship with this plan's rollout on
this machine only and must be listed explicitly in the commit/PR
description as out-of-repo touches. Section 4.4 edits repo docs.

## 4.1 Primary tool reference

`~/.agents/skills/hi-repository-search/references/code_graph.md`:

- Add `parser_type` row to each of the 13 fan-out tool sections
  (`search_functions` 73-90, `search_by_code` 92-107, `get_symbol`,
  `get_node_details`, `query_subgraph`, `find_paths`, listup_*,
  `trace_flow`, `list_possible_calls`).
- Add a "Fan-out contract" subsection: omit `parser_type` = search across
  query engines (results deduplicated by node id, `parsers_searched` lists
  engine representatives); pass `parser_type` to scope.

## 4.2 Guardrail block (shared wording, replicate per file conventions)

1. **Scoped-first:** for deep traces / impact analysis, pass `parser_type`
   (or a registered `project_id`, which pins the parser) — avoids fan-out
   merge entirely.
2. **Dedup trust:** when a fan-out response is consumed, trust only
   deduplicated node identities (`id`); if `dedup_removed` is absent
   (pre-fix server) or ids repeat, dedup client-side by `id` /
   `(start_id, type, end_id)` before counting or branching.
3. **`parsers_failed` is advisory, not fatal:** if present, check
   `parser_errors` types — `OVERLOADED`/admission errors or
   `tool_not_in_backend` for the engine you need -> re-issue a scoped call
   with `parser_type`; isolated profile failures -> proceed but note the
   gap in the answer's confidence.

Apply to: `hi-reverse/references/MCP-TOOLS.md` (13, 28, 69, 74, 104),
`hi-usecase-discovery/references/graph-retrieval.md` (38, 114),
`hi-predict/SKILL.md` (60), `hi-scenario/SKILL.md` (79),
`hi-knows/references/retrieval-playbook.md`,
`shared/retrieval-protocol.md` + per-skill copies (hi-api-contract-discovery,
hi-behavior-modeling, hi-command-spec-discovery, hi-repo-recon),
`hi-tech-build-audit/references/audit-checklist.md` (18).

Keep edits minimal — reference the primary block in `code_graph.md` where
the file structure allows, rather than duplicating full text.

## 4.3 Root directive

`~/.claude/CLAUDE.md` graph_mcp section: add one line to the routing
guidance — pass `parser_type` on graph tools when known; fan-out responses
dedup by node id; treat `parsers_failed` as advisory per above.

## 4.4 Repo docs (amend)

- `code-tiny/mcp/Readme.md`: fix stale line ~768 ("active profile when
  omitted"); document fan-out = per-query-engine with dedup.
- `docs/UNIFIED_INGEST_QUERY_CONTRACT.md`: add `parser_type` Query
  Precedence paragraph mirroring `project_id` precedence (99-116): present
  -> scoped; absent -> engine fan-out (deduped).
- `docs/MCP_CAPABILITY_ACCEPTANCE_MATRIX.md`: add fan-out row — merged
  `query_engine=graph_fanout`, `parsers_searched` = engine
  representatives, dedup guarantee.

## 4.5 Backlog note (no action)

`fastmcp_server.py` standalone defs (1527-2496, `--mode fast` deployment)
and `java_mcp.py` legacy tools do not mirror the new param — separate
deployment paths; revisit only if `--mode fast` regains users.

## Verification

- `rg -n "parsers_failed|dedup" ~/.agents/skills/` returns the new
  guardrail hits.
- Catalog docs match live `list_mcp_functions` output (spot-check 13
  tools).
