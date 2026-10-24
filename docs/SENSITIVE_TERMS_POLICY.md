# Sensitive terms policy — chính sách từ nhạy cảm

> **Tóm tắt:** Tên dự án / khách hàng / đối tác / nhân sự **tuyệt đối không
> được xuất hiện trong git** của repo này — không nằm trong code, tài liệu,
> tên file, commit message, hay dữ liệu mẫu. Danh sách term cụ thể nằm ở
> `.sensitive-terms` **ở máy local** (file này bị gitignore vì bản thân nó
> phải chứa term để guard chạy). Pre-commit hook tự chặn commit vi phạm.

## Quy tắc

1. **Không bao giờ commit** term nhạy cảm hoặc bất kỳ keyword/identifier
   nào của dự án thật dưới mọi dạng (đủ mọi vị trí: nội dung file, comment,
   tên biến/fixture trong unit test, đường dẫn mẫu, tên tài liệu/màn hình/
   module/symbol/control lấy từ live DB, `docs/plans/`, `docs/logs/`,
   commit message, dữ liệu testdata).
2. **Quy tắc bắt buộc cho mọi AI Agent & Harness (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`)**:
   - Khi debug hoặc kiểm thử trên dữ liệu thật trong Qdrant / FalkorDB /
     LadybugDB, Agent **tuyệt đối không được copy nguyên văn** tên file,
     đường dẫn, tên màn hình/chức năng, tên biến/control/hàm, hay tiêu đề
     tài liệu của dự án thật vào unit test (`tests/`), comment, plan
     (`docs/plans/`), hay session log (`docs/logs/`).
   - Bắt buộc đổi toàn bộ ví dụ/fixture sang định danh giả lập trung tính
     (ví dụ: `project-x`, `fixt`, `src/orders/OrderSummaryForm.vb`,
     `grid_unit_col`, `specs/order_form_spec.xlsx`, "the live project",
     "the deployment") — **không** dùng term thật kèm `# sensitive-guard:allow`.
3. `.sensitive-terms` là file **local-only** (đã gitignore). Không commit
   nó, không xoá nó — nó là nguồn của hook chặn commit. Nếu clone máy mới
   mà thiếu file này, tạo lại theo mẫu ở dưới và điền term thật.
4. Dữ liệu binary (PDF/docx testdata) **không** được chứa tên dự án trong
   nội dung hiển thị; guard không đọc được binary một cách đáng tin.
5. File untracked trong thư mục repo cũng phải sạch trước khi `git add`
   (untracked hôm nay có thể thành tracked ngày mai).

## Khi hook chặn commit

Hook (`sensitive-guard`) in ra term + file + dòng vi phạm. Cách đúng:

- Thay term bằng placeholder trung tính ở **cùng ngữ cảnh** (giữ ý nghĩa
  kỹ thuật), rồi commit lại.
- **Cấm** `git commit --no-verify` để "đi tiếp" — bypass làm rò rỉ vĩnh
  viễn vào lịch sử.

## Nếu đã lỡ commit_term vào lịch sử

Quy trình đã dùng cho repo này (xem commit "purge sensitive terms"):

1. Backup ngoài repo: `git bundle create ../backup-<date>.bundle --all`.
2. `git filter-repo` với `--blob-callback` (chỉ quét text blob — skip
   blob chứa byte `\0` — và word-boundary regex, tránh hỏng PDF) +
   `--commit-callback` cho commit message.
3. `git reflog expire --expire=now --all && git gc --prune=now --aggressive`.
4. Force-push **có phối hợp** với mọi người dùng repo (tất cả commit hash
   đổi; mọi clone cũ phải re-clone).

## Mẫu `.sensitive-terms`

```
# .sensitive-terms — managed by sensitive-guard-cli
# Plain terms match whole words, case-insensitively
# Prefix case: for case-sensitive matching
# Use * as wildcard for substring matching
<ten-du-an-khach-hang>
<ten-nhan-su-username>
<ten-doi-tac>
```

Điền term thật vào máy local; không bao giờ commit file này.
