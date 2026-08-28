# Hướng dẫn batch test MCP và tạo báo cáo Markdown

## Mục đích

`mcp_batch_report.py` chạy một danh sách MCP tool với input cố định được khai
báo trong suite JSON. Sau mỗi lần chạy, tool tạo một file Markdown chứa đầy
đủ:

- inventory tool lấy trực tiếp từ từng MCP server;
- description, input schema và output schema từ `tools/list`;
- input thực tế gửi vào từng tool;
- raw JSON-RPC output đã parse, kể cả response có `isError: true`;
- thời gian chạy, trạng thái, error code và kết quả kiểm tra contract;
- so sánh kết quả thực tế với expected outcome trong suite.

Batch runner không coi `SUCCESS_EMPTY` là lỗi. Một `TOOL_ERROR` cũng có thể
PASS nếu suite chủ động khai báo error code đó là kết quả mong đợi.

## Các file liên quan

| File | Vai trò |
| --- | --- |
| `mcp_batch_report.py` | Batch runner và Markdown report generator |
| `mcp_client.py` | MCP streamable HTTP client và raw JSON-RPC call |
| `suites/procsample-all-tools.json` | Suite cố định cho 39 graph tool và 5 mind tool |
| `outputs/` | Thư mục output mặc định nếu không truyền `--output` |

## Điều kiện trước khi chạy

Chạy lệnh từ thư mục gốc `cortex-harness` và bảo đảm hai MCP server đang hoạt
động:

```bash
./.venv/bin/python cortex_harness/dev.py mcp start \
  --project-dir /Users/hieplq1.aip/Migration/procsample
```

Kiểm tra trạng thái storage và MCP:

```bash
cd /Users/hieplq1.aip/Migration/procsample
/Users/hieplq1.aip/AI/cortex-harness/.venv/bin/python \
  /Users/hieplq1.aip/AI/cortex-harness/cortex_harness/dev.py doctor
```

## Chạy suite `procsample`

Tạo report vào thư mục `output_porting` với tên có timestamp:

```bash
cd /Users/hieplq1.aip/AI/cortex-harness

./.venv/bin/python code-tiny/testtool/mcp_batch_report.py \
  --suite code-tiny/testtool/suites/procsample-all-tools.json \
  --output /Users/hieplq1.aip/Migration/procsample/output_porting
```

Ghi vào một file xác định:

```bash
./.venv/bin/python code-tiny/testtool/mcp_batch_report.py \
  --suite code-tiny/testtool/suites/procsample-all-tools.json \
  --output /Users/hieplq1.aip/Migration/procsample/output_porting/mcp-latest.md
```

Ẩn progress của từng tool khi chạy trong automation:

```bash
./.venv/bin/python code-tiny/testtool/mcp_batch_report.py \
  --suite code-tiny/testtool/suites/procsample-all-tools.json \
  --output /Users/hieplq1.aip/Migration/procsample/output_porting \
  --quiet
```

Các option:

| Option | Ý nghĩa |
| --- | --- |
| `--suite PATH` | File suite JSON; mặc định là suite `procsample-all-tools` |
| `--output PATH` | File `.md` cụ thể hoặc thư mục nhận report timestamped |
| `--timeout SECONDS` | Timeout HTTP cho mỗi MCP call; mặc định `120` giây |
| `--quiet` | Không in progress từng tool |

## Cấu trúc suite JSON

Suite gồm metadata chung và danh sách server. Mỗi server có endpoint và một
danh sách case theo đúng thứ tự cần chạy.

```json
{
  "name": "sample-all-tools",
  "project": "sample-project",
  "parser": "proc",
  "servers": [
    {
      "name": "graph_mcp",
      "endpoint": "http://127.0.0.1:8788/mcp",
      "require_full_inventory": true,
      "cases": [
        {
          "tool": "search_functions",
          "input": {
            "query": "batch_job_run|run_step|main",
            "project_id": "sample-project",
            "parser_type": "proc",
            "limit": 20,
            "content_mode": "summary",
            "include_raw_fields": false
          },
          "expected_status": "SUCCESS"
        }
      ]
    }
  ]
}
```

### Field cấp suite và server

| Field | Bắt buộc | Ý nghĩa |
| --- | --- | --- |
| `name` | Có | Tên suite; được dùng trong tên report mặc định |
| `project` | Có | Project được kiểm thử và hiển thị trong report |
| `parser` | Có | Parser chính của suite |
| `servers` | Có | Danh sách MCP server cần kiểm thử |
| `servers[].name` | Có | Tên logic, ví dụ `graph_mcp`, `mind_mcp` |
| `servers[].endpoint` | Có | Streamable HTTP MCP endpoint |
| `servers[].require_full_inventory` | Không | Nếu `true`, mọi live tool phải có đúng một case cố định |
| `servers[].cases` | Có | Danh sách case chạy theo thứ tự |

