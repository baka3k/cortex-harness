---
title: "Single-channel JSON response contract v2.0 — mọi MCP tool trả một envelope JSON duy nhất trong content[0].text"
status: pending
created: 2026-10-13
mode: hi-plan --full
scope: "mcp_contract.py envelope v2.0 + structural size cap; unified_mcp.py _wrap_dispatch_result bỏ structuredContent; migration testtool client + batch report + tests; rewrite docs/mcp-output-contract.md; live smoke cả graph_mcp (8788) và mind_mcp (8789)"
blockedBy: []
blocks: []
relatedPlans:
  - 260828-1508-multi-instance-fanout-default (cùng file code-tiny/mcp/unified_mcp.py nhưng concern fan-out semantics — không đụng envelope; không blockedBy, chỉ lưu ý rebase)
  - 260727-testtool-mcp-coverage-and-ux (testtool client đang ưu tiên structuredContent — phase 02 đổi preference)
  - 260719-0100-mcp-query-capability-hardening (prior art error envelope — error codes giữ nguyên v1)
  - 260728-0000-unified-ingest-query-contract (nguồn gốc wire envelope cortex.mcp.tool-result v1.0)
---

# Single-channel JSON response contract v2.0

## Overview

Hiện nay mọi tool của `graph_mcp` (:8788) và `mind_mcp` (:8789) trả response
hai kênh: `content[0].text` chỉ là một câu stub
(`"Success: N items. Read structuredContent.data."`, sinh bởi
`result_summary` tại `cortex_harness/mcp_contract.py:233-259`) còn toàn bộ
dữ liệu nằm trong `structuredContent.data`. Assemble nằm ở 2 chỗ: graph
server wrap mọi tool qua `_wrap_dispatch_result`
(`code-tiny/mcp/unified_mcp.py:984-1037`), mind server dùng decorator riêng
`_standard_tool` (`doc-tiny/mcp_graph_rag.py:1371-1423`); ngoài ra
`fastmcp_server.py` standalone (~25 tool trả raw dict, default port 8788,
KHÔNG được dev.py launch) là điểm đang bỏ qua contract.

Hệ quả quan sát được: các agent LLM chạy trên client chỉ render `content`
không đọc được dữ liệu và tự tìm cách curl thẳng JSON-RPC endpoint; client
forward cả hai kênh (ZCode) thì nhận stub vô ích; nếu mirror full JSON vào
`content` song song với `structuredContent` thì data bị duplicate trong
context model ở chính những client đang hoạt động tốt.

Quyết định thiết kế (đã chốt với user): **single channel** — bỏ hẳn
`structuredContent`, serialize toàn bộ envelope thành JSON string trong
`content[0].text`, một shape thống nhất cho mọi tool, kèm cap kích thước
có cấu trúc (không bao giờ cắt giữa chuỗi JSON). Đánh đổi đã cân nhắc: mất
kênh machine-readable theo MCP spec; chấp nhận vì consumer thực tế chỉ là
LLM agents + testtool first-party, cả hai đều đọc được JSON từ `content`.

## Mục tiêu

1. Một envelope JSON duy nhất, giống hệt nhau cho mọi tool, cả hai server,
   cả success/empty/error.
2. `content[0].text` LUÔN parse được JSON (không bao giờ truncate giữa
   chuỗi); data nằm trong envelope, message nằm trong envelope.
3. Cap kích thước server-side có cấu trúc + hint thu hẹp query.
4. Không còn `structuredContent` trong bất kỳ response nào.
5. Tất cả first-party consumers (testtool, batch report, tests, docs) đọc
   contract mới trong cùng change set.
6. Agent instruction docs phản ánh contract mới → hết lý do curl.

## Phi mục tiêu (non-goals)

- KHÔNG đổi shape dữ liệu nghiệp vụ trong `data` (results/ids/db, scope
  block, pagination... giữ nguyên 100% như v1).
- KHÔNG đổi bảng error codes (`docs/mcp-output-contract.md:173-189`).
- KHÔNG đổi `tools/list` / input schema / tool catalog.
- KHÔNG đụng fan-out semantics, project scope, admission queue (scope của
  `260828-1508`).
