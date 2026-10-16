# Phase 07: Docs + live smoke + session log

- **Size:** S
- **Depends on:** Phase 01–06
- **BlockedBy:** none

## Deliverable

1. **`docs/REPO_QUERY_RULES.md`** (mới, ngắn — mirror PROJECT_ID_QUERY_RULES): 5 rule RR1–RR5, helper list (`prepare_repo_scope_parameters`, `repo_scope_predicate`, `matches_repo_scope`), bảng anchor semantics (kết quả từ Phase 02–04), ví dụ gọi tool với repo="s3" và repo="/Users/.../s1".
2. **`docs/mcp-output-contract.md`**: section `scope` bổ sung field repo-side (additive; ⚠ rebase-cảnh báo với plan `261013` cũng sửa file này).
3. **Live smoke falkordblite** (dev default): 1 tool graph bất kỳ + `repo` exact và suffix trên store thật; ghi kết quả vào `smoke-report.md` trong plan dir. Đây là gate đóng Red-team note #3 (ENDS WITH trên falkordblite chưa từng verify).
4. **`code-tiny/mcp/Readme.md`** (nếu có mục params) + session log entry theo hi-log contract.

## Steps

1. Viết docs (RR rules + examples từ test thực tế).
2. Chạy smoke script (đơn giản: pytest-mark live hoặc script python nhỏ qua testtool client) trên falkordblite + LadybugDB (đối chiếu backend parity).
3. Session log.

## Test scope

- Mapped acceptance criteria: smoke = by-hand acceptance (không unit test mới); docs cross-check (mỗi helper nêu trong docs phải tồn tại + có test ở phase trước — checklist tay).
- Expected size: **S**.

## Red-team notes

- Nếu falkordblite fail ENDS WITH → dừng, escalate thiết kế fallback (plan.md RT#3) thay vì merge nửa mặt.
- Docs phải nêu rõ: repo KHÔNG lọc node láng giềng traversal (câu hỏi dễ nhầm nhất của người dùng mới).
