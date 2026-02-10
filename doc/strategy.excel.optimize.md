Việc xử lý file Excel để đưa vào hệ thống RAG/Knowledge Graph là một thách thức thú vị vì Excel nằm ở ranh giới giữa dữ liệu có cấu trúc (hàng/cột) và phi cấu trúc (text trong cell, merge cell).
Dựa trên bối cảnh bạn đang xây dựng hệ thống HyperMind (với Neo4j và Qdrant), đây là chiến lược "Hybrid Ingestion" tối ưu để tận dụng sức mạnh của cả hai loại cơ sở dữ liệu này.

## 1. Tư duy cốt lõi: "Structure in Graph, Meaning in Vector"
Để query hiệu quả, bạn không thể chỉ ném text vào vector store. Bạn cần chia chiến lược thành 2 luồng:
Neo4j (Graph): Lưu trữ cấu trúc phân cấp (File -> Sheet -> Row) và các thực thể định danh (Category, Department, Author) để thực hiện các query chính xác (hard filtering).
Qdrant (Vector): Lưu trữ ngữ nghĩa của nội dung trong hàng (row) hoặc đoạn văn bản (nếu cell chứa text dài) để tìm kiếm tương đồng (fuzzy search).

## 1.1. Ràng buộc thực tế cần ưu tiên
Excel thực tế của bạn có nhiều bảng trong một sheet và ưu tiên độ chính xác dữ liệu số/ngày.
Điều này bắt buộc pipeline phải:
- Phân tách được từng bảng (table detection) trước khi suy ra header và row.
- Chuẩn hóa kiểu dữ liệu cẩn thận để không làm mất precision hoặc sai ngày.
- Lưu cả raw value và formatted value để audit và debug.

## 2. Mô hình hóa dữ liệu (Data Modeling)
Bạn cần thiết kế schema sao cho Neo4j và Qdrant có thể "nói chuyện" với nhau qua một ID chung (thường là chunk_id hoặc row_id).

### A. Mô hình trong Neo4j (Graph Schema)
Thay vì lưu nguyên bảng Excel vào một node, hãy tách nhỏ:
File: Node đại diện cho file Excel.
Sheet: Node đại diện cho từng sheet.
Table: Node đại diện cho từng bảng trong sheet (vì 1 sheet có thể có nhiều bảng).
Row / Chunk: Node trung tâm chứa dữ liệu.
Entity: Các cột quan trọng (ví dụ: "Tên dự án", "Người phụ trách", "Loại lỗi") nên được tách thành các Node riêng để tạo liên kết giữa các file khác nhau.
Ví dụ Schema:
```
(File {name: "Báo cáo_2024.xlsx"})-[:CONTAINS]->(Sheet {name: "Q1"})
(Sheet)-[:HAS_TABLE]->(Table {id: "table_1", name: "Báo cáo lỗi", range: "B5:H42"})
(Table)-[:HAS_ROW]->(Row {id: "row_123", content: "..."})
(Row)-[:MENTIONS]->(Project {name: "HyperMind"})
(Row)-[:ASSIGNED_TO]->(Person {name: "Mr. A"})
```

### B. Dữ liệu trong Qdrant (Vector Payload)
Qdrant sẽ lưu embedding của hàng đó.
Vector: Embedding của chuỗi văn bản đã được "làm phẳng" (serialized) từ hàng.
Payload (Metadata): Phải chứa row_id, file_name, sheet_name và các thuộc tính quan trọng để filter (năm, phòng ban).
Nếu ưu tiên chính xác số/ngày, hãy thêm raw_values vào payload để có thể trả lời chính xác mà không cần LLM suy đoán.

## 3. Pipeline Ingestion (Quy trình xử lý)
Đây là quy trình từng bước để code pipeline (sử dụng Python + openpyxl + pandas).
Phân vai rõ ràng:
- openpyxl: đọc cấu trúc Excel gốc (merged, formula, number_format), cắt bảng, header detection.
- pandas: chỉ xử lý DataFrame sau khi đã cắt đúng bảng, để làm sạch và chuẩn hóa kiểu dữ liệu.

Vai trò của pandas (chỉ dùng sau khi đã cắt đúng vùng bảng):
- Chuẩn hóa dữ liệu bảng (DataFrame) sau khi table detection xong.
- Fill merge dọc (ffill) và làm sạch NaN theo cột.
- Chuẩn hóa kiểu dữ liệu (to_datetime, to_numeric) nhưng vẫn giữ raw value để audit.
- Serialization row thành text chuẩn cho embedding.