- KHÔNG đổi JSON-RPC transport (unknown tool/malformed vẫn JSON-RPC error).
- KHÔNG thêm outputSchema (mất kênh typed là chủ đích của v2).

## Wire contract v2.0 (chuẩn bắt buộc mới)

### CallToolResult sau thay đổi

| Field | Quy tắc v2.0 |
| --- | --- |
| `content` | Array với ĐÚNG MỘT TextContent; `text` = ENVELOPE_JSON (JSON string, `ensure_ascii=False`) |
| `structuredContent` | MUST NOT emit (bỏ hẳn) |
| `isError` | Giữ nguyên ngữ nghĩa v1 (`true` khi tool thất bại) |
| `_meta` | Giữ nguyên: `{contract, contractVersion: "2.0", tool}` |

### ENVELOPE_JSON — shape chuẩn

```json
{
  "contract": "cortex.mcp.tool-result",
  "contractVersion": "2.0",
  "tool": "search_functions",
  "message": "Success: 2 results.",
  "ok": true,
  "data": {
    "db": "procsample",
    "results": [],
    "ids": []
  },
  "error": null,
  "truncation": null
}
```

Phân vai field (trả lời "mess đặt đâu, data đặt đâu"):

| Field | Kiểu | Vai trò |
| --- | --- | --- |
| `contract`, `contractVersion` | string | Tự mô tả envelope; trùng `_meta` (chi phí ~0, `_meta` không vào context model) |
| `tool` | string | Tên tool sinh ra response |
| `message` | string | Một câu ngắn cho người/LLM lướt nhanh — kế thừa logic `result_summary` hiện có, BỎ đuôi `"Read structuredContent.data."` |
| `ok` | bool | `true`/`false`; phân biệt máy được success/error |
| `data` | object/list/null | **Toàn bộ payload nghiệp vụ**, shape giữ nguyên v1 (search: `results`/`ids`/`db`; mind: `passages`/`scope`; pagination: `items`/`page`). Ngoại lệ duy nhất: tool text-only trả string thô → wrap `{"text": <string>}` |
| `error` | null hoặc `{code, message, retryable, details}` | Nguyên tắc v1 giữ nguyên: code ổn định, message ngắn, details nhỏ, KHÔNG catalog dump |
| `truncation` | null hoặc `{truncated, reason, cap_chars, hint}` | Có mặt khi response bị cap |

### Quy tắc bất biến

1. `content[0].text` LUÔN là JSON hợp lệ — không truncate giữa chuỗi.
2. Cap mặc định **20.000 chars**, override bằng env
   `MCP_CONTENT_MAX_CHARS` (giá trị `0` = tắt cap).
3. Truncation algorithm: serialize đủ; nếu vượt cap → thay `data` bằng
   `{"truncated": true, "preview": <min(cap - overhead, len) ký tự đầu của
   data JSON>, "note": "<hint>"}` và điền block `truncation` với
   `reason: "content_cap_exceeded"` + hint gợi ý thu hẹp query
   (`top_k`, `include_raw_fields=false`, `content_mode=summary`,
   `limit`). Overhead tính từ **template envelope đã dựng đủ** (có block
   `truncation` + `note`), clamp `preview = max(0, cap - overhead)`; nếu
   `cap < overhead` → `preview: ""`. Kết quả LUÔN là JSON hợp lệ.
4. Success-empty vẫn là success: `data` là collection rỗng đúng kiểu
   (`[]`/`{}`), không đổi thành error hay `null` tùy tiện.
5. Error: `ok=false`, `data=null`, `error` đầy đủ 4 field,
   `message = error.message`, `isError=true`. Error path CŨNG chịu cap:
   list trong `error.details` (như `violations`, `missing_relationships`)
   cắt ở 50 phần tử + flag `details_truncated: true` trong details.
6. Hai server (graph, mind) MUST dùng cùng envelope, cùng version — kiểm
   chứng bằng live smoke.

### Tương thích client

