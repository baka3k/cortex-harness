# HI-PREDICT: Chuyển CortexHarness từ Python sang Rust

- **Date:** 2026-09-13
- **Depth:** quick (default — proposal không chỉ định depth)
- **Proposal:** "tôi cần đổi python sang rust" (đề xuất chưa xác định phạm vi)
- **Verdict: STOP** — chuyển đổi toàn bộ như đề xuất hiện không khả thi với chi phí hợp lý; cần thu hẹp phạm vi / chọn lại kiến trúc đích trước khi chạy lại prediction.

---

## Executive Summary

CortexHarness có ~184k LOC Python trải trên 3 khối: `code-tiny` (165k — MCP servers graph_mcp/mind_mcp, analyzers, project registry), `cortex_harness` + `harness` (12.5k — CLI `dev`, storage gateway, lease/generation, MCP contract), `doc-tiny` (6.3k — GraphRAG ingest/query, NER). Việc chuyển toàn bộ sang Rust vấp 2 rào cản cứng: (1) stack ML (torch, transformers, sentence-transformers, GLiNER, langextract) là Python-native, không có tương đương Rust hoàn chỉnh; (2) mode "embedded, no-daemon" của dự án dựa trên qdrant-client local mode và falkordblite — cả hai đều không tồn tại trong Rust, thay engine đồng nghĩa đổi format on-disk của mọi instance hiện có. Verdict là **STOP cho đề xuất "convert toàn bộ"**, kèm các hướng đi thay thế đã được phân tích.

> **Note (MCP fallback):** graph/mind MCP hiện chỉ index project khác (`procsample`), không có context cho repo này. Theo fallback của skill, code context được dựng bằng đọc code trực tiếp; các phát hiện phụ thuộc index được đánh dấu confidence thấp hơn.

---

## Agreements (4+/5 persona đồng thuận)

