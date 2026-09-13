# Prediction Report: FalkorDB → LadybugDB Migration

- **Date:** 2026-09-13
- **Depth:** deep
- **Proposal:** Thay thế FalkorDB (hiện tại: `falkordblite` embedded local + `falkordb` remote/Windows) bằng LadybugDB (https://github.com/LadybugDB/ladybug) làm graph backend của cortex-harness.
- **Verdict:** ⚠️ **CAUTION**

## Executive Summary

LadybugDB chính là KùzuDB đổi tên — graph DB *embedded* (in-process), Cypher, MIT, có wheel Windows native và FTS/vector index native, khớp tốt với seam kiến trúc sẵn có (`GraphProvider.KUZU` placeholder đã tồn tại trong `tools/graph/core/base.py:15`). Tuy nhiên đây không phải swap dependency: khác biệt nền tảng (multi-named-graph per file → one-graph-per-directory; schemaless → static schema; remote mode biến mất; bundle `.cortexdb` chứa `.rdb` thô vô nghĩa) tạo ra 4 rủi ro High và một vùng "ẩn" đáng kể ngoài file driver 909 dòng. Khuyến nghị làm theo phase với cờ `GRAPH_PROVIDER`, giữ FalkorDB làm rollback, và bắt buộc gate benchmark + ETL migration trước khi flip default.

## Agreements (≥4/5 persona)

1. Kiến trúc hiện tại **có seam đúng** để cắm Ladybug: `GraphDriver`/`CypherGraphDriver` + `GraphDriverFactory` + `StorageFactory.get_falkordb_driver()`; enum `GraphProvider.KUZU` placeholder chứng tỏ hướng này đã được dự liệu.
2. Concurrency **không bị phá vỡ**: model hiện tại đã là exclusive single-writer (`StorageLease` portalocker `LOCK_EX` + `BoundedLane(concurrency=1)`) — model embedded single-writer của Ladybug tương thích, chỉ cần bổ sung chế độ mở `read_only=True` cho reader/second process thay cho fan-out `additional_paths` qua socket redislite.
3. Phần lớn **237 call site** `execute_query` dùng Cypher khả chuyển (MATCH/WHERE/UNWIND/MERGE/SET + `$params`); các hàm rewrite sẵn có (`CALL (var) {}` → `CALL { WITH var }`, `datetime()` → parameter) là mô hình tốt cho một lớp normalize dùng chung.
4. Windows **cải thiện rõ**: hôm nay `falkordblite` chỉ chạy non-win32, win32 buộc dùng server FalkorDB remote; Ladybug phát hành wheel `win_amd64`/`win_arm64` (CPython 3.10–3.14) → bỏ được cấu hình kép trong `requirements.txt:2-3`.
5. Bắt buộc phải có **công cụ migration dữ liệu** (ETL logic `.rdb` → Ladybug) và cơ chế fail-closed khi mở store cũ bằng provider mới — không được để user thấy "graph trống" âm thầm.

## Conflicts

| Topic | Architect | Security | Performance | UX | Devil's Advocate | Resolution |
|---|---|---|---|---|---|---|
| Mức độ công việc | Trung bình, seam sẵn có | — | — | — | Driver chỉ là 40% dễ; schema/DDL, named-graph remap, bundle, fingerprint là 60% ẩn | **Chấp nhận thêm scope**: plan theo phase, ước lượng theo danh mục ẩn của DA, không estimate theo driver |
| Static schema vs schemaless | Cần auto-DDL/manifest governance | Thêm DDL → kiểm soát injection chặt hơn | MERGE upsert trong Kuzu historically chậm hơn plain CREATE | Lỗi "property không tồn tại" khó hiểu nếu không dịch thông báo | Giả định "write path tương thích" là giả; 162 site `SET n.*` phân tán | **Auto-DDL có gate config** + dịch lỗi thành message thân thiện; benchmark upsert trước khi flip default |
| Multi-graph model | Map graph-name → sub-directory | Tenancy fingerprint phải giữ credential-free | Mở nhiều DB = nhiều buffer pool | Doc graph / doctor graph phải trong suốt với user | Có thể đơn giản hóa: 1 DB + namespace table, nhưng thay đổi label namespace | **Graph-name → directory** (giữ ngữ nghĩa isolate hiện có); không gộp DB |
| Fork maturity (Kuzu archived → Ladybug fork) | Kiến trúc ổn, MIT | Pin version + Sigstore attestation | — | — | Bus-factor/release cadence chưa được chứng minh | **Giữ FalkorDB provider + rollback path** qua `GRAPH_PROVIDER` tối thiểu 1–2 release |
| Remote graph mode | `FALKORDB_URI` không có tương đương Ladybug (embedded-only) | Bỏ attack surface, nhưng đổi mô hình topology | — | User remote-mode hiện hữu bị strand | Có thể giữ FalkorDB chỉ cho remote | **Ladybug = local provider; FalkorDB = remote provider** (và legacy local cho rollback) — không phá user remote hiện hữu |

## Risk Summary

| Risk | Severity | Persona | Mitigation |
|---|---|---|---|
| Static schema: thuộc tính/label mới từ analyzer bị từ chối (ngày nay ghi schemaless tự do) | **High** | Architect + DA | Bootstrap `CREATE NODE TABLE IF NOT EXISTS` từ `CODE_GRAPH_SCHEMA`; auto-`ALTER TABLE ADD PROPERTY` gated bởi config + fail-loud logging; test parity với toàn bộ analyzer |
| Named-graph mismatch (`select_graph` multi-graph 1 file `.rdb` → 1 graph/DB directory) | **High** | Architect | Mapping graph-name → sub-directory trong layout.py; cập nhật `doc-tiny/graph_store.py`, `scripts/mcp-lifecycle.py` (graph "doctor"), `migration.py` inventory |
| Dữ liệu hiện hữu fail-closed sau khi đổi (fingerprint `provider=falkordb`, `TARGET_SCHEMA_VERSION`, bundle `.rdb` thô) | **High** | Architect + UX | Lệnh `cortex migrate` ETL `.rdb` → Ladybug; bump `TARGET_SCHEMA_VERSION` + marker migration; `db_transfer.py` thêm định dạng bundle ladybug (directory) + importer hiểu bundle cũ |
| Introspection/FTS dialect: `CALL db.labels()/db.relationshipTypes()/db.indexes()`, `db.idx.fulltext.queryNodes`, `create_node_*_index` | **High** | Architect + Perf | Một lớp `LadybugDriver` dịch toàn bộ (`show_tables()`, `show_indexes()`, `QUERY_FTS_INDEX`); chấp nhận ranking FTS khác → document; giữ fallback CONTAINS |
| Năng lực fork LadybugDB (Kuzu archive) | **High** | DA + Security | Pin `ladybug==x.y.z` (+hash), theo dõi release; giữ rollback FalkorDB ≥2 release; đọc license/attestation khi bump |
| Throughput MERGE/upsert chưa được đo trên graph thật | Medium | Performance | Benchmark gate trên graph mẫu ≥100k nodes trước khi flip default; nếu thua, batch `UNWIND + MERGE` hoặc tạo trước bằng COPY |
| Buffer pool/memory chưa tune | Medium | Performance | Env `LADYBUG_BUFFER_POOL_SIZE` mặc định hợp lý, expose vào storage config |
| Injection qua chuỗi label/query trong driver mới (f-string `_cypher_string` hiện tại) | Medium | Security | Parameterize tối đa; escape/whitelist label cho FTS DDL; tái dùng bộ security test hiện có |
| Quyền truy cập dữ liệu at-rest (directory DB thay vì file + unix socket) | Low | Security | Set permission directory 0700 khi tạo; lease lock giữ nguyên |
| Env var surface đổi (`FALKORDB_PATH/URI/…`) | Medium | UX | Alias deprecated + warning (đúng precedent `DeprecationWarning` sẵn có trong driver), doctor probe cảnh báo khi thấy `.rdb` cũ |
| Bundle cũ (`.cortexdb` v1 chứa `.rdb`) không import được | Medium | UX + Architect | Importer hỗ trợ cả hai schema bundle, ETL on-import |

## Per-Persona Detail

### Architect — confidence: high
- **Concerns:** (1) Driver mới ~900 dòng parity (`execute_query_sync`, `create_indexes`, `inspect_indexes`, `list_databases`, `list_relationship_types`, `find_node_by_id(s)`, `search_functions`, `search_by_code`); (2) named-graph → directory mapping chạm layout/migration/doc-graph/doctor; (3) static schema + rel tables cần khai báo FROM/TO tường minh — rủi ro nhất với rel động giữa label tùy ý; (4) `provider_contract.py` fail-closed chỉ cho phép falkordb/neo4j → thêm ladybug; (5) remote mode không tồn tại ở Ladybug.
- **Recommendations:** implement `LadybugDriver` sau `CypherGraphDriver`; schema bootstrap từ manifest; phase rollout với `GRAPH_PROVIDER=ladybug`; ETL migration command; bump `TARGET_SCHEMA_VERSION`.

### Security — severity: medium (no critical)
- **Threats:** supply chain PyPI (mitigated: trusted publishing + Sigstore, vẫn pin+hash); Cypher injection qua string-built FTS/DDL; data-at-rest permission; không được lỏng fail-closed validation khi thêm provider alias.
- **Mitigations:** pin+hash dependency; parameterize + label whitelist; chmod 0700 data dir; giữ `normalize_graph_provider_name` reject unknown; audit security test cho driver mới.

### Performance — metrics_impact: read latency kỳ vọng flat→tốt hơn; ingestion upsert chưa đo; startup +DDL bootstrap
- **Bottlenecks:** MERGE upsert tại scale; buffer pool sizing; DDL bootstrap mỗi lần mở DB; FTS scoring khác → độ chính xác ranking thay đổi.
- **Alternatives:** giữ falkordblite; dual-provider A/B benchmark trước cutover; nếu chỉ nhắm Windows → cân nhắc nhánh nhỏ hơn.

### UX — issues & edge cases
- **Issues:** env var surface đổi; "graph trống" âm thầm nếu thiếu migration; thông báo lỗi schema khó hiểu; kết quả search khác ranking.
- **Edge cases:** mixed-mode remote-graph + local-qdrant; bundle cũ import vào bản mới; `FALKORDB_URI` set nhưng default là ladybug; win32 user (thắng lợi: bỏ server remote).
- **a11y:** N/A (CLI) — chỉ cần error text rõ ràng, nhất quán tên provider.

### Devil's Advocate
- **Assumptions challenged:** (1) "Cần đổi ngay" — falkordblite 0.10.0 đang chạy; động cơ thật phải được nêu (Windows? maintenance? hiệu năng?) và đo trước; (2) "Ladybug production-ready" — fork trẻ của một project đã archive; (3) "Port thuần kỹ thuật" — 60% khối lượng nằm ngoài driver (schema, graph model, bundle, fingerprint, migration, test); (4) đơn giản hơn: chỉ bật Ladybug cho Windows trước, hoặc dual-provider benchmark rồi mới quyết.
- **Simpler alternatives:** dual-provider sau `GRAPH_PROVIDER` (khuyến nghị chấp nhận như phase 1 của chính plan migration); Neo4j desktop cho ai cần server.
- **Worst case:** schema drift khiến analyzer ghi graph thiếu cột âm thầm → MCP trả kết quả thiếu, parity test ở downstream thất bại muộn; kèm bundle user cũ không đọc được và user remote-FalkorDB bị strand — toàn bộ mitigable nhưng chỉ nếu plan có gate.

## Recommendations (numbered, with rationale)

1. **Chạy dual-provider phase trước (không flip default):** thêm `LadybugDriver` + `GRAPH_PROVIDER=ladybug` opt-in. Rationale: rollback miễn phí, đo lường thật thay vì giả định, khớp precedent neo4j optional.
2. **Thiết kế graph-name → sub-directory mapping trong `storage/layout.py` ngay từ đầu.** Rationale: chạm fingerprint/journal compatibility — sửa muộn sẽ đắt gấp nhiều lần.
3. **Schema bootstrap + auto-DDL gated:** `CREATE NODE TABLE IF NOT EXISTS` từ `CODE_GRAPH_SCHEMA`, `ALTER TABLE ADD PROPERTY` có cờ config + log loud. Rationale: giả lập schemaless một cách kiểm soát được, chặn worst-case schema drift.
4. **Viết ETL `.rdb` → Ladybug + lệnh `cortex migrate`, bump `TARGET_SCHEMA_VERSION`, mở rộng `db_transfer.py` cho bundle mới và đọc bundle cũ.** Rationale: dữ liệu user là hard gate, fail-closed hiện có sẽ chặn mọi run cũ ngay khi đổi provider.
5. **Benchmark gate:** upsert/MERGE throughput + read latency trên graph ≥100k nodes, so sánh falkordblite vs ladybug, trước khi quyết flip default. Rationale: điểm bất định duy nhất về hiệu năng, rẻ để đo, đắt để sai.
6. **Dịch toàn bộ introspection/FTS surface trong một lớp duy nhất** (`show_tables`, `show_indexes`, `QUERY_FTS_INDEX`, index API) — các MCP server (`java/cplus/android`) và `dead_code_report` không được gọi dialect trực tiếp. Rationale: hiện `CALL db.relationshipTypes()` đã lộ ra 4 file ngoài driver — phải gom về driver để không tái diễn.
7. **Pin `ladybug==x.y.z` + hash, ghi rollback path vào ReadMe, giữ FalkorDB ≥2 release.** Rationale: fork trẻ; MIT + cùng engine giúp fork-c回来的 rủi ro thấp nhưng không bằng không.
8. **Deprecation alias cho env vars + doctor probe phát hiện `.rdb` cũ và hướng dẫn migrate.** Rationale: đúng precedent `DeprecationWarning` hiện có trong `FalkorDBDriver`.

## Next Steps (verdict: CAUTION)

1. Trả lời câu hỏi động cơ (DA): Windows-first? maintenance? hiệu năng? — quyết định xem phase Windows-first hay full cutover.
2. Vào **hi-plan** với phase: (a) `LadybugDriver` + provider plumbing (contract/targets/factory/layout); (b) schema bootstrap + auto-DDL; (c) ETL migrate + bundle v2; (d) benchmark gate; (e) flip default + deprecation window.
3. Trước khi code: benchmark spike (rec #5) và prototype spike static-schema trên 2 analyzer phức tạp nhất (servlet_jsp state, spring) để xác nhận auto-DDL đủ.

## Decision Update (2026-09-13, sau khi user xác nhận động cơ)

**Động cơ chính: Windows support.** Điều này re-scope toàn bộ plan theo hướng "Windows-first" mà Devil's Advocate đã dự báo:

**Fact thay đổi cục diện:** trên win32, local embedded mode hiện *không tồn tại* (`storage/config.py:478` — "FalkorDBLite (unavailable on win32)"; `tools/graph/cli.py:222`; `requirements.txt` chọn `falkordb` client remote cho win32). Windows user hôm nay buộc phải chạy server FalkorDB (Docker/WSL) và trỏ `FALKORDB_URI`.

Hệ quả trực tiếp lên risk table:

| Risk cũ | Trạng thái sau re-scope |
|---|---|
| ETL `.rdb` → Ladybug + `cortex migrate` | **De-scope phase 1** — không có dữ liệu local Windows nào để migrate |
| Bundle `.cortexdb` chứa `.rdb` thô | **De-scope phase 1** — importer cũ vẫn dùng cho macOS/Linux; bundle ladybug thêm khi full cutover |
| Fingerprint `provider=falkordb` fail-closed với store cũ | **Biến mất** — Windows local store là *mới*, không có store cũ xung đột; macOS/Linux giữ nguyên falkordblite |
| Static schema / auto-DDL | **Vẫn trong scope** (High, mitigated) |
| Named-graph → directory mapping | **Vẫn trong scope** (High, mitigated) |
| Fork maturity | Downgrade Medium — rollback của Windows user chính là remote falkordb, tức *trạng thái hiện tại* của họ |
| Benchmark gate | Downgrade smoke-check — baseline của Windows user là "remote server hoặc không có gì", embedded native chắc chắn tốt hơn |

**Verdict hai track:**
- **Windows-first phase → GO** (0 Critical, mitigations rõ): `LadybugDriver` + provider plumbing, chỉ local-mode trên win32 mặc định sang ladybug; macOS/Linux không đổi gì.
- **Full cutover (macOS/Linux bỏ falkordblite) → giữ CAUTION** — chỉ mở lại sau khi Windows-first ổn định ≥1 release và auto-DDL đã qua parity test toàn bộ analyzer.

**Scope phase 1 (Windows-first):**
1. `LadybugDriver` implement `CypherGraphDriver` (execute/query/index/introspection/FTS).
2. `provider_contract.py`: thêm alias `ladybug`/`lbug`; `targets.py`: provider `ladybug`, default scheme vẫn path-based (mode=file).
3. `config.py`/`resolve_storage`: trên win32, local backend resolve → `ladybug` path thay vì yêu cầu `FALKORDB_URI`; `requirements.txt` bỏ điều kiện `sys_platform` cho ladybug (multi-platform wheel).
4. Graph-name → sub-directory mapping trong layout (hyper_graph + doc + doctor).
5. Schema bootstrap + auto-DDL gated từ `CODE_GRAPH_SCHEMA`.
6. Smoke benchmark + parity test trên Windows runner (CI nếu có).

Bước tiếp theo: **hi-plan** cho scope 6 mục trên.

---
*Phân tích dựa trên đọc trực tiếp source (`cortex_harness/storage/*`, `code-tiny/tools/graph/*`, `doc-tiny/graph_store.py`, `db_transfer.py`, requirements) + tài liệu LadybugDB/Kuzu công khai. Không dùng mind/graph MCP trong lần chạy này — repo đọc trực tiếp nên độ tin cậy code-derived findings: high.*