### Field cấp case

| Field | Bắt buộc | Ý nghĩa |
| --- | --- | --- |
| `tool` | Có | Tool name đúng như `tools/list` trả về |
| `input` | Có | JSON object gửi nguyên trạng vào `tools/call.arguments` |
| `expected_status` | Không | Mặc định `SUCCESS` |
| `expected_error_code` | Không | Error code cần khớp khi mong đợi `TOOL_ERROR` |

## Input mẫu

### Graph MCP: success có dữ liệu

```json
{
  "tool": "search_functions",
  "input": {
    "query": "batch_job_run|run_step|main",
    "project_id": "procsample",
    "parser_type": "proc",
    "limit": 20,
    "content_mode": "summary",
    "include_raw_fields": false
  },
  "expected_status": "SUCCESS"
}
```

### Graph MCP: success nhưng có thể không có dữ liệu

`SUCCESS` chấp nhận cả `SUCCESS_DATA` và `SUCCESS_EMPTY`:

```json
{
  "tool": "get_ipc_message",
  "input": {
    "sender": "batch_job_run",
    "receiver": "batch_control_finish",
    "project_id": "procsample"
  },
  "expected_status": "SUCCESS"
}
```

Nếu bắt buộc kết quả phải rỗng, khai báo chính xác:

```json
{
  "tool": "get_ipc_message",
  "input": {
    "sender": "unknown_sender",
    "receiver": "unknown_receiver",
    "project_id": "procsample"
  },
  "expected_status": "SUCCESS_EMPTY"
}
```

### Graph MCP: lỗi capability là kết quả mong đợi

Parser `proc` không có relationship `EXPOSES_ENDPOINT`, vì vậy case dưới đây
phải trả `TOOL_ERROR/capability_unavailable` và vẫn được đánh dấu PASS:

```json
{
  "tool": "get_endpoints",
  "input": {
    "project_id": "procsample",
    "parser_type": "proc",
    "protocol": "http",
    "offset": 0,
    "limit": 50
  },
  "expected_status": "TOOL_ERROR",
  "expected_error_code": "capability_unavailable"
}
```

### Mind MCP: semantic search

```json
{
  "tool": "semantic_search",
  "input": {
    "query": "batch job checkpoint commit restart",
    "project_id": "procsample",
    "top_k": 5,
    "max_passage_chars": 500,
    "include_entity_ids": true,
    "include_entity_mentions": false
  },
  "expected_status": "SUCCESS"
}
```

### Tool không cần input

Input vẫn phải là một JSON object:

```json
{
  "tool": "list_databases",
  "input": {},
  "expected_status": "SUCCESS"
}
```

## Các expected status

| Expected status | Điều kiện PASS |
| --- | --- |
| `SUCCESS` | Response là `SUCCESS_DATA` hoặc `SUCCESS_EMPTY` |
| `SUCCESS_DATA` | Tool thành công và có business data |
| `SUCCESS_EMPTY` | Tool thành công nhưng business collection rỗng |
| `TOOL_ERROR` | MCP trả `isError: true`; error code phải khớp nếu được khai báo |
| `ANY` | Không so sánh outcome, nhưng response vẫn phải đúng contract |

Trạng thái hạ tầng `PROTOCOL_ERROR` và `CLIENT_EXCEPTION` luôn làm case FAIL.

## Contract được kiểm tra tự động

Mỗi response phải có:

```json
{
  "jsonrpc": "2.0",
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "tool_name"
    },
    "content": [
      {
        "type": "text",
        "text": "Nội dung ngắn gọn."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {},
      "error": null
    },
    "isError": false
  }
}
```

