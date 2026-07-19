# Parser-MCP Runtime Alignment — 2026-07-19

## Context

Parser profiles and framework overlays had diverged at query time: unified dispatch inferred a framework name from `parser_type`, while ingested facts use exact overlay names such as `fastapi`, `django`, `express_js`, and `laravel`. The [runtime-alignment plan](../../plans/260719-2150-parser-mcp-runtime-alignment/plan.md) also required MCP to distinguish advertised capability from schema evidence observed in the active graph.

## Change

- Search dispatch now keeps the parser profile separate from the optional framework filter, while `search_functions` always applies parser labels/properties and treats an explicit framework as an exact predicate (`code-tiny/mcp/unified_mcp.py:595`, `code-tiny/mcp/cplus/cplus_mcp.py:2675`, `code-tiny/mcp/cplus/cplus_mcp.py:2734`).
- A versioned schema-evidence contract evaluates `symbols`, `calls`, `endpoints`, and `database` independently and produces an order-independent fingerprint; unavailable inspection remains distinct from an inspectable empty schema (`code-tiny/mcp/framework_registry.py:68`, `code-tiny/mcp/framework_registry.py:211`, `code-tiny/mcp/framework_registry.py:253`, `code-tiny/mcp/cplus/cplus_mcp.py:970`).
- The new `inspect_parser_capabilities` MCP tool reports advertised and effective support, missing dimensions, schema status/fingerprint, and a bounded recommendation without triggering ingestion (`code-tiny/mcp/unified_mcp.py:686`, `code-tiny/mcp/unified_mcp.py:746`). Regression tests cover parser/framework separation and live-schema outcomes (`tests/test_framework_mcp_search.py:18`, `tests/test_unified_mcp_input_coercion.py:431`).

## Impact

MCP searches no longer discard valid framework overlay facts through an invented filter, and callers can tell whether a parser capability is merely declared or actually supported by the current provider schema. **Risk level: medium** because schema evidence is label/relationship based and intentionally does not prove that the source index is fresh or complete.

## Decision

Use `parser_type` only to select the query profile and reserve `framework` for an explicit exact data constraint. Publish a provider-neutral, versioned runtime contract rather than infer success from aliases or return silent empty results. Full source-to-index freshness remains a separate incremental-sync concern because analyzer provenance is not yet consistent enough for a reliable commit-level assertion.

## References

- Plan: [plans/260719-2150-parser-mcp-runtime-alignment/plan.md](../../plans/260719-2150-parser-mcp-runtime-alignment/plan.md)
- Acceptance contract: `docs/MCP_CAPABILITY_ACCEPTANCE_MATRIX.md:55`
- Base commit: `d9623f30811fafe2fe3e9bc47a0bba0640a5af09`
