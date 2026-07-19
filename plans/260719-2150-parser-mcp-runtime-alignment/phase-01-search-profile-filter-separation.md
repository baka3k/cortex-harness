# Phase 01: Search Profile and Framework Filter Separation

## Context

Parser profiles and framework filters currently share one variable despite
representing different concepts.

## Requirements

- Use `parser_type` to choose searchable labels/properties.
- Apply `framework` only when explicitly provided.
- Make explicit framework filtering exact in full-text and fallback paths.

## Related Files

- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/cplus/cplus_mcp.py`
- `tests/test_framework_mcp_search.py`
- `tests/test_unified_mcp_input_coercion.py`

## Todo

- [x] Add failing parser/framework separation tests.
- [x] Correct dispatch and backend query construction.
- [x] Run focused search regressions.

## Risks

- Existing callers that relied on implicit framework filtering may see broader,
  but more correct, parser-profile results.

## Success Criteria

- Python/JavaScript/PHP parser searches can return their overlay endpoint facts.
- `framework=fastapi` returns FastAPI facts only.
