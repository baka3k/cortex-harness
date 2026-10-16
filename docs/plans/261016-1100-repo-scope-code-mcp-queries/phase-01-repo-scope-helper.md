# Phase 01: Repo-scope helper trong `project_scope.py`

- **Size:** S
- **Depends on:** none (root của mọi phase khác)
- **BlockedBy:** none

## Deliverable

Helper duy nhất cho predicate `repo` trên READ path, đặt cạnh các helper project scope hiện có trong `code-tiny/tools/common/project_scope.py`:

1. `prepare_repo_scope_parameters(parameters: Mapping) -> Dict[str, Any]`
   - key `repo` có mặt và **non-blank** → inject sibling `repo_suffix = "/" + repo.strip()`.
   - key `repo` vắng mặt / blank/whitespace → **không inject** gì (Cypher `$repo IS NULL`… không đúng — `repo` vẫn phải được normalize thành `None` để predicate no-op; làm tròn blank → `None` trước khi inject, mirror cách `project_id` xử lý blank).
2. `repo_scope_predicate(alias: str) -> str`
   - Trả fragment: `($repo IS NULL OR {alias}.repo = $repo OR {alias}.repo ENDS WITH $repo_suffix)` — dùng lại xuyên suốt Phase 02–06, cấm viết tay fragment khác.
3. Hằng `REPO_SCOPE_PARAMETER_KEYS = ("repo",)` theo pattern `_PROJECT_SCOPE_PARAMETER_KEYS`.

**Không đụng** `prepare_project_scope_parameters`, `qdrant_project_filter`, hay bất kỳ predicate `project_id_normalized` nào (R6).

## Files

- `code-tiny/tools/common/project_scope.py` — thêm helper.
- `code-tiny/tools/common/test_project_scope.py` — unit tests (hoặc file mới `test_repo_scope.py` cùng thư mục nếu file hiện tại quá dài).

## Steps

1. Thêm 3 export trên + `__all__` update.
2. Unit tests table-driven, map theo test-budget ({typical, boundary, invalid}):
   - typical: `repo="s3"` → params có `repo="s3"`, `repo_suffix="/s3"`; predicate đúng chuỗi.
   - boundary: `repo=""` / `"   "` → `repo=None`, không có `repo_suffix`; predicate alias `n`/`node`/`s` render đúng.
   - invalid: `repo=None` → giống unscoped; alias chứa ký tự đặc biệt không cần escape (fragment là template, alias là định danh tĩnh trong query — assert không interpolate giá trị user).
   - integration-light: 1 test ghép `prepare_project_scope_parameters` + `prepare_repo_scope_parameters` trên cùng params dict (không ghi đè lẫn nhau).

## Test scope

- Mapped acceptance criteria: RR3, RR4 (plan.md Contract changes).
- Expected size: **S** (~4–6 test functions, table-driven).
- Chạy: `pytest code-tiny/tools/common/test_project_scope.py` (hoặc test_repo_scope.py) + bộ merge-gate hiện có của project_scope.

## Red-team notes

- Blank `repo` phải thành `None` **trước** khi inject sibling — nếu chỉ skip suffix mà để `repo="  "` thì `= $repo` không match gì nhưng `ENDS WITH "/  "` cũng vô nghĩa → results sai kiểu "rỗng bí ẩn". Assert test này.
- Không dùng regex/`CONTAINS` — suffix phải neo bằng `/` để tránh `s3` khớp `xs3` (quyết định thiết kế, có test chốt hành vi).
