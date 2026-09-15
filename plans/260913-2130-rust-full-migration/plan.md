---
title: "Rust full migration — analyzers, CLI/storage, MCP server (sau phase 01-07)"
status: code-complete (P01-P14 xong toàn bộ; còn gate vận hành: dogfood 1 tuần trước khi flip default — runbook docs/cutover-runbook.md; chi tiết trong từng phase-XX.md)
created: 2026-09-13
target: "rust/ (mở rộng), code-tiny/tools + code-tiny/mcp + doc-tiny + cortex_harness (tham chiếu parity, xoá dần sau cutover)"
blockedBy: []
blocks: []
relatedPlans:
  - "260913-1715-rust-retrieval-graph-port"
  - "260913-1538-ladybug-graph-provider"
  - "260914-1706-onnx-embedding-spike"
  - "260914-2259-dev-make-python-cutover"  # con của Scope B cutover: entrypoint dev/make + xoá pyexec bridge + port mcp-lifecycle
  - "260915-analyzer-layer-rust-cutover"  # đóng gate Wave-D chưa check (phase-08.md tail) + flip default + xoá analyzer scripts Python
predictionReport: "reports/prediction_report_20260913-1553.md"
---

# Rust full migration — chương trình hoàn thiện phần còn lại

## Overview

Kế hoạch chuyển toàn bộ phần còn lại của CortexHarness sang Rust sau khi phases 01–07
(retrieval brain, graph core, driver lbug, PyO3) đã hoàn thành với parity gate.
Tổng bề mặt Python còn lại ~**160k LOC**, chia 8 khối:

| Khối | LOC | Ghi chú |
|---|---|---|
| Analyzers + overlays (tools/*, trừ graph/common/sync) | **~84k** | 28 analyzers + 12 framework overlays; khối lớn nhất (Phase 05–08) |
| tools/common (còn lại) | ~10k | qdrant adapters, project registry/scope, graph_expander, result_packager |
| tools/sync + graph (còn lại) | ~18k | incremental_sync, writer, operations, manifest staging |
| mcp/ | **~22k** | unified_mcp 3.2k, fastmcp_server 2.9k, language sub-servers 8.6k, services 3k |
| cortex_harness + harness | ~12.5k | dev CLI 5.1k (21 commands), storage 5.4k, sync_processes, db_transfer |
| doc-tiny | ~6.3k | GraphRAG, GLiNER, neo4j loader |
| project_topology | ~3.3k | topology writer |

Chương trình đi theo **strangler-fig**: mỗi phase có parity gate riêng, Python giữ nguyên
làm default đến khi Rust pass gate; mỗi backend swap nằm sau env flag để rollback tức thì.

## Non-goals (không port)

- **C# Roslyn analyzer** — đã là process ngoài bằng C#; orchestrator Rust chỉ invoke subprocess.
- **Remote FalkorDB / Qdrant servers** — Rust là client, không phải host.
- **langextract / LLM API calls** — chỉ là HTTP; port sang reqwest khi làm doc-tiny.
- **GLiNER ONNX** — không có export chính thức; giữ Python NER sidecar (đã quyết ở addendum) đến khi có export khả thi.

## Phase map (4 waves / 14 phases)

| Phase | Wave | Scope | LOC Python tham chiếu |
|---|---|---|---|
| 01 | W0 | **FalkorDB Rust client spike** (redis-rs + GRAPH.ROQUERY) — go/no-go cho toàn bộ graph path | — |
| 02 | W0 | Journal hoàn tất: manifest staging, conservation, endpoint audit, claim_reconciling | ~2.5k |
| 03 | W0 | Graph writer + operations (language_writer, topology, operations/*) | ~7k |
| 04 | **D** | **Analyzer framework + python analyzer template** + parity harness thật trên stock | ~2.2k + framework |
| 05 | **D** | Analyzers batch 1 — dynamic/scripting: js, ts (backend+frontend), php, perl, shell | ~15.6k |
| 06 | **D** | Analyzers batch 2 — JVM: java, kotlin, android (java/kotlin/mixed) | ~11.9k |
| 07 | **D** | Analyzers batch 3 — systems & legacy: cplus (16.6k), rust, go, swift, delphi, cobol, vb*, jp1 | ~29.5k |
| 08 | **D** | Framework overlays: servlet_jsp, struts, mybatis, spring, aspnet×2, fastapi_django, express_js, laravel, web_framework, database_sql/plsql/schema | ~22.8k |
| 09 | **E** | incremental_sync orchestrator + embeddings pipeline (change detection, manifests, embedder) | ~5.1k + embedder |
| 10 | **E** | dev CLI (clap, 21 commands) + storage layer (gateway/lease/admission/generation) + db_transfer | ~12.5k |
| 11 | **F** | MCP server khung (rmcp): wire contract, tool catalog, registry/scope, capability matrix | ~5k |
| 12 | **F** | MCP graph tools (explore/search/subgraph/impact/workflows) | ~8k |
| 13 | **F** | MCP mind tools + semantic expansion (ONNX jina int8 qua ort; fallback sidecar) | ~7k |
| 14 | G+H | doc-tiny + cutover: migration, installers, CI, kết thúc dual-run | ~6.3k + ops |

## Key architectural decisions

1. **Tree-sitter native**: crate `tree-sitter` + grammar crates, pin version khớp output của
   `tree-sitter-languages` Python đang dùng; parse-quality machinery (report/repair) port theo.
2. **Analyzer contract không đổi**: CLI surface (`--root --project-id --changed-files-manifest
   --graph-provider --falkordb-graph ...`) giữ nguyên từng chữ — orchestrator và parity harness
   gọi được cả 2 backend bằng đúng lệnh.
3. **Embeddings tách khỏi analyzers**: analyzers Rust chỉ ghi graph + emit artifact; bước
   embedding do orchestrator điều phối (Python embedder worker đầu tiên, ONNX ort sau — spike
   riêng ở Phase 09; jina int8 đã có đường ONNX chính thức).
4. **FalkorDB client**: `redis-rs` + raw `GRAPH.ROQUERY`/`GRAPH.EXPLAIN` (Phase 01 quyết);
   không qua ORM. Ladybug local đi qua `lbug` (đã chứng minh).
5. **MCP wire contract byte-compatible**: golden contract tests cho **mọi** tool (capture
   response Python làm fixture) phải pass trước khi swap; `cortex.mcp.tool-result` v1.0
   không đổi version.
6. **Dual-run + rollback**: mỗi analyzer/tool/backend có env flag (`CORTEX_RUST_ANALYZER=python|rust`,
   `CORTEX_MCP_BACKEND=python|rust`); Python giữ default đến khi parity gate + 1 kỳ dogfood pass.
7. **ML tại query-time**: semantic expansion qua ONNX `ort` (jina int8) — nếu parity cosine
   < 0.999 hoặc hiệu năng kém thì fallback embedding sidecar Python (không chặn phase).
8. **Không port C# Roslyn** — subprocess invoke từ orchestrator Rust.

## Risks & gates

| Risk | Severity | Gate |
|---|---|---|
| FalkorDB Redis protocol từ Rust thiếu cái gì đó (query format, cached procedures) | Critical | Phase 01 spike go/no-go — nếu fail thì graph path giữ Python + PyO3 bridge |
| tree-sitter grammar version drift → parse trees khác Python | High | Per-analyzer: dump AST/symbols 2 bên so exact trên corpus thật (stock + procsample + testdata) |
| Concurrency semantics của storage gateway (bounded lanes, generation pinning) port sai | High | Stress test đa tiến trình + so journal state trước/sau (dùng chính journal Rust) |
| MCP contract lệch → vỡ clients | High | Golden contract tests byte-level cho mọi tool; wire diff CI gate |
| GLiNER không export ONNX được | Medium | Đã quyết fallback sidecar — không chặn |
| Volume analyzers (~84k) vượt dự kiến | Medium | Mỗi batch độc lập, ship riêng; ngôn ngữ nào chưa xong vẫn chạy Python |
| Remote infra (SSH tunnel) είναι test dependency | Low | Parity harness cho phép local falkordblite/ladybug backend |

## Verification strategy

- **Per-analyzer parity**: chạy analyzer Python và Rust trên cùng repo thật (stock làm primary,
  procsample + testdata phụ), dump graph (nodes+rels, cả properties) và so exact.
- **Scenario replay**: mở rộng harness `journal_scenario` cho sync orchestrator.
- **MCP golden contract**: record response Python cho bộ query cố định → fixture → Rust phải
  byte-match (trừ trường volatile có khai báo).
- **CI**: mọi PR chạy `cargo test` + clippy `-D warnings` + parity suites (pytest + shell scripts
  trong `scripts/rust_parity/`).
- **Dogfood**: mỗi wave chạy 1 kỳ sync thật trên stock trước khi tuyên bố xong phase.

## Active-plan coordination

- `260913-1715-rust-retrieval-graph-port` (implemented-phases-01-07): kế thừa toàn bộ crates
  `cortex-retrieval`, `cortex-graph-core`, `cortex-graph-driver`, `cortex-retrieval-py` và
  parity harness; plan này là phần tiếp nối chính thức.
- `260913-1538-ladybug-graph-provider`: implemented — ladybug là local provider mặc định;
  Phase 01 spike phải test cả falkordb remote VÀ ladybug local qua một trait `GraphStore`.