| Client | Hành vi sau v2 |
| --- | --- |
| ZCode CLI | Đọc `content[0].text` → thấy trọn envelope (trước đây thấy stub + khối structured content; giờ một khối duy nhất, không duplicate) |
| testtool `mcp_client.py` | Phase 02 đổi: parse JSON từ content trước; `isError` raise giữ nguyên; đọc `structuredContent` chỉ còn là legacy fallback |
| Agent curl JSON-RPC | Vẫn đọc được — data nay nằm trong `result.content[0].text` |
| Client text-only (loại từng gây curl) | Hết cảnh mù dữ liệu — đây là mục tiêu gốc |

## Thay đổi theo file

| File | Thay đổi | Phase |
| --- | --- | --- |
| `cortex_harness/mcp_contract.py` | `MCP_CONTRACT_VERSION` → `"2.0"` (:14); thêm serializer envelope v2 + structural cap; `result_summary` sinh `message` không đuôi cũ; `normalize_success`/`normalize_error` giữ nguyên làm building block | 01 |
| `code-tiny/mcp/unified_mcp.py` | `_wrap_dispatch_result` (:984-1037): build ENVELOPE_JSON, `ToolResult(content=<json>, structured_content=None, is_error=...)` — phủ 39 tool (24 proxied + 15 direct, middleware attach :1163) | 01 |
| `doc-tiny/mcp_graph_rag.py` | `_standard_tool` (:1371-1423) build `CallToolResult` trực tiếp cho 5 tool mind — chuyển sang shared serializer v2; audit phần còn lại của file có đọc `structuredContent` | 01 |
| `code-tiny/mcp/tool_error_middleware.py` + `code-tiny/mcp/fastmcp_server.py` | Exception path (:108-112, chỉ install trên standalone server :2772) đi qua shared serializer; audit ~25 tool raw-dict của standalone server: conform qua helper (mặc định) hoặc đề xuất retire nếu không còn entry point | 01 |
| `code-tiny/testtool/mcp_client.py` | `call_tool` (:127-160): ưu tiên parse JSON từ content text | 02 |
| `code-tiny/testtool/mcp_batch_report.py` | Extract từ envelope v2; THAY rule "content ≤1000 chars" (:297-300) xung đột; bump `CONTRACT_VERSION` (:31) đồng bộ `1.0→2.0` | 02 |
| `tests/test_mcp_output_contract.py` | Rewrite cho v2 (envelope, truncation, no-structuredContent) | 01 |
| `tests/test_mcp_testtool_client.py`, `tests/test_mcp_batch_report.py`, `tests/test_semantic_graph_scope_block.py`, `tests/test_unified_mcp_input_coercion.py`, `tests/test_unified_contract_doc_paths.py` | Migrate assertion từ `structured_content` sang content JSON | 01-02 |
| `docs/mcp-output-contract.md` | Rewrite chuẩn v2 (bảng contract, 3 ví dụ wire, template code, client rules, mục lịch sử v1→v2) | 03 |
| `docs/specs/mcp.md`, `code-tiny/mcp/Readme.md`, `code-tiny/testtool/mcp-batch-report-guide.md` | Sửa ví dụ/tham chiếu structuredContent | 03 |
| `docs/plans/260828-1508-multi-instance-fanout-default/plan.md`, `docs/plans/260727-testtool-mcp-coverage-and-ux/plan.md` | Thêm 1 dòng relatedPlans trỏ về plan này | 03 |

## Cross-plan dependency

- **260828-1508-multi-instance-fanout-default** [pending]: cùng đụng
  `unified_mcp.py` (vùng `_ProxyMiddleware`) nhưng concern rời nhau
  (fan-out semantics vs envelope). Không `blockedBy`. Lưu ý rebase:
  phase 01 sửa đúng method `_wrap_dispatch_result` mà plan đó không đụng
  đến — xung đột chỉ có thể phát sinh ở import block.
- **260727-testtool-mcp-coverage-and-ux** [implemented]: đã thiết kế
  testtool client ưu tiên `structuredContent`. Phase 02 của plan này đảo
  preference — ghi cross-ref vào plan đó để ai đọc lại không ngạc nhiên.
- **260719-0100-mcp-query-capability-hardening**: error codes + quy tắc
  "không catalog dump" giữ nguyên — plan này KHÔNG đụng bảng codes.

## Scope challenge (3 câu hỏi — tự trả lời từ directive user)

