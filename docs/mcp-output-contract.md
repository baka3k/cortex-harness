# Chuẩn output và checklist thay đổi MCP

## Mục tiêu

Tài liệu này là chuẩn bắt buộc cho mọi tool của `graph_mcp` và `mind_mcp`.
Mục tiêu là ngăn các lỗi đã lặp lại nhiều lần:

- mỗi tool tự đặt một kiểu error khác nhau (`type`, `warning`, `exception`,
  `error` string);
- client coi `isError: true` là dữ liệu thành công;
- success, empty và error không phân biệt được bằng máy;
- output chứa nguyên `code`, `note` hoặc catalog chẩn đoán dù caller chỉ yêu
  cầu summary;
- input schema khai báo sai kiểu, ví dụ số và boolean đều thành `string`.

Các từ **MUST**, **MUST NOT**, **SHOULD** và **MAY** trong tài liệu mang nghĩa
quy chuẩn.

## Contract chung (v2 — single channel)

Từ v2, toàn bộ response nằm trong MỘT kênh: `content[0].text` là chuỗi JSON
envelope. `structuredContent` KHÔNG còn được emit — đây là thay đổi chủ đích
so với v1 (xem [Lịch sử v1 → v2](#lịch-sử-v1--v2)); mọi client (kể cả những
client chỉ render `content` cho LLM) đọc được trọn vẹn dữ liệu mà không cần
curl thẳng JSON-RPC.

| Lớp | Mục đích | Quy tắc |
| --- | --- | --- |
| `content` | Envelope JSON v2 — kênh duy nhất | MUST là JSON hợp lệ, đúng một text block |
| `structuredContent` | — | MUST NOT emit |
| `isError` | Trạng thái thực thi tool | MUST là `true` khi tool thất bại |
| `_meta` | Metadata ngoài nghiệp vụ | MUST chứa tên/version contract và tên tool |

Tên contract hiện tại:

```text
cortex.mcp.tool-result / 2.0
```

### Envelope v2

```json
{
  "contract": "cortex.mcp.tool-result",
  "contractVersion": "2.0",
  "tool": "search_functions",
  "message": "Success: 1 result.",
  "ok": true,
  "data": {
    "db": "procsample",
    "results": [
      {
        "id": "function-1",
        "labels": ["Function"],
        "properties": {
          "name": "batch_job_run",
          "file_path": "src/core/batch_job.c",
          "content": "batch_job_run"
        }
      }
    ],
    "ids": ["function-1"]
  },
  "error": null,
  "truncation": null
}
```

Phân vai field:

| Field | Vai trò |
| --- | --- |
| `contract`, `contractVersion`, `tool` | Tự mô tả envelope (trùng `_meta`; `_meta` không vào context model nên không tốn token) |
| `message` | Một câu ngắn cho người/LLM lướt nhanh |
| `ok` | `true`/`false` — phân biệt máy được success/error |
| `data` | **Toàn bộ payload nghiệp vụ**; shape giữ nguyên theo tool |
| `error` | `null` hoặc `{code, message, retryable, details}` |
| `truncation` | `null` hoặc block mô tả việc bị cap kích thước |

Quy tắc:

- `data` MUST giữ nguyên kiểu nghiệp vụ: object là object, list là list,
  không `json.dumps` rồi nhét thành string.
- Tên field nghiệp vụ MAY khác giữa các tool; envelope không được khác.
- Không lặp `ok` hoặc `error` lần hai bên trong `data`.
- Metadata như contract version, tool name và tracing SHOULD nằm trong
  `_meta`, không làm bẩn `data`.
- Payload string thô (tool chỉ trả text) MUST được wrap thành
  `{"text": "<string>"}` để envelope giữ kiểu ổn định.

### Cap kích thước (structural truncation)

`content[0].text` bị cap mặc định **20.000 ký tự**, override bằng env
`MCP_CONTENT_MAX_CHARS` (giá trị `0` = tắt cap, chỉ dùng khi debug).

Khi vượt cap:

- `data` MUST được thay bằng
  `{"truncated": true, "preview": "<head của data JSON>", "note": "<hint>"}`;
- block `truncation` MUST được điền:
  `{"truncated": true, "reason": "content_cap_exceeded", "cap_chars": N, "hint": "<hint>"}`;
- JSON MUST VẪN hợp lệ — không bao giờ cắt giữa chuỗi;
- error path cũng chịu cap: `error.message` cắt ở 500 ký tự, list trong
  `error.details` cắt ở 50 phần tử kèm `details_truncated: true`.

Client gặp `truncation.truncated = true` SHOULD thu hẹp query (`top_k`,
`limit`, `include_raw_fields=false`, `content_mode=summary`) thay vì đọc
preview.

### Success nhưng không có dữ liệu

Không có kết quả là một success hợp lệ, không phải exception. `data` là
collection rỗng đúng kiểu (`[]` hoặc `{}`), `message` là `"Success: 0 results."`.

### Tool execution error

```json
{
  "contract": "cortex.mcp.tool-result",
  "contractVersion": "2.0",
  "tool": "find_screen_workflows",
  "message": "NAVIGATE is unavailable for parser cplus.",
  "ok": false,
  "data": null,
  "error": {
    "code": "capability_unavailable",
    "message": "NAVIGATE is unavailable for parser cplus.",
    "retryable": false,
    "details": {
      "missing_relationships": ["NAVIGATE"]
    }
  },
  "truncation": null
}
```

Quy tắc:

- Tool/business/input/storage error MUST dùng `isError: true`.
- `error.code` MUST là mã ổn định cho máy; không dùng nguyên exception message
  làm code.
- `error.message` MUST ngắn, an toàn và có thể hành động được.
- `error.retryable` MUST là boolean.
- `error.details` MUST là object, kể cả khi rỗng.
- `error.details` MUST nhỏ và chỉ chứa dữ liệu giúp client xử lý request hiện
  tại, ví dụ `parser`, `missing_relationships`, `missing_labels`,
  `missing_parameters`, `retry_after_ms` hoặc `correlation_id`.
- Error response MUST NOT nhúng catalog/schema chẩn đoán như
  `available_labels`, `available_relationships`, `accepted_params`,
  `required_params`, `received_params`, `supported_parsers`,
  `supported_aliases`, `capability_diagnostics` hoặc context backend đầy đủ.
  Client dùng `list_mcp_functions`, `list_parsers` hoặc
  `inspect_parser_capabilities` khi cần dữ liệu discovery chi tiết.
- Không trả `warning` thay cho error nếu request không thể thực hiện.
- Unknown tool: fastmcp 4 trả isError result — trên unified server result
  này được bọc envelope v2 với code `not_found_error`; malformed JSON-RPC và
  lỗi transport vẫn ở lớp JSON-RPC, không ép vào tool envelope.

### Mã lỗi chuẩn

Ưu tiên tái sử dụng các code sau trước khi tạo code mới:

| Code | Khi dùng | Retryable mặc định |
| --- | --- | ---: |
| `invalid_parameters` | Giá trị hoặc combination input sai | false |
| `missing_required_parameters` | Thiếu input bắt buộc | false |
| `unsupported_parser` | Parser/alias không đăng ký | false |
| `capability_unavailable` | Corpus/parser không có fact cần thiết | false |
| `project_not_registered` | `project_id` không tồn tại | false |
| `collection_unavailable` | Collection chưa ingest hoặc không truy cập được | false |
| `overloaded` | Admission queue/capacity tạm hết | true |
| `storage_unavailable` | Backend tạm thời không sẵn sàng | true |
| `tool_execution_error` | Lỗi thực thi chưa phân loại | false |

Không đổi chính tả hoặc casing của code đã phát hành nếu chưa có migration.

## Template triển khai

### Shared normalizer

Không tự viết envelope trong từng tool. Dùng helper chung:

```python
from cortex_harness.mcp_contract import (
    normalize_error,
    normalize_success,
    result_envelope_json,
    result_meta,
    result_summary,
)
```

`result_envelope_json(tool, ok=..., data=..., error=..., message=...)` là
điểm serialize duy nhất — nó áp cap, bound error details và luôn trả JSON
hợp lệ.

### Template FastMCP `ToolResult`

```python
from fastmcp.tools.tool import ToolResult


def success_result(tool_name: str, data: object) -> ToolResult:
    return ToolResult(
        content=result_envelope_json(
            tool_name,
            ok=True,
            data=normalize_success(data)["data"],
            message=result_summary(data, ok=True),
        ),
        meta=result_meta(tool_name),
        is_error=False,
    )


def error_result(tool_name: str, exc: Exception) -> ToolResult:
    payload = normalize_error(exc)
    return ToolResult(
        content=result_envelope_json(
            tool_name,
            ok=False,
            data=None,
            error=payload["error"],
            message=payload["error"]["message"],
        ),
        meta=result_meta(tool_name),
        is_error=True,
    )
```

KHÔNG truyền `structured_content` — fastmcp sẽ auto-generate
`structuredContent` cho dict return nếu middleware/tool không wrap; middleware
là điểm chặn bắt buộc.

### Template MCP SDK `CallToolResult`

```python
from mcp.types import CallToolResult, TextContent


def success_result(tool_name: str, data: object) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=result_envelope_json(
                    tool_name,
                    ok=True,
                    data=normalize_success(data)["data"],
                    message=result_summary(data, ok=True),
                ),
            )
        ],
        isError=False,
        _meta=result_meta(tool_name),
    )
```

### Template typed tool

```python
@mcp.tool()
def search_items(
    query: str,
    limit: int = 20,
    include_details: bool = False,
    project_id: str | None = None,
) -> dict[str, object]:
    ...
```

MUST dùng type annotation thật. Không để `top_k`, `limit`, boolean hoặc array
thành `string` trong `tools/list` chỉ để tiện coercion ở runtime.

## Quy ước payload nghiệp vụ

### Search/list

`data` giữ shape v1:

```json
{
  "results": [],
  "ids": [],
  "db": "project_graph"
}
```

- `results` luôn là list record.
- `ids` luôn là list ID cùng thứ tự với `results` nếu tool công bố field này.
- `db`, `collection` hoặc `collections_searched` phải phản ánh target thực tế.
- Một node SHOULD có `id`, `labels`, `properties` theo đúng kiểu.
- `include_raw_fields=false` MUST loại `code`, `note`, raw document và field
  nội bộ dung lượng lớn.
- `content_mode=summary` MUST có fallback về `name`/`id`, không trả string rỗng.

### Scope block (`semantic_graph_search`, mind server)

`semantic_graph_search` trả thêm trong `data`: block `scope` giải thích
chính nó đã search ở đâu, và `warnings` (list string, mirror của
`scope.warnings`). Envelope v2 không đổi; các field là additive.
`collections_searched` giữ nguyên ý nghĩa cũ — chi tiết discovery nằm ở
`scope.discovered_collections`, hit thật nằm ở `scope.per_collection_hits`.

| Field | Kiểu | Ý nghĩa |
|---|---|---|
| `mode` | `"scoped" \| "collection" \| "unscoped" \| "fallback_all"` | Nhánh resolve nào đã chạy. |
| `requested_project_id` | `string \| null` | `project_id` gốc của call. |
| `resolved_project_id` | `string \| null` | Dạng canonical trong registry (single match). |
| `registry_hit` | `bool` | Registry có biết id này không. |
| `collections_searched` | `list[string]` | Tên collection được yêu cầu (giữ nghĩa cũ). |
| `collections_present` | `list[string]` | Tên thực sự present trên store. |
| `per_collection_hits` | `map[string, int]` | Số hit thô mỗi collection đóng góp. |
| `discovered_collections` | `list[string]` | Shard `*_doc` tìm thấy bằng live listing (unscoped/fallback). |
| `targets_total` / `targets_searched` | `int` | Fan-out trước/sau cap (`SCOPE_MAX_TARGETS`, mặc định 12). |
| `graph_present` | `bool \| null` | Gate GRAPH.LIST cho shard convention; `null` = gate không trả lời được (fail-open). |
| `warnings` | `list[string]` | Fallback, cap, store unreachable, found-but-empty… |

`strict_scope=true` + id lạ → `ok:false`, error code
`project_not_registered` (query không bao giờ tự tạo shard rác). Unscoped
sạch trả `mode:"unscoped"`, `warnings: []`.

Toán tử: `dev doctor` có nhóm **scope drift** (warn-only) báo shard lạ /
declared-but-absent / shard rỗng / config JSON hỏng.

### Scope block (`repo`, code server)

Code-side query tools nhận thêm `repo` (optional, string) theo
[`docs/REPO_QUERY_RULES.md`](./REPO_QUERY_RULES.md) — response trả block
`scope` additive trong `data` + `warnings` mirror ở top-of-data (envelope
v2 không đổi, shape mirror mind-side nhưng tối giản):

| Field | Kiểu | Ý nghĩa |
|---|---|---|
| `scope.repo` | `string \| null` | Giá trị đã normalize của request (null = unscoped). |
| `scope.filter_applied` | `bool` | `false` = predicate no-op, tìm tất cả repo. |
| `scope.pre_filter_hits` | `int` | (chỉ vector: `semantic_search`, `explore_graph`) số hit trước post-filter. |
| `scope.post_filter_hits` | `int` | (chỉ vector) số hit sau post-filter. |
| `scope.warnings` | `list[string]` | Scoped-rỗng: `"repo '<x>' matched 0 nodes. Known repo values: …"` (≤5, query phụ chỉ chạy nhánh rỗng); vector over-fetch cạn: gợi ý tăng `top_k` hoặc thu hẹp bằng `project_id` + `repo`. |

`get_ipc_message` trả bare list nên chỉ có predicate, không scope block.
Trên unified fan-out, `scope` được promote từ parser đầu tiên thành công và
`warnings` gộp-dedupe. Acceptance gate:
`tests/test_unified_mcp_wrapper_signatures.py::test_repo_scope_acceptance_gate_live_schema`.

### Pagination

Tool có thể trả nhiều dữ liệu SHOULD dùng:

```json
{
  "items": [],
  "page": {
    "limit": 50,
    "next_cursor": null,
    "has_more": false
  }
}
```

Không dùng lúc `offset`, lúc `cursor`, lúc `page` cho cùng một họ tool mà
không có lý do và tài liệu migration.

### Discovery

- Discovery mặc định MUST là summary nhỏ.
- Diagnostic catalog lớn MUST có opt-in rõ ràng, ví dụ
  `detail_level="full"`.
- Không nhúng toàn bộ capability catalog vào mỗi business response.

## Checklist thêm MCP

- [ ] Xác định rõ tool là read-only, mutation, destructive hay open-world.
- [ ] Tên tool và field dùng `snake_case`, có nghĩa ổn định.
- [ ] Mọi input có type annotation đúng (`int`, `float`, `bool`, list, object).
- [ ] Required/optional/default trong signature khớp `tools/list.inputSchema`.
- [ ] Validate range, enum, length và tổ hợp input trước khi gọi storage.
- [ ] Payload nghiệp vụ trả kiểu Python object/list, không trả JSON string.
- [ ] Success đi qua shared normalizer và `result_envelope_json` — KHÔNG
  truyền `structured_content`.
- [ ] Empty là success với collection rỗng đúng kiểu.
- [ ] Tool error có `isError=true` và đủ `code/message/retryable/details`.
- [ ] Metadata contract/tool/tracing nằm trong `_meta`.
- [ ] Nếu framework hỗ trợ output schema, schema là object và khớp response.
- [ ] Có unit test success, empty, invalid input và backend failure.
- [ ] Có test xác nhận không leak `code`/`note` khi raw fields tắt.
- [ ] Có live smoke test qua streamable HTTP, không chỉ gọi function trực tiếp.
- [ ] Client test xác nhận parse JSON từ `content[0].text` và tôn trọng
  `isError`.
- [ ] Tool được thêm vào catalog/discovery đúng một lần.
- [ ] Cập nhật tài liệu và ví dụ gọi tool.

## Checklist sửa MCP

- [ ] Ghi rõ thay đổi additive hay breaking.
- [ ] Không đổi kiểu/ý nghĩa field hiện hữu âm thầm.
- [ ] Nếu đổi schema, thêm regression test cho schema cũ cần giữ tương thích.
- [ ] Không đưa metadata chẩn đoán lớn vào business response mặc định.
- [ ] Không bỏ `isError=true` khi refactor exception handling.
- [ ] Không đổi empty result thành error hoặc ngược lại ngoài chủ đích.
- [ ] Chạy snapshot/contract test cho `tools/list`.
- [ ] Chạy test toàn bộ graph và mind normalizer.
- [ ] Restart process thật; xác nhận process mới đã load code mới.
- [ ] So sánh live output trước/sau về field, type và kích thước.

## Checklist xoá MCP

- [ ] Tìm toàn bộ caller, catalog entry, default input và test fixture.
- [ ] Xác định deprecation window hoặc migration tool thay thế.
- [ ] Xoá registration, metadata, router và backend cùng một change set.
- [ ] `tools/list` không còn quảng cáo tool đã xoá.
- [ ] Unknown tool phải thành protocol error, không thành success rỗng.
- [ ] Xoá hoặc cập nhật smoke test và tài liệu liên quan.
- [ ] Kiểm tra không còn alias hoặc proxy trỏ vào callable đã xoá.

## Bộ test tối thiểu trước khi merge

```bash
.venv/bin/pytest -q \
  tests/test_mcp_output_contract.py \
  tests/test_mcp_testtool_client.py \
  tests/test_mcp_batch_report.py \
  tests/test_unified_mcp_input_coercion.py \
  tests/test_unified_contract_doc_paths.py \
  tests/test_semantic_graph_scope_block.py
```

Sau unit test:

```bash
.venv/bin/python cortex_harness/dev.py mcp start \
  --force-restart \
  --project-dir /path/to/project

.venv/bin/python scripts/mcp-lifecycle.py doctor
```

Live smoke MUST có ít nhất:

1. success có data;
2. success empty;
3. invalid input error;
4. capability/storage error;
5. `tools/list` schema đúng kiểu;
6. `content[0].text` parse được JSON envelope v2 và không có
   `structuredContent` trên wire;
7. cả `graph_mcp` và `mind_mcp` dùng cùng envelope/version.

## Quy tắc cho client và report

- Client MUST đọc `isError` trước.
- Khi `isError=true`, client MUST raise/route error; không trả error dict như dữ
  liệu thành công.
- Client MUST parse JSON từ `content[0].text`; dữ liệu nghiệp vụ nằm ở
  `envelope.data`, lỗi ở `envelope.error`. Đọc `structuredContent` chỉ là
  fallback cho server legacy pre-v2.
- Client ngoài được enroll qua `dev mcp integrate` (Claude Desktop, VS Code,
  Cursor…): từ v2 dữ liệu nằm trong `content[0].text`, không còn
  `structuredContent`.
- Report mặc định SHOULD ghi envelope (hoặc `data`), trạng thái và summary;
  không lặp cả serialized `content` lẫn envelope một lần nữa.
- Chế độ raw/full chỉ dùng khi debug protocol và phải ghi rõ output bị lặp vì
  backward compatibility.

## Lịch sử v1 → v2

v1 (1.0) đặt một câu stub ngắn trong `content[0].text`
(`"Success: N items. Read structuredContent.data."`) và toàn bộ dữ liệu trong
`structuredContent.data` — lúc đó nhằm chặn thói quen trả JSON string lộn xộn
trong `content`. Hệ quả thực tế: client chỉ render `content` cho LLM không
đọc được dữ liệu (agent phải curl thẳng JSON-RPC), còn client forward cả hai
kênh thì nhận stub vô ích.

v2 (2.0) đảo chính sách một cách chủ đích: single channel — JSON envelope có
cấu trúc trong `content[0].text`, `structuredContent` bị bỏ hẳn. Kỷ luật mà
v1 chứng minh vẫn giữ nguyên: envelope bắt buộc qua shared normalizer, error
có code ổn định, không catalog dump; thêm cap kích thước có cấu trúc để kênh
duy nhất không bị lạm dụng. Version bump từ 1.0 → 2.0 là breaking change:
client đọc `structuredContent` phải chuyển sang parse `content[0].text`.

## Nguồn chuẩn

- [MCP Tools specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [MCP schema reference 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/schema)
- [FastMCP tools and structured output](https://github.com/prefecthq/fastmcp/blob/main/docs/servers/tools.mdx)
- [FastMCP client tool results](https://github.com/prefecthq/fastmcp/blob/main/docs/clients/tools.mdx)
