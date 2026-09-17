# Phase 04 — Comment Extraction (Hidden Channel) + Embedding Enrichment

> Gate ra: M5 (≥80% declaration có comment liền kề mang `comment != ""` trong payload VÀ Qdrant point payload — fake-driver assert).

## Tasks

### 4.1 Worker: comment attach

- Nguồn: `module.getTokens()` (CommonTokenStream chứa COMMENT hidden channel — g4:2077 `COMMENT ... -> channel(HIDDEN)`; research §4 xác nhận token stream được giữ trên module).
- Thuật toán (Java, util mới trong worker):
  - Gom comment token theo line; comment "liền kề" declaration khi: (a) cùng dòng với dòng `start_line` của declaration (trailing), hoặc (b) các dòng PHÍA TRÊN liền kề (không dòng trống ngăn cách), bắt đầu từ dòng ngay trên `start_line`.
  - Nhiều comment khối → nối `"\n"`. Strip leading `'`/`Rem `.
  - Điền vào `comment` (và `summary = comment` nếu summary rỗng) cho: functions (mọi kind), classes/interfaces, enums/constants/events/declares (plane phase-01).
- `file_def.comment` giữ "" (file-level note không thuộc scope này).

### 4.2 Python: payload → Qdrant

- Mapping Qdrant (`vb_analyzer_base.py:1249-1497`) đã đọc `comment/summary/note` — verify không cần đổi code ngoài plane mới (phase-01 đã thêm). Assert bằng test thay vì sửa.
- Embedding text = `note or code` (research §2): với plane mới có comment → note được điền → embedding tự giàu. KHÔNG đổi embedding model/pipeline.

### 4.3 Tests

- Fixture gài comment: block comment 3 dòng phía trên 1 function, trailing comment 1 property get, comment trên enum.
- Contract test: payload `comment` đúng nội dung (sanitize so sánh); fake-driver Qdrant point payload chứa comment text (pattern `CapturingDriver`/fake qdrant như graph contract hiện hành).
- Regex path KHÔNG đổi — và KHÔNG assert parity comment với regex (red-team F7: regex hiện chỉ extract `FileDef.comment`, không có per-procedure comment; ANTLR là comment-authoritative, không đòi bằng nhau).

## Định nghĩa xong

- [ ] M5: ≥80% planted comments trong payload + Qdrant payload
- [ ] Hidden-channel comment không làm lệch line/column payload (golden line-number vẫn xanh)
- [ ] Perf: worker elapsed_ms không vượt absolute budget trên fixture replicate (đo nhanh, chính thức ở phase-05)