1. **Không có lý do đã được đo đạc để rewrite** — chưa có profiling nào cho thấy Python là bottleneck; các đường nóng chính là I/O ngoài (Qdrant search, graph query, embedding HTTP) chứ không phải CPU Python. (Devil's Advocate, Architect, Performance, UX)
2. **MCP wire contract là biên phải bảo toàn tuyệt đối** — `cortex.mcp.tool-result` v1.0 phải giữ nguyên byte-for-byte nếu có bất kỳ thành phần nào được viết lại, vì MCP clients (graph_mcp/mind_mcp) không được phép nhận行为 khác. (Architect, Security, UX)
3. **Porting cục bộ từng component + parity test** (strangler-fig) là cách tiếp cận đúng nếu có tiến hành — không phải big-bang rewrite. (Architect, Performance, Devil's Advocate)
4. **Rust mang lại lợi ích thật cho 1 số phần hẹp:** CLI `dev` (startup time, phân phối single-binary), parser/analyzer CPU-bound trong `code-tiny` (batch ingestion) — đây là ứng viên chuyển đổi hợp lý nhất. (Architect, Performance)

---

## Conflicts

| Topic | Architect | Security | Performance | UX | Devil's Advocate | Resolution |
|---|---|---|---|---|---|---|
| Toàn bộ 184k LOC có nên chuyển? | Không — hư cấu trúc embedded/no-daemon | Neutral, ưu tiên memory-safety | Lợi ích chưa chứng minh | Rủi ro hồi quy workflow lớn | Không — động cơ chưa validate | **STOP toàn bộ; chỉ xem xét hẹp từng phần** |
| Stack ML (GLiNER/langextract/torch) | Blocker — không có Rust tương đương | — | Lợi ích ~0 (đã là native dưới Python) | Kết quả NER có thể lệch nếu đổi engine | Blocker xác nhận | **Giữ Python cho ML; chỉ gọi qua biên** |
| Embedded storage (qdrant local + falkordblite) | Đổi engine = đổi on-disk format | Dữ liệu at-rest cần migration an toàn | Engine Rust có thể nhanh hơn nhưng phải chạy daemon/sidecar | Break "no daemon" promise trong ReadMe | Break promise = mất USP | **Chỉ đổi engine nếu chấp nhận thiết kế lại + migration; mặc định giữ** |
| CLI `dev` + storage gateway (12.5k) sang Rust trước | Khả thi, low-risk tương đối | Cần giữ lock semantics (portalocker → fd-lock) | Startup +x nhanh, không đổi big-O | Phải giữ 100% CLI surface & Makefile | OK nhưng lợi ích nhỏ so với chi phí | **Ứng viên chuyển đổi hợp lý nhất nếu được duyệt** |
| Ryu/PyO3 hybrid vs full rewrite | Hybrid giữ contract, giảm rủi ro | 2 runtime = 2 attack surface cần quản lý | FFI overhead không đáng kể với batch lớn | Không đổi UX | Nhanh hơn nhiều để có giá trị đầu tiên | **Hybrid/strangler là path được đề xuất** |

---

## Risk Summary

| Risk | Severity | Persona | Mitigation |
|---|---|---|---|
| Không có engine vector/graph embedded tương đương trong Rust → hoặc mất "no-daemon" promise hoặc redesign storage | **Critical** | Architect | Không chuyển storage layer trừ khi redesign được duyệt; nếu redesign: chọn LanceDB/usearch + graph engine mới, viết migration cho `~/.cortex-harness/v1`, version hoá layout |
| Stack ML (GLiNER, langextract, sentence-transformers) không port được → mất NER/GraphRAG parity | **Critical** | Architect | Giữ Python cho pipeline ML; tách thành service/process riêng, Rust gọi qua IPC/MCP |
| 184k LOC rewrite → vài quý effort, parity risk cao, đóng băng feature (đang có C# analyzer, project-id scope mới merge) | **High** | Devil's Advocate | Thu hẹp phạm vi; port theo component kèm parity-gate (repo đã có sẵn infra porting/parity skills) |
| MCP wire contract lệch (error codes, alias, `_INTERNAL_ERROR_DETAIL_KEYS`) làm hỏng clients | High | Architect/Security | Golden contract tests (đã có `test_provider_boundary.py`) phải pass trước khi swap bất kỳ backend nào |
| Semantics lock/lease (portalocker, generation-pinned gateway, BoundedLane) port sai → race/corruption dữ liệu instance | High | Security/Architect | Port `lease.py`/`admission.py` cuối cùng, kèm stress test đa tiến trình; cân nhắc giữ Python gateway |
| Dữ liệu instance hiện tại (~/.cortex-harness/v1) không đọc được sau khi đổi | High | Security | Migration tool + backup tự động trước upgrade; version field trong manifest |
| Hiệu năng không cải thiện đáng kể (I/O-bound) → chi phí không hoàn vốn | Medium | Performance/DA | Bắt buộc profiling + đặt SLO đo được trước khi bắt đầu bất kỳ port nào |
| Windows support (falkordb remote trên win32, PowerShell installer) lệch sau chuyển | Medium | UX | Ma trận smoke test Win/macOS/Linux cho mọi PR port |
| Secrets trong `.env` (dotenv) xử lý khác trong Rust binary | Low | Security | Dùng dotenv crate + review không log secrets; threat-model lại binary distribution |

---

## Per-Persona Detail

### Architect — confidence: high
**Concerns:**
- Đặc sản "embedded, file-backed, no daemon" của CortexHarness dựa trên 2 thư viện Python-only: `qdrant-client` local mode (pure-Python simulation) và `falkordblite`. Trong hệ sinh thái Rust không có tương đương — Rust SDK của Qdrant chỉ nói chuyện với server.
- MCP servers (`code-tiny/tools`, `mcp/`) + contract `cortex.mcp.tool-result` v1.0 là biên công khai; rewrite phải bảo toàn 100% (error-code aliases, canonical shapes).
- Storage layer hiện có Protocol-based abstraction (`QdrantStore` Protocol, `StorageFactory`) — đây là chỗ cắm được backend Rust nếu sau này muốn, không cần rewrite toàn bộ.

**Recommendations:** (1) Không rewrite storage/ML; (2) nếu port, chọn strangler-fig theo component; (3) biên đầu tiên nên là CLI `dev` hoặc analyzer CPU-bound.

### Security — severity: medium
**Threats:** dữ liệu instance at-rest trong `~/.cortex-harness/v1` phải migrate; lock semantics (portalocker) port sai dẫn đến lease bypass giữa tiến trình; binary Rust phân phối qua installer mới mở surface supply-chain; 2 runtime song song cần 2 pipeline bảo mật.
**Mitigations:** migration tool + backup; dùng `fd-lock`/`fs2` kèm stress test; ký binary + lockfile `cargo-deny`; xác định rõ owner của từng path trong giai đoạn hybrid.

### Performance — metrics_impact: chưa đo được (chưa có baseline)
**Bottlenecks (giả thuyết cần verify):** hotspot thật nằm ở embedding HTTP call, vector search, graph query, và parse CPU-bound trong analyzer khi ingest batch lớn — không phải overhead Python của orchestration.
**Alternatives:** (1) PyO3 extension cho analyzer/parse loop (không đổi kiến trúc); (2) multiprocess/worker pool trong Python; (3) đo trước bằng cProfile/py-spy trước khi quyết. Rewrite Rust cho I/O-bound orchestration cho lợi ích ~0.

### UX — issues & edge cases
- CLI surface (`dev`, make targets, `make doctor`, installers Windows PowerShell/bat) phải giữ hành vi y nguyên trong suốt quá trình chuyển; dual-language cài đặt (uv + cargo) làm phức tạp onboarding mà ReadMe hiện quảng bá "clone + install dev command".
- Edge cases: error messages/exit codes thay đổi làm hỏng script người dùng; Windows không có falkordblite — logic backend-selection phải port chính xác.
- A11y không áp dụng (CLI), thay bằng: output contract của CLI phải snapshot-tested.

### Devil's Advocate
**Assumptions challenged:**
1. *"Python chậm nên phải đổi Rust"* — chưa có profiling; hệ thống I/O-bound, xác suất cao giả định sai.
2. *"Đổi toàn bộ"* — đề xuất 29 ký tự không xác định phạm vi; khả năng cao ý định thật là 1 phần (CLI? analyzer? MCP server?) — scope phải được làm rõ.
3. *"Rust giảm chi phí vận hành"* — thực tế thêm 1 toolchain, 1 CI matrix, 1 bộ installer, và trong hybrid là 2 runtime phải cùng sống.

**Simpler alternatives:** PyO3 hot-path extension; đổi từng analyzer sang Rust binary gọi qua subprocess; giữ everything else.
**Worst case:** 2–3 quý rewrite, data instances cũ hỏng, MCP clients vỡ contract, ML pipeline mất parity, feature development đóng băng — mà latency không đổi đáng kể.

---

## Recommendations (numbered)

1. **[STOP action] Làm rõ phạm vi + động cơ** trước khi any port: đo profile thực tế (py-spy/cProfile trên ingest + query paths) và viết ra con số cần cải thiện. *Rationale: mọi persona đều thấy lợi ích chưa được chứng minh.*
2. **Nếu mục tiêu là hiệu năng ingestion/analyzer:** viết Rust extension (PyO3) hoặc binary subprocess cho parser hot-loop trong `code-tiny/tools` — giữ Python orchestrator và MCP contract nguyên vẹn. *Rationale: giá trị cao nhất, rủi ro thấp nhất, không đụng storage/ML.*
3. **Nếu mục tiêu là distribution/CLI:** port `cortex_harness` CLI + `storage/config|layout|targets` (~3–4k LOC) sang Rust `clap`, giữ gateway/lease bằng cách gọi Python sidecar hoặc port sau. *Rationale: ranh giới sạch, test được bằng golden CLI snapshot.*
4. **Không port:** ML pipeline (GLiNER/langextract/transformers) và embedded storage layer. *Rationale: không có tương đương Rust; làm mất promise "no daemon" và parity NER.*
5. **Nếu vẫn muốn full-Rust endgame:** chạy lại pre-port với đề xuất mới gồm (a) engine vector/graph đích, (b) thiết kế migration cho `~/.cortex-harness/v1`, (c) chiến lược giữ MCP contract, (d) SLO đo được. *Rationale: đó là một dự án redesign, không phải một lần port.*
6. **Bất kỳ port nào cũng phải đi qua parity gate** (repo đã có hi-porting-* / port-parity-gate skills + contract tests) — không merge backend swap khi contract tests đỏ.

## Next Steps (STOP → redesign)

1. Chạy profiling 1 phiên ingest + 1 phiên query; ghi baseline latency/throughput.
2. Quyết định mục tiêu thật: hiệu năng vs distribution vs "muốn Rust".
3. Viết proposal hẹp (1 component, 1 metric, 1 deadline) → chạy lại `/hi-predict` với depth `deep` → nếu đạt CAUTION/GO thì sang `/hi-plan`.

---

*Báo cáo tạo bởi hi-predict (depth: quick). MCP graph không có index cho repo này — code context từ đọc trực tiếp, confidence các nhận định về runtime behavior ở mức medium.*
