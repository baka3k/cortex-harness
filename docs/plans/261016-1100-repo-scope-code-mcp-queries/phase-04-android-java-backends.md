# Phase 04: Backend `android_mcp.py` + `java_mcp.py` — repo param + predicate

- **Size:** M
- **Depends on:** Phase 01
- **BlockedBy:** none

## Deliverable

Tương tự Phase 02/03 cho 2 backend còn lại: `code-tiny/mcp/android/android_mcp.py` và `code-tiny/mcp/java/java_mcp.py`. Surface nhỏ hơn cplus (mỗi backend vài tool query + `_resolve_db_candidates` fan-out đã theo mẫu PROJECT_ID_QUERY_RULES §6).

## Steps

1. Signature + predicate + params chain + scope block/warnings — y hệt Phase 02 steps 1–5.
2. `_resolve_db_candidates` không đổi (fan-out theo project, không theo repo — repo là property filter trong từng graph, không phải selector target).

## Files

- `code-tiny/mcp/android/android_mcp.py`
- `code-tiny/mcp/java/java_mcp.py`
- `tests/test_repo_scope_android_java.py` (mới)

## Test scope

- Mapped acceptance criteria: RR1/RR2/RR3 — 1 tool đại diện mỗi backend (search + 1 quan hệ).
- Expected size: **M** (~6 test functions, parameterize chéo 2 backend).
- Chạy: pytest file mới + regression android/java hiện có.

## Red-team notes

- Java/android templates đôi khi trùng lặp query text giữa 2 file (copy-paste origin) — đếm template từng file độc lập, không giả định số lượng bằng nhau.
- Anchor bảng per-tool như Phase 02 — review đối chiếu trước khi merge (plan.md RT#6).
