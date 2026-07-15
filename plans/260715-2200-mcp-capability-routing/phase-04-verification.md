# Phase 04: Regression, Compatibility, and Provider-Gated Verification

## Goal

Prove routing correctness without breaking existing parser integrations.

## Tests

1. Registry tests for alias uniqueness, canonicalization, backend assignment,
   support levels, and collision precedence.
2. Unified MCP tests for activation, `list_parsers`, search, subgraph, paths,
   flow, endpoint, workflow, impact, and unsupported capability responses.
3. Compatibility tests for Android, generic C++, Java/JVM, Spring, Struts,
   Flutter, ASP.NET, COBOL, and Perl profiles.
4. Mixed-project tests proving Android and framework profiles can coexist while
   sharing one Unified MCP process.
5. Provider-neutral tests for relationship filtering and no-orphan responses;
   run Neo4j/FalkorDB parity only when credentials/services are available.
6. Determinism and backward-compatibility checks for aliases, serialized
   discovery output, and default relationship ordering.

## Exit criteria

- Focused MCP routing/discovery suite passes.
- Existing MCP and framework registry regressions pass.
- Unsupported capability behavior is covered by tests and documentation.
- External provider gates are reported as incomplete when unavailable, never
  counted as passing silently.
