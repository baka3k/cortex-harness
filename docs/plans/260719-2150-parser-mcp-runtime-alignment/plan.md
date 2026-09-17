---
title: "Parser-MCP Runtime Alignment and Capability Observability"
status: completed
created: 2026-07-19
mode: hi-plan --fast
scope: unified MCP search semantics, runtime capability inspection, provider-neutral metadata, acceptance
blockedBy: [neo4j-to-falkordb-migration]
relatedPlans: [260719-0100-mcp-query-capability-hardening, 260715-2200-mcp-capability-routing]
reviewed: 2026-07-19
---

# Parser-MCP Runtime Alignment and Capability Observability

## Overview

Close the remaining gap between static parser profiles, framework-specific graph
facts, and the schema actually available to MCP at query time. The work fixes a
framework-filter mismatch, adds a provider-neutral live capability inspector,
and removes stale Neo4j/C++ terminology from the public tool catalog.

## Verified Findings

- Unified dispatch currently auto-populates `framework` with the canonical parser
  profile. This produces invalid filters such as `python`, `javascript`, and
  `php`, while overlay facts use `fastapi`, `django`, `express_js`, and `laravel`.
- `search_functions` applies profile labels/properties only when a framework
  filter is present, coupling two independent concerns.
- `list_parsers` reports advertised support but cannot show whether the active
  graph contains the required labels and relationships.
- Public catalog/tool descriptions still contain Neo4j-specific and legacy
  backend terminology even when FalkorDB is active.
- Windows `dev mcp start --force-restart` cannot discover MCP processes through
  POSIX `pgrep`, and its Unicode heading can fail on a `cp1252` console. This can
  leave an old server bound to the port after an apparently successful restart.
- The active `cortext` semantic index is older than the current source, and graph
  expansion timed out during audit. No full sync is authorized by this plan.

## Phases

1. [Phase 01 — Search profile/filter separation](phase-01-search-profile-filter-separation.md)
2. [Phase 02 — Runtime capability observability](phase-02-runtime-capability-observability.md)
3. [Phase 03 — Provider-neutral catalog](phase-03-provider-neutral-catalog.md)
4. [Phase 04 — Acceptance and live verification](phase-04-acceptance-live-verification.md)

## Dependencies

- Extends the completed parser capability registry and schema-gating work in
  `260719-0100-mcp-query-capability-hardening`.
- Uses the active provider-neutral driver contract while Neo4j parity remains
  gated by `neo4j-to-falkordb-migration`.
- Does not change analyzer ownership, graph identifiers, or Qdrant collections.

## Contract Decisions

- `parser_type` selects labels, properties, relationships, and query behavior.
- `framework` is an optional exact data filter and is never inferred from the
  parser profile.
- Runtime inspection reports `advertised`, `observed`, and `effective` support
  for `symbols`, `calls`, `endpoints`, and `database`.
- Observed support is derived from provider labels/relationship types against a
  versioned schema contract. Uninspectable schema is `unknown`, not success.
- A deterministic schema fingerprint allows drift comparison without storing or
  exposing provider credentials.
- Full source-to-index freshness requires consistent analyzer provenance and is a
  follow-up; this phase reports resync recommendations from schema gaps only.

## Success Criteria

- Parser-only searches do not inject an invalid framework filter.
- Explicit framework filters remain exact and cannot be bypassed by profile-label
  predicates.
- A new MCP capability inspection tool reports schema status, fingerprint,
  missing contract items, effective dimensional support, and recommended action.
- Tool catalog/public descriptions use provider-neutral terminology while legacy
  CLI/environment aliases remain compatible.
- Windows force-restart terminates the discovered MCP process trees and emits
  console-safe status output before starting replacement services.
- Contract, routing, search, FalkorDB, and live read-only checks pass without a
  full ingestion or embedding sync.

## Plan Review

Reviewed before implementation under the `hi-craft` hard gate. The design fixes
the confirmed query bug first, then adds observability before broadening parser
coverage. It intentionally avoids a speculative universal parser abstraction and
does not claim graph freshness that current analyzer provenance cannot prove.
