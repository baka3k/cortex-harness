# Phase 06 — Verification Toàn Diện + Benchmark + Docs + Red-team Disposition

> Gate ra: M4 benchmark chính thức; M7 (suite không regress); red-team findings xử lý/xếp hạng; prediction-report follow-up.

## Tasks

### 6.1 Benchmark chính thức (M4)

- `benchmark_vb6_parse_quality.py` chạy: engine=antlr vs engine=regex trên (a) fixture corpus, (b) corpus mở rộng nếu có repo VB6 thật (≥200 files lý tưởng — hỏi owner; nếu không có, ghi giới hạn vào report).
- Thu: p50/p95 latency per file, files/s, JVM startup amortization (batch 1 vs N files), worker RSS.
- So baseline phase-01: delta call-capture (missed → captured), resolution rate.
- Kết quả vào `benchmark-report.md` trong plan dir.

### 6.2 Test suite toàn diện (M7)

- Chạy full `tests/` (ít nhất vb-related + common + registry + mcp acceptance liên quan parser map: `test_common_analyzer_registry.py`, `test_mcp_acceptance_matrix.py`).
- Golden tests engine=antlr + engine=regex (fallback path cũng phải đạt mức regex chấp nhận được — không tệ hơn baseline).
- Incremental: đổi 1 file fixture → sync incremental → chỉ file đó re-write, cạnh cross-module vẫn đúng (chứng minh AD-02) **+ cạnh INCOMING từ file không đổi vào file đổi được dựng lại (AD-09/F1 — assert hướng ngược)**, và số placeholder external_symbol không tăng sau 2 sync liên tiếp (F8 reconciliation).

### 6.3 Graph verification (M5 mở rộng)

- Ingest fixture vào graph dev → Cypher asserts: CALLS modMain.DoWork→modUtil.CalcTotal tồn tại; POSSIBLE_CALLS có resolution_status late_bound/ambiguous/external đúng case (`resolution_class=lexical_candidate`, không giá trị tự chế — F2); CONTAINS class→method; IMPLEMENTS clsShip→IShip (IShip emit như Interface node — AD-10).
- `graph_mcp.find_callers` + `trace_flow` smoke qua MCP tools.

### 6.4 Red-team disposition

- Chạy reviewer (role=reviewer, lens assumptions+failure-modes) trên plan + diff lớn nhất.
- Mọi finding Critical/High: fix hoặc ghi disposition vào `red-team.md` (đã làm gì / chấp nhận rủi ro gì vì sao).

### 6.5 Docs chốt

- Cập nhật `docs/PARSER_TYPE_QUERY_RULES.md` nếu vb6 parser_type semantics đổi (engine không đổi parser_type — verify).
- Prediction report `260917-1139`: append kết cục (metrics thực tế so gate M1-M7) — đóng vòng theo dõi.
- `hi-log` entry cuối (theo convention repo nếu có docs/logs).

## Định nghĩa xong

- [ ] benchmark-report.md có số M4 chính thức (hoặc ghi rõ giới hạn corpus)
- [ ] Full suite xanh; incremental AD-02 chứng minh bằng test
- [ ] Graph asserts + MCP smoke xong
- [ ] red-team.md có disposition từng finding
- [ ] Prediction report closed-loop + log entry
