# Phase 06: Vector tools — over-fetch + Python post-filter

- **Size:** M
- **Depends on:** Phase 01 (helper), Phase 05 (surface hoàn chỉnh trước khi đụng response shape chung)
- **BlockedBy:** none

## Deliverable

`semantic_search` và `explore_graph` (hybrid) nhận `repo: Optional[str] = None` và lọc kết quả vector theo RR5:

- `repo` blank/None → không đổi.
- Có repo → **over-fetch** `limit × 4` (const `REPO_POSTFETCH_FACTOR = 4`) rồi post-filter bằng Python predicate **chung** với graph side: exact `== repo` hoặc `.endswith("/" + repo)` — đặt predicate Python này trong `project_scope.py`Phase 01 (`matches_repo_scope(value, repo)`) để graph test và vector test dùng chung ngữ nghĩa.
- Không dùng native Qdrant filter cho repo (payload có `repo` — `primary_vector_sync.py:136` — nhưng `match.any` chỉ diễn đạt exact; một code path duy nhất cho cả hai semantics).
- `scope` block mở rộng cho vector: `{"repo", "filter_applied", "pre_filter_hits", "post_filter_hits"}` + warning khi `post_filter_hits == 0` hoặc khi over-fetch cạn (tất cả fetched đều bị lọc → gợi ý tăng limit hoặc thu hẹp bằng project_id + repo).

## Steps

1. `matches_repo_scope` vào Phase 01 file (unit test tại Phase 01 hoặc bổ sung ở đây — 1 test).
2. Áp vào 2 tool; giữ nguyên `collections_searched` và mọi field hiện có (no-silent-semantic-change).
3. Docstring + catalog entry cho 2 tool (nếu Phase 05 chưa gộp).

## Files

- `code-tiny/tools/common/project_scope.py` (thêm `matches_repo_scope`)
- Tool definitions của `semantic_search` / `explore_graph` (vị trí chính xác theo research-digest: unified direct tools / fastmcp — implementer xác minh theo registry)
- `tests/test_repo_scope_vector.py` (mới)

## Test scope

- Mapped acceptance criteria: RR5 — over-fetch + filter đúng; hits trước/sau báo đúng; unscoped không đổi hành vi (regression); "xs3" không lọt.
- Expected size: **M** (~5–7 test functions; fake store trả payload có `repo`).
- Chạy: pytest file mới + regression semantic_search hiện có (`tests/test_semantic_graph_scope_block.py` là tham chiếu pattern mind-side).

## Red-team notes

- Over-fetch factor là trade-off documented: repo nhỏ ⇒ có thể vẫn thiếu; KHÔNG tự tăng factor động (độ phức tạp không mua gì) — warning là đủ.
- Post-filter chạy TRƯỚC khi format content_mode=summary — không được để node đã lọc lọt vào summary counts.