Với lỗi tool:

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "capability_unavailable",
    "message": "Parser 'proc' cannot execute 'get_endpoints'.",
    "retryable": false,
    "details": {
      "parser": "proc",
      "missing_relationships": [
        "EXPOSES_ENDPOINT"
      ]
    }
  }
}
```

Batch runner báo contract FAIL nếu error response làm lộ catalog/diagnostics
nội bộ như:

- `available_labels`, `available_relationships`;
- `accepted_params`, `required_params`, `received_params`;
- `supported_parsers`, `supported_aliases`;
- `capability_diagnostics` hoặc backend `context` đầy đủ.

## Cách đọc report

Phần `Summary` cho biết:

- tổng số live tool và số case đã chạy;
- số `SUCCESS_DATA`, `SUCCESS_EMPTY`, `TOOL_ERROR`;
- số case PASS/FAIL và error code;
- inventory có khớp suite hay không.

Phần `Execution index` giúp tìm nhanh tool fail. Mỗi section phía dưới chứa:

1. actual status và expected status;
2. contract result;
3. schema quảng bá bởi MCP server;
4. input đã gửi;
5. raw parsed JSON-RPC output.

Report đầy đủ có thể lớn vì lưu toàn bộ success data theo yêu cầu. Error
response vẫn phải nhỏ và chỉ chứa thông tin cần thiết để client xử lý.

## Tạo suite cho project mới

1. Copy suite hiện tại:

   ```bash
   cp code-tiny/testtool/suites/procsample-all-tools.json \
     code-tiny/testtool/suites/my-project-all-tools.json
   ```

2. Đổi `name`, `project`, `parser` và endpoint nếu cần.
3. Thay toàn bộ `project_id`, `parser_type`, query và ID node bằng dữ liệu của
   project mới.
4. Chạy suite với `require_full_inventory: true`.
5. Kiểm tra report; chỉ khai báo `TOOL_ERROR` khi lỗi đó thực sự là hành vi
   mong đợi của parser/corpus.

Không dùng `expected_status: "ANY"` để che lỗi lâu dài. Chỉ dùng tạm thời khi
khảo sát một tool có outcome phụ thuộc corpus.

## Checklist khi thêm, sửa hoặc xoá MCP tool

### Thêm tool

- Thêm một case có input chạy được vào mọi suite áp dụng.
- Giữ `require_full_inventory: true` để phát hiện tool chưa có fixture.
- Khai báo expected status và error code rõ ràng.
- Chạy batch report và kiểm tra contract PASS.
- Xác nhận `content` ngắn, dữ liệu đầy đủ nằm trong `structuredContent.data`.

### Sửa tool hoặc schema

- Cập nhật input fixture theo schema mới.
- Không đổi error code ổn định nếu chưa có migration cho client.
- Chạy lại suite và xem raw input/output trong report.
- Kiểm tra response không đưa diagnostics nội bộ vào `error.details`.

### Xoá hoặc đổi tên tool

- Xoá hoặc đổi tên case tương ứng trong suite.
- Chạy lại với `require_full_inventory: true`.
- Không để case stale hoặc live tool thiếu fixture.

## Lưu ý về dữ liệu thay đổi

Một số case dùng node ID, source ID hoặc paragraph ID cố định. Sau khi ingest
lại project, các ID này có thể thay đổi. Khi đó batch runner vẫn tạo report
nhưng case sẽ FAIL; cập nhật suite bằng ID mới có bằng chứng từ corpus.

Suite hiện tại có case `annotate_node`. Mỗi lần chạy nó ghi lại đúng bộ
`note`, `tags`, `severity` đã khai báo. Chỉ dùng node test hoặc giá trị
idempotent; không trỏ case này vào annotation do người dùng quản lý.

## Exit code

| Code | Ý nghĩa |
| ---: | --- |
| `0` | Inventory, expectations và contract đều PASS |
| `1` | Có case FAIL hoặc inventory mismatch; report vẫn được tạo |
| `2` | Suite/config/output không hợp lệ hoặc không ghi được report |
| `130` | Người dùng ngắt bằng Ctrl+C; không tạo partial report |

## Troubleshooting

### Không kết nối được MCP

- Chạy `dev.py doctor` từ thư mục project.
- Kiểm tra endpoint `8788/mcp` và `8789/mcp` trong suite.
- Khởi động lại bằng `dev.py mcp start --force-restart` nếu cần.

### Inventory mismatch

- Live tool mới nhưng chưa có case: thêm fixture với input cố định.
- Case còn tồn tại nhưng tool đã bị xoá/đổi tên: sửa suite.
- Không tắt `require_full_inventory` chỉ để bỏ qua drift ngoài ý muốn.

### Expected outcome mismatch

- So sánh `Expected status`, `Actual status` và raw output trong section của
  tool.
- Kiểm tra corpus/parser trước khi thay expected outcome.
- Không chuyển lỗi bất ngờ thành expected error nếu chưa xác nhận đó là hành
  vi đúng.

### Contract FAIL

- Xem danh sách `Contract violations` trong section tương ứng.
- Sửa output ở shared MCP boundary thay vì vá từng report hoặc từng client.
- Chạy lại toàn bộ suite để bảo đảm graph và mind MCP cùng tuân theo chuẩn.
