# Phase 03: Provider-Neutral Catalog

## Context

Public metadata still describes graph names and raw properties as Neo4j-specific,
which is incorrect under FalkorDB.

## Requirements

- Replace provider-specific wording in public MCP descriptions/catalog entries.
- Preserve legacy CLI and environment variable aliases.
- Describe parser profiles using query engines and dimensional support.

## Related Files

- `code-tiny/mcp/tool_metadata.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/Readme.md`

## Todo

- [x] Update public descriptions and examples.
- [x] Add catalog assertions preventing terminology regression.

## Risks

- Internal variable names remain legacy for compatibility and are out of scope.

## Success Criteria

- Public catalog no longer claims Neo4j-only behavior for provider-neutral tools.
