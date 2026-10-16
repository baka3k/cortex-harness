# Phase 03: Backend `cplus_mcp.py` — repo param + predicate

- **Size:** L
- **Depends on:** Phase 01
- **BlockedBy:** none

## Deliverable

Tương tự Phase 02 cho backend C++: mọi tool query đọc trong `code-tiny/mcp/cplus/cplus_mcp.py` nhận `repo` và áp `repo_scope_predicate` lên anchor. Backend này gồm phần lớn 13 fan-out search tools + planner tools (theo research-digest). `_run_cypher_first` wrapper (`cplus_mcp.py:1281-1282`) là điểm đi qua chung — đánh giá xem inject params + scope block có thể gói tại wrapper (thì mỗi tool chỉ cần signature + predicate trong template) hay phải per-tool.

## Anchor rules

Như Phase 02: search → node kết quả; callers/callees/contains → anchor phía node được hỏi; **không bao giờ cả 2 endpoint** của quan hệ. Bảng per-tool do implementer hoàn thiện khi sửa.

## Steps

1. Signature `repo: Optional[str] = None` cho mọi tool đọc.
2. Chèn predicate vào template (đếm trước/sau như Phase 02).
3. Params chain qua helper Phase 01 tại điểm gọi `_run_cypher_first` nếu được, nếu không thì tại mỗi call site.
4. Scope block + warnings mirror như Phase 02 — nếu gói được ở wrapper thì shape nhất quán tự động.

## Files

- `code-tiny/mcp/cplus/cplus_mcp.py`
- `tests/test_repo_scope_cplus.py` (mới; dùng RecordingRunner / fake driver pattern hiện có của backend cplus)

## Test scope

- Mapped acceptance criteria: RR1/RR2/RR3 trên 1 tool fan-out search đại diện + 1 tool quan hệ (callers) đại diện + wrapper param-injection test.
- Expected size: **M** (~6–8 test functions).
- Chạy: pytest file mới + regression test cplus hiện có.

## Red-team notes

- Wrapper-level injection nếu chọn: đảm bảo tool KHÔNG nhận repo (trường hợp đặc thù) không bị gán params thừa gây lỗi unknown-param ở driver — chỉ inject khi signature có `repo`.
- Cplus nodes ghi `repo` dạng `{project_name}/{basename}` (qua incremental_sync) — chính là flavor suffix-match phục vụ; test phải gồm cả flavor này.
