# Plan: Fix graph_mcp "Type mismatch: expected String or Null but was Boolean"

## Root cause (verified)

`search_functions(parser_type=...)` build text-search predicate bằng cách bọc **mọi** property
trong `searchable_properties(parser)` với `toLower(coalesce(n.<prop>, ''))`:

- `code-tiny/mcp/cplus/cplus_mcp.py:2759-2775` (`tool_search_functions`, `property_predicate`)

Nhưng `GENERIC_SEARCHABLE_PROPERTIES` trong `code-tiny/mcp/framework_registry.py:25-29`
chứa property **không phải string**:

- `is_public_api` (BOOLEAN)
- `parse_depth` (INT)

Trên các graph đã ingest bằng schema mới có `is_public_api: true/false`
(`askilldev`, `bakatrans`, `hyperpack`), FalkorDB lite (Kuzu) ném
`Type mismatch: expected String or Null but was Boolean` khi evaluate
`toLower(coalesce(n.is_public_api,''))`. Graph cũ (`default`) không có
giá trị boolean nên chạy OK — lý do lỗi chỉ tái hiện fan-out across DBs.

Repro độc lập (không cần agent):

```python
from redislite.falkordb_client import FalkorDB
g = FalkorDB("<copy>/askilldev/data.rdb").select_graph("askilldev")
g.query("MATCH (n) WHERE any(q IN $qs WHERE toLower(coalesce(n.is_public_api,'')) CONTAINS q) RETURN n LIMIT 1",
        {"qs": ["main"]})   # -> ResponseError Type mismatch: expected String or Null but was Boolean
```

## Fix

1. `code-tiny/mcp/framework_registry.py`
   - Thêm `NON_TEXT_SEARCH_PROPERTIES = frozenset({"is_public_api", "parse_depth", "start_line", "end_line", ...})`
     (mọi property số/boolean từng xuất hiện trong GENERIC_SEARCHABLE_PROPERTIES).
   - Thêm hàm `text_search_properties(parser_type)` = `[p for p in searchable_properties(p) if p not in NON_TEXT_SEARCH_PROPERTIES]`;
     export trong `__all__`.
2. `code-tiny/mcp/cplus/cplus_mcp.py` (`tool_search_functions`)
   - Đổi `searchable_properties(capability.name)` → `text_search_properties(capability.name)`
     ở cả nhánh profile và nhánh fallback default tuple.
3. Rà soát các predicate builder khác dựng từ `searchable_properties()` (hiện chỉ cplus_mcp dùng;
   fastmcp_server dùng list cứng chỉ-string — an toàn) + thêm test chống hồi quy.

## Tests

- Unit: `text_search_properties("python")` không chứa `is_public_api`/`parse_depth`; các parser khác tương tự.
- Integration (opt-in, cần DB copy): chạy fallback cypher trên graph có `is_public_api` boolean → không exception.
- Regression sweep: `/tmp/graph_mcp_sweep.py` style batch qua 39 tool với parser_type hợp lệ.

## Risks / notes

- Mất khả năng match text trên `is_public_api`/`parse_depth` — không phải mất chức năng thật
  (match text "true" lên boolean property vô nghĩa).
- Lỗi chỉ manifest khi server fan-out qua nhiều instance DB (môi trường dev này có 5 instance);
  môi trường 1 DB không bao giờ thấy lỗi → dễ bị bỏ sót khi test.
- Khác biệt hành vi theo MCP protocolVersion (client invalid version "2026-02-2" nhận kết quả,
  version hợp lệ nhận lỗi) chưa giải thích được trọn vẹn — nghi ngờ cache/list schema per-session;
  sau khi fix property predicate cần verify lại cả hai đường (raw httpx 2025-06-18 + fastmcp Client).