Pandas không nên dùng cho:
- Table detection (vì 1 sheet có nhiều bảng).
- Merged cells/formula (pandas đọc sẽ mất thông tin gốc).

### Mapping theo code skeleton (để đối chiếu)
Tham chiếu: `extractor/excel/excel_table_pipeline_skeleton.py`
1. Load workbook hai chế độ: `load_workbooks` dùng `data_only=True` để lấy giá trị và `data_only=False` để lấy formula/merged ranges.
2. Build cell map: `build_cell_map` gom `raw`, `formatted`, `data_type`, `is_date`, `number_format`, `formula`.
3. Fill merged cells: `extract_merged_ranges` + `apply_merged_cell_fill` để lan giá trị merge ra toàn vùng.
4. Table detection: `detect_tables` tách bảng theo block non-empty và gộp theo `max_row_gap`, `max_col_gap`.
5. Extract matrix: `extract_table_matrix` cắt bảng thành ma trận cell.
6. Header detection: `detect_header_rows` tìm các dòng header theo tỷ lệ text/numeric.
7. Header normalization: `normalize_headers` + `_build_header_matrix` xử lý multi-header và colspan.
8. Pandas cleaning (khuyến nghị sau bước 7): chuyển matrix -> DataFrame để `ffill`, xử lý `NaN`, `to_datetime`, `to_numeric` nhưng giữ raw.
9. Row iteration: `iter_data_rows` tạo `RowRecord`, gắn `cell_meta`, `serialized`, và phát hiện `is_footer`.
10. Row ID + hash: `compute_row_identity` và `_stable_hash`, ưu tiên key columns nếu có.

Tham số CLI đang hỗ trợ đối chiếu:
- `--min-rows`, `--min-cols`, `--max-row-gap`, `--max-col-gap`.
- `--max-header-rows`, `--min-text-ratio`, `--max-numeric-ratio`.
- `--key-columns`, `--skip-footer-rows`.

### Bước 0: Table Detection (openpyxl)
Tách sheet thành nhiều vùng bảng dựa trên:
- Vùng non-empty liên tục (connected blocks).
- Biên dạng đường viền (nếu có).
- Các dòng tiêu đề lặp lại.
Mỗi bảng cần một table_id ổn định để liên kết (sheet_name + index + range).

### Bước 1: Pre-processing & Standardization (openpyxl -> pandas)
Excel rất lộn xộn. Bạn cần chuẩn hóa trước:
Header Detection: Xác định dòng nào là header thật sự cho từng bảng.
Multi-header: Nếu header nằm nhiều dòng, hãy hợp nhất để ra một tên cột chuẩn.
Sau khi đã cắt đúng bảng bằng openpyxl, chuyển matrix -> DataFrame để làm sạch bằng pandas (ffill, NaN, type cast).
Serialization: Biến mỗi hàng thành một đoạn văn bản có ý nghĩa.
Input (Excel): | Project | Status | Description |
Output (Text): "Project: HyperMind. Status: Active. Description: Building knowledge base system."
Data Typing (ưu tiên chính xác):
- Lưu raw value (giá trị thực) và formatted value (hiển thị trên Excel).
- Chuẩn hóa ngày về ISO (YYYY-MM-DD) và số về dạng decimal string.
- Nếu cell là formula, dùng cached value để search nhưng lưu cả formula text.
Entity Extraction (Optional nhưng recommended): Dùng LLM hoặc Regex để tách các keyword từ các cột quan trọng (ví dụ: cột "Tags", "Category").

### Bước 2: Ingest vào Qdrant
Tạo embedding từ chuỗi text đã serialize (dùng model embedding bạn đang thích như BGE-M3 hoặc OpenAI).
Upsert vào Qdrant với ID là UUID (ví dụ: uuid_row_1).

Row ID Strategy (khi có và khi không có khóa ổn định):
- Nếu có khóa ổn định (ID dự án, mã lỗi): row_id = hash(key_columns + file_id + sheet_id + table_id).
- Nếu không có: row_id = hash(serialized_row + file_id + sheet_id + table_id) và lưu row_index để audit.

### Bước 3: Ingest vào Neo4j
Tạo Node Row với ID trùng với Qdrant (uuid_row_1).
Linking:
Tạo Node Entity từ các cột phân loại (Categorical Columns).
Tạo quan hệ (Row)-[:RELATED_TO]->(Entity).
Ví dụ: Nếu cột B là "Department", giá trị là "IT", hãy tạo Node (:Department {name: "IT"}) và nối vào Row. Điều này giúp bạn query: "Tìm tất cả dữ liệu liên quan đến IT" cực nhanh bằng Graph traversal.