1. **Breaking change có được phép không?** Được. User yêu cầu "toàn bộ
   các response từ mcp cần theo 1 cấu trúc format json chuẩn giống hệt
   nhau" → version bump `1.0 → 2.0`, một change set duy nhất, mọi
   first-party consumer cập nhật trong plan. Không có deprecation window.
2. **Có giữ `structuredContent` song song không?** Không (user chốt "bỏ
   luôn"). Mirror gây duplicate trong ZCode; không có client nào ĐỌC
   `structuredContent` ngoài testtool first-party (consumer inventory
   phase 02). Lưu ý: `dev mcp integrate` có enroll client ngoài
   (Claude Desktop/VS Code/Cursor) nhưng chỉ ghi config URL — phase 03
   thêm 1 dòng docs về thay đổi hành vi cho client ngoài.
3. **Payload vượt cap thì sao?** Structural truncation: envelope vẫn
   valid JSON, `data` thay bằng preview + block `truncation`, hint thu
   hẹp query. Không bao giờ trả JSON cắt cụt, không bao giờ trả error
   thay cho success khi chỉ là vấn đề kích thước.

## Risks & mitigations

| Rủi ro | Mitigation |
| --- | --- |
| FastMCP tự derive `structuredContent` | GUARDED BY TEST (không "loại vĩnh viễn" — fastmcp chưa pin, `requirements.txt:17`): middleware luôn wrap nên auto-generate bị bypass (`tools/base.py:422-426`); `structured_content=None` → wire KHÔNG có field (`exclude_none`, `mcp/shared/peer.py:57`). Phase 01 pin `fastmcp==4.0.4` + test chặn regression |
| fastmcp_server.py standalone (~25 tool raw, port 8788) ngoài contract | Phase 01 audit: conform qua shared helper là mặc định; nếu xác nhận không còn entry point (dev.py không launch) → đề xuất retire trong follow-up plan |
| Error-middleware path thiếu `_meta` hiện tại | Phase 01 chuẩn hóa qua helper — v2 mọi path đều có meta |
| Token bloat ở client text-only (trước đây chỉ thấy stub) | Đó là chủ đích (chúng đang mù dữ liệu); cap 20k + truncation chặn trường hợp cực đoan; fastmcp `ResponseLimitingMiddleware` KHÔNG dùng được (byte-cut làm gãy JSON — researcher xác nhận) |
| `docs/mcp-output-contract.md:8-9` từng liệt kê "JSON string trong content" là BUG cần ngăn | Phase 03 rewrite doc kèm mục "v1 → v2" giải thích đảo chính sách: v1 chống JSON-string do stub hóa; v2 chủ đích single-channel với envelope có cấu trúc |
| Test assert trên `structured_content` rải rác | Inventory đầy đủ trong `research/repository-findings.md` (mcp_client :127-160, batch_report validate_contract :265-339, 5 test file, 14 reads trong doc-paths test); phase 02 migrate từng file + gate bằng bộ test tối thiểu |
| Curl-agents quen field `structuredContent` | Docs v2 ghi rõ data nay ở `content[0].text` (JSON); agents curl vẫn đọc được ngay field đầu |
| `_MCP_FUNCTIONS_JSON` catalog có thể embed guidance stale về structuredContent (chưa audit) | Phase 02 audit + cập nhật |

## Acceptance criteria (global)

- **AC1**: mọi `tools/call` từ cả 2 server trả `content[0].text` parse
  được JSON với đủ 8 field envelope; KHÔNG có `structuredContent`.
- **AC2**: live smoke 4 tình huống (success có data, success empty,
  invalid input error, capability error) cho thấy envelope identical
  shape trên cả 8788 và 8789.
- **AC3**: payload vượt cap → envelope vẫn valid JSON + block
  `truncation` đúng shape.
- **AC4**: testtool + batch report chạy xanh với client parse mới.
- **AC5**: `docs/mcp-output-contract.md` phản ánh v2, không còn quy tắc
  "content MUST ngắn".

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

Sau unit test — live smoke theo checklist contract doc (restart thật cả
2 server, `scripts/mcp-lifecycle.py doctor`, so sánh output trước/sau).

## Research artifact

Digest chi tiết blast radius:
[research/repository-findings.md](research/repository-findings.md)
(spawned researcher, delegation contract mode=spawn).
