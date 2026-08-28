# Chuẩn output và checklist thay đổi MCP

## Mục tiêu

Tài liệu này là chuẩn bắt buộc cho mọi tool của `graph_mcp` và `mind_mcp`.
Mục tiêu là ngăn các lỗi đã lặp lại nhiều lần:

- dữ liệu bị trả thành JSON string trong `content` thay vì object trong
  `structuredContent`;
- client coi `isError: true` là dữ liệu thành công;
- mỗi tool tự đặt một kiểu error khác nhau (`type`, `warning`, `exception`,
  `error` string);
- success, empty và error không phân biệt được bằng máy;
- output chứa nguyên `code`, `note` hoặc catalog chẩn đoán dù caller chỉ yêu
  cầu summary;
- input schema khai báo sai kiểu, ví dụ số và boolean đều thành `string`.

Các từ **MUST**, **MUST NOT**, **SHOULD** và **MAY** trong tài liệu mang nghĩa
quy chuẩn.

## Contract chung

### Phân lớp của response

MCP đã có protocol envelope riêng. Không tự tạo thêm HTTP status hoặc JSON-RPC
envelope bên trong payload.

| Lớp | Mục đích | Quy tắc |
| --- | --- | --- |
| `content` | Nội dung ngắn cho người/LLM | MUST ngắn, không chứa full payload hoặc source code |
| `structuredContent` | Dữ liệu cho máy | MUST chứa contract chuẩn bên dưới |
| `isError` | Trạng thái thực thi tool | MUST là `true` khi tool thất bại |
| `_meta` | Metadata ngoài nghiệp vụ | MUST chứa tên/version contract và tên tool |

Tên contract hiện tại:

```text
cortex.mcp.tool-result / 1.0
```

### Success có dữ liệu

```json
{
  "content": [
    {
      "type": "text",
      "text": "Success: 1 result. Read structuredContent.data."
    }
  ],
  "structuredContent": {
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
    "error": null
  },
  "isError": false,
  "_meta": {
    "contract": "cortex.mcp.tool-result",
    "contractVersion": "1.0",
    "tool": "search_functions"
  }
}
```

Quy tắc:

- `data` MUST giữ nguyên kiểu nghiệp vụ: object là object, list là list, không
  `json.dumps` rồi nhét thành string.
- Tên field nghiệp vụ MAY khác giữa các tool; envelope không được khác.
- Không lặp `ok` hoặc `error` lần hai bên trong `data`.
- Metadata như contract version, tool name và tracing SHOULD nằm trong
  `_meta`, không làm bẩn `data`.

### Success nhưng không có dữ liệu

Không có kết quả là một success hợp lệ, không phải exception.

```json
{
  "content": [
    {
      "type": "text",
      "text": "Success: 0 results. Read structuredContent.data."
    }
  ],
  "structuredContent": {
    "ok": true,
    "data": {
      "db": "procsample",
      "results": [],
      "ids": []
    },
    "error": null
  },
  "isError": false,
  "_meta": {
    "contract": "cortex.mcp.tool-result",
    "contractVersion": "1.0",
    "tool": "search_functions"
  }
}
```

MUST dùng collection rỗng đúng kiểu (`[]` hoặc `{}`). Không đổi lúc thì
`null`, lúc thì `[]`, lúc thì thiếu field.

### Tool execution error

```json
{
  "content": [
    {
      "type": "text",
      "text": "NAVIGATE is unavailable for parser cplus."
    }
  ],
  "structuredContent": {
    "ok": false,
    "data": null,
    "error": {
      "code": "capability_unavailable",
      "message": "NAVIGATE is unavailable for parser cplus.",
      "retryable": false,
      "details": {
        "missing_relationships": ["NAVIGATE"]
      }
    }
  },
  "isError": true,
  "_meta": {
    "contract": "cortex.mcp.tool-result",
    "contractVersion": "1.0",
    "tool": "find_screen_workflows"
  }
}
```

Quy tắc:

- Tool/business/input/storage error MUST dùng `isError: true`.
- `error.code` MUST là mã ổn định cho máy; không dùng nguyên exception message
  làm code.
- `error.message` MUST ngắn, an toàn và có thể hành động được.
- `error.retryable` MUST là boolean.
- `error.details` MUST là object, kể cả khi rỗng.
- Không trả `warning` thay cho error nếu request không thể thực hiện.
- Unknown tool, malformed JSON-RPC và lỗi protocol vẫn dùng JSON-RPC error;
  không ép chúng vào tool envelope.

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
    result_meta,
    result_summary,
)
```

### Template FastMCP `ToolResult`

```python
from fastmcp.tools.tool import ToolResult


def success_result(tool_name: str, data: object) -> ToolResult:
    return ToolResult(
        content=result_summary(data, ok=True),
        structured_content=normalize_success(data),
        meta=result_meta(tool_name),
        is_error=False,
    )


def error_result(tool_name: str, exc: Exception) -> ToolResult:
    payload = normalize_error(exc)
    return ToolResult(
        content=result_summary(
            None,
            ok=False,
            message=payload["error"]["message"],
        ),
        structured_content=payload,
        meta=result_meta(tool_name),
        is_error=True,
    )
```

### Template MCP SDK `CallToolResult`

```python
from mcp.types import CallToolResult, TextContent


def success_result(tool_name: str, data: object) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=result_summary(data, ok=True),
            )
        ],
        structuredContent=normalize_success(data),
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
- [ ] Success đi qua shared normalizer và đặt full data trong
  `structuredContent.data`.
- [ ] Empty là success với collection rỗng đúng kiểu.
- [ ] Tool error có `isError=true` và đủ `code/message/retryable/details`.
- [ ] `content` ngắn, không chứa full JSON, source code, secret hoặc stack trace.
- [ ] Metadata contract/tool/tracing nằm trong `_meta`.
- [ ] Nếu framework hỗ trợ output schema, schema là object và khớp response.
- [ ] Có unit test success, empty, invalid input và backend failure.
- [ ] Có test xác nhận không leak `code`/`note` khi raw fields tắt.
- [ ] Có live smoke test qua streamable HTTP, không chỉ gọi function trực tiếp.
- [ ] Client test xác nhận ưu tiên `structuredContent` và tôn trọng `isError`.
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
  tests/test_unified_mcp_input_coercion.py \
  tests/test_unified_contract_doc_paths.py \
  tests/test_framework_mcp_search.py
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
6. `content` ngắn và `structuredContent` parse được;
7. cả `graph_mcp` và `mind_mcp` dùng cùng envelope/version.

## Quy tắc cho client và report

- Client MUST đọc `isError` trước.
- Khi `isError=true`, client MUST raise/route error; không trả error dict như dữ
  liệu thành công.
- Client MUST ưu tiên `structuredContent`; chỉ parse `content` như fallback cho
  tool legacy.
- Report mặc định SHOULD ghi `structuredContent`, trạng thái và summary; không
  lặp cả serialized `content` lẫn structured object.
- Chế độ raw/full chỉ dùng khi debug protocol và phải ghi rõ output bị lặp vì
  backward compatibility.

## Nguồn chuẩn

- [MCP Tools specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [MCP schema reference 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/schema)
- [FastMCP tools and structured output](https://github.com/prefecthq/fastmcp/blob/main/docs/servers/tools.mdx)
- [FastMCP client tool results](https://github.com/prefecthq/fastmcp/blob/main/docs/clients/tools.mdx)