### Bước 4: Incremental Update + Dedup (Vận hành ổn định)
Vì file Excel thay đổi thường xuyên, cần có cơ chế cập nhật vi sai:
- File Versioning: lưu file_hash (hash nội dung file) và updated_at.
- Table Versioning: table_id + header_hash + range để biết bảng nào đổi cấu trúc.
- Row Versioning: row_hash = hash(normalized_row + table_id + sheet_id + file_id).
Chiến lược cập nhật:
- Nếu file_hash không đổi: skip toàn bộ.
- Nếu file_hash đổi: so sánh row_hash mới với row_hash cũ để upsert/delete.
Dedup:
- Nếu có key ổn định, ưu tiên key để tránh gộp nhầm.
- Nếu không có key, dùng content_hash để phát hiện dòng trùng (giữ bản mới nhất).
- Trong Graph, có thể tạo quan hệ (Row)-[:DUP_OF]->(Row) để audit.

### 4. Chiến lược Retrieval (Truy xuất)
Khi user đặt câu hỏi, bạn sẽ dùng chiến lược Hybrid Search:

Bước 1 (Semantic Search - Qdrant):
Query câu hỏi của user vào Qdrant để lấy ra Top-K row_id có nội dung liên quan nhất.

Bước 2 (Graph Enrichment - Neo4j):
Cầm danh sách row_id đó sang Neo4j.
Tìm các node Row tương ứng và mở rộng (traverse) sang các node lân cận để lấy thêm ngữ cảnh (Context).
Ví dụ: Vector tìm thấy lỗi trong dự án A. Graph sẽ cho biết thêm: "Dự án A do ông B quản lý, và ông B cũng đang quản lý dự án C (có thể cũng bị lỗi này)."

Bước 3 (LLM Synthesis):
Đưa toàn bộ context (Text từ Qdrant + Quan hệ từ Neo4j) vào Prompt để trả lời.

### 5. Một số "Mẹo" kỹ thuật (Pro-tips)
Xử lý bảng phức tạp (Merged Cells): Dùng thư viện openpyxl hoặc pandas với option ffill (forward fill) để lấp đầy các ô bị merge trước khi xử lý.
GraphRAG cho Excel: Nếu file Excel chứa các mô tả quy trình, bạn có thể dùng LLM để trích xuất quan hệ nhân quả (Cause-Effect) và lưu vào Neo4j dưới dạng (Step A)-[:NEXT_STEP]->(Step B).
Tận dụng Metadata Filtering: Khi search Qdrant, hãy dùng metadata filter (lấy từ query analysis) để lọc bớt phạm vi (ví dụ: chỉ search trong file_name="Báo cáo tài chính"), sau đó mới vector search.
Accuracy-first tip: Khi trả lời câu hỏi cần số liệu, ưu tiên lấy raw_values từ payload thay vì trích từ text đã serialize.

## 6. Các bước cần thiết để code (Checklist triển khai)
1. Chuẩn bị môi trường:
- Cài `openpyxl`, `pandas`, `qdrant-client`, `neo4j`.
- Xác định schema Neo4j và payload Qdrant.
2. Ingestion Excel:
- Load workbook 2 chế độ (`data_only=True/False`).
- Build cell map và fill merged cells.
- Table detection + merge block theo gap.
- Cắt table matrix, detect header, normalize header (multi-header + colspan).
- Chuyển matrix -> DataFrame, chạy `ffill`, `to_datetime`, `to_numeric` nhưng giữ raw.
3. Tạo row artifacts:
- Serialize row -> text embedding.
- Tạo row_id và row_hash (ưu tiên key columns nếu có).
- Gắn cell_meta (formula, number_format, is_date) để audit.
4. Ingest Qdrant:
- Embed serialized text.
- Upsert với payload: `row_id`, `file/sheet/table`, `raw_values`, `formatted_values`.
5. Ingest Neo4j:
- Upsert `File -> Sheet -> Table -> Row`.
- Link `Row -> Entity` từ các cột categorical.
6. Incremental update + dedup:
- Lưu `file_hash`, `table_hash`, `row_hash`.
- So sánh hash để upsert/delete.
- Dedup theo key hoặc content_hash.
7. Retrieval layer:
- Query analysis -> filter metadata trước.
- Vector search -> row_id -> Graph enrichment.
8. Observability:
- Log số bảng, số row, tỷ lệ lỗi parse.
- Lưu báo cáo theo file/sheet để debug nhanh.
