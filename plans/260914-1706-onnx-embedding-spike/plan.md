---
title: "ONNX embedding spike — Rust ort native (jina-v3 int8, bge-m3 dense) + GLiNER ONNX evaluation"
status: partially-done
created: 2026-09-14
target: "rust/crates/cortex-embed (mới), cortex-mcp (mind/graph), cortex-doc, cortex-sync (seam), scripts/rust_mcp, scripts/rust_parity — code-tiny/doc-tiny giữ làm tham chiếu parity"
blockedBy: []
blocks:
  - "260915-analyzer-layer-rust-cutover"  # phase-06 vector plane; NO-GO (reports/phase05-sync-decision.md) đã được overturn 2026-09-15 bằng 8 component gates → plans/260915-analyzer-layer-rust-cutover/reports/phase06-vector-component-gates.md
relatedPlans:
  - "260913-2130-rust-full-migration"
  - "260829-2322-vector-search-query-optimization"
  - "260913-1715-rust-retrieval-graph-port"
  - "260915-analyzer-layer-rust-cutover"  # phase-06 overturn NO-GO (reports/phase05-sync-decision.md) bằng component gates; kết quả gates + wall-time feed vào re-baseline golden + latency ở plan này
---

> **Phase-06 result (2026-09-15, cutover side)** — sync-time ONNX embedding pass
> cho **shared-7 lineage** (`SHARED_VECTOR_CLI_PARSERS`) đạt 8/8 component gates
> (`plans/260915-analyzer-layer-rust-cutover/reports/phase06-vector-component-gates.md`):
> point-id uuid5, redaction 3-tầng, `_hash_vector`, delete-by-filter/vectors_config,
> provenance pin fail-closed, artifact 0600 — byte/value-identical vs Python; live
> sync cosine worst 0.999999999992 / 0 under-gate; wall-time native **1.95×** Python
> (không regression). **Carve-out tường minh**: legacy `CodeEmbedder` lineage + local
> embedded-store lane vẫn trên Python child (fail-closed loud, không im lặng).
> **Open items của spike không đổi ở đây**: (1) MCP golden re-baseline (decision #7,
> chờ hết dogfood) — số G5/G6 phase-06 sẵn sàng feed vào; (2) int8 jina/bge vẫn chưa
> đo (chỉ fp32 được pin + gate); (3) runbook:47 "+25–31 ms/tool-call chưa giải thích"
> đã được root-cause ở phase-02b không phải ORT — cập nhật runbook khi flip
> `CORTEX_EMBED_BACKEND`.

# ONNX embedding spike — bỏ Python sidecar khỏi đường embedding

## Corrections (2026-09-14, measured) — đọc trước khi implement

4 premise dưới đây đã bị **bác bằng số liệu** trước khi viết dòng code đầu tiên; chi tiết +
bằng chứng file:line ở [`findings.md`](findings.md). Khi mâu thuẫn, `findings.md` thắng.

1. Code plane **không có 2 đường số học** — `mean-pool 512 không normalize` là nhánh chết
   với jina-v3 (nó có `.encode()`). Đo: cosine giữa 2 lane = **1.000000**, đều unit-norm.
2. bge-m3 là **CLS-pool + CÓ normalize** (`modules.json` có `2_Normalize`), không phải
   "encode raw không normalize" → implement theo bản cũ sẽ sai vector toàn lane mind/doc.
3. jina-v3 **không có ONNX chính thức dùng được**: artifact HF đòi `task_id` scalar
   (0..4, mượn `token_type_embeddings` làm task-embedding) ⇒ không biểu diễn được
   "adapter tắt" mà production đang chạy (cos tốt nhất 0.9418). P01 phải **tự export**.
   bge-m3 thì artifact chính thức **sạch**, dùng thẳng. Không chỗ nào có int8.
4. Tokenizer jina-v3 là `XLMRobertaTokenizerFast` (không phải Mistral) ⇒ quirk
   `fix_mistral_regex` là no-op. **Gate #0 đã PASS**: `tokenizers` 0.23.2 tái tạo 0/20
   token-id sai khác với `AutoTokenizer`.

## Overview

Đây là **Phương án A** mà phase-09 của `260913-2130-rust-full-migration` cố tình defer
("A là spike song song có deadline"): port model inference embedding sang Rust bằng
`ort` (ONNX Runtime) + crate `tokenizers`, thay các Python sidecar hiện tại, gate parity
**cosine ≥ 0.999** so với Python.

Hiện trạng 2 model plane (kết quả research 2026-09-14, bằng chứng file:line ở bảng dưới):

| Plane | Model | Dùng ở | Status Rust |
|---|---|---|---|
| **Code** | `jinaai/jina-embeddings-v3` (1024-dim, trust_remote_code) | ingest sync + query `semantic_search`/`explore_graph` | Rust **không embed**; MCP Rust trả vector lane RỖNG có chủ đích |
| **Doc/mind** | `BAAI/bge-m3` (dense head 1024; sparse/colbert KHÔNG dùng) | doc ingest, mind/doc query, livingdoc | Rust gọi Python sidecar (`embed_worker.py` persistent, `cortex-doc` one-shot) |
| **NER** | `urchade/gliner_large-v2.1` (deberta-v3-large backbone) | doc ingest entity extraction (không query-time) | Rust gọi Python sidecar (`GlinerProvider`, `gliner_sidecar.py`) |

Phát hiện then chốt của research: **premise "GLiNER không có ONNX export chính thức"
đã lỗi thời** — gliner **0.2.28** trong `.venv` có `GLiNER.export_to_onnx(..., quantize=)`
+ `from_pretrained(load_onnx_model=True)` + 6 kiến trúc ORT inference (`gliner/onnx/model.py`).
Cảnh báo của chính gliner: model deberta-based **mất accuracy với int8** → gate phải chạy
fp32 trước. Quyết định "GLiNER stays Python sidecar" (phase-13/14) cần đánh giá lại —
phase 03 của plan này làm đúng việc đó với bằng chứng số, không assume.

## Non-goals (không port trong spike này)

- **livingdoc** (`code-tiny/livingdoc/living-doc-vectorize*.py`, `living-doc-link.py`) —
  ngoài luồng sync/MCP; ghi nhận rủi ro mixed-plane (bge-m3 ghi vào code collection) ở
  mục Risks, không sửa.
- **bge-m3 sparse/colbert heads** — không luồng nào dùng (grep toàn repo: 0 match).
- **Named-vector "v2" writer** — đọc-path có support (`_DEFAULT_NAMED_VECTOR="semantic"`)
  nhưng không writer nào tạo named vector; chỉ giữ behavior đọc nguyên trạng.
- **Chunking/payload/upsert cho sync ingest** (nội dung `primary_vector_sync.py`) — là
  quyết định của phase-05, không nằm trong spike core.
- **langextract/LLM calls, C# Roslyn** — không phải embedding, ngoài scope.
- **Rust analyzers bắt đầu embed** — không; boundary "analyzer chỉ ghi graph" giữ nguyên.

## Inventory: TOÀN BỘ touchpoint embedding/NER từ ingest đến query

Bảng này là **hợp đồng coverage** — mọi phase phải tick đủ, không được có luồng nào
không nằm trong bảng. Bằng chứng file:line từ research 2026-09-14.

| # | Luồng | Trigger | Implementation hiện tại | Model + điểm cần lưu ý parity | Post-spike owner | Phase |
|---|---|---|---|---|---|---|
| 1 | Ingest-sync code — shared-CLI parsers (go, perl, rust, shell, jp1, dart/flutter, swift) | embedding pass của orchestrator | `go_analyzer.py:1270`, `perl:288`, `rust:1318`, `shell:315`, `jp1:87`, `flutter:149`, `swift:1207` → `sync_vector_documents` (`primary_vector_sync.py:262-373`), embedder `embed_runtime.get_sentence_transformer` (`embed_runtime.py:270-295`) | jina-v3, `encode(normalize_embeddings=True)`; text bound từ `documents_from_payloads` (`:99-155`, secret-redact `:69-78`); batch 8, max 4000, hard cap 16000 | Python child (onnx chỉ khi P05 port xong chunk+upsert) | P05 |
| 2 | Ingest-sync code — legacy analyzers (python, js, java, kotlin, android, cplus, delphi, php, csharp, ts, sql, plsql, vb*) | như trên | `CodeEmbedder` riêng từng analyzer: `python_analyzer.py:904-1095`, `js:821-964`, `java:1224-1309`, `cplus:2601-2881`, `vb_analyzer_base.py:1199-1204` | jina-v3 qua `AutoTokenizer/AutoModel` + **mean-pool, max_length=512, KHÔNG normalize** (`python_analyzer.py:953-1034`) — **số học KHÁC dòng 1**; `--chunk-embed` mean-pool chunk vectors; device default `cpu` với java/cplus/csharp/sql/plsql/delphi/php | Python child | P05 |
| 2b | Ingest-sync code — cobol | như trên | `cobol/qdrant.py:105-140` (`normalize_embeddings=True`, max_chars=**800**) | jina-v3; model default env `EMBEDDING_MODEL` (khác các lane khác) | Python child | P05 |
| 3 | Orchestrator embedding pass (điểm cắm embedder) | `dev sync code` / `cortex-sync` | Rust: `cortex-sync/src/orchestrator.rs:1907-2065` + `embedding_phase_env :2486`; env inject `:2454-2469`; Python: `incremental_sync.py:3320-3482` | Delegates to child; `writes_vectors` 24 primary parsers; framework/topology = false | Seam nơi `Embedder` trait cắm vào nếu P05 chọn port | P05 |
| 4 | Message lane `_mess` | `--enable-message-scan` (17 parsers) | `message_scan.py:423-496` (`upsert_messages_to_qdrant`), text `"name \| sender \| receiver \| payload \| explanation"` (`:458-469`), batch 256 | Embedder của analyzer cha + **fallback `_hash_vector` 1024-dim SHA1-bucketing** (`:393-401, 470-484`) — deterministic, phải bit-replicate nếu port | Python child (hash fallback port theo P05 nếu chọn) | P05 |
| 5 | Rust analyzers | swap `CORTEX_RUST_ANALYZER` | `cortex-analyzer-framework/src/cli.rs:91-125` nhận-+-bỏ-qua vector flags | Không embed by design | Không đổi (boundary) | — |
| 6 | Query-code — MCP `semantic_search` | unified/fast/cplus/android MCP servers | Python in-process: `cplus_mcp.py:1650-1760` (`_embed_query:1743` → `embed_runtime.embed_query`), `fastmcp_server.py:1180-1210`, shared `qdrant_query_support.py:57-81`; preload `MCP_PRELOAD_EMBEDDER` | jina-v3; query path tokenizer `max_length=512` mean-pool **có LRU cache 512** (`embed_runtime.py:183-208`); Rust hiện trả **vector lane rỗng có chủ đích** (`tools_semantic.rs:332-341`) | **Rust ort native** (un-empty lane) | P04 |
| 7 | Query-code — MCP `explore_graph` (hybrid search) | explore_service | `explore_service.py:615-621` + `_make_embedder :169-207`; engine embed query `intelligent_retrieval.py:708-711`, named-vector resolve `:136-172` | jina-v3; fusion BM25/graph là non-embedding; Rust seeds-rỗng (`tools_explore.rs:19-20`, `cortex-retrieval/src/fusion.rs:152-155`) | **Rust ort native** (cấp seeds thật) | P04 |
| 8 | Query-mind — mind MCP `semantic_search` + `query_graph_rag_langextract` | `cortex-mcp --server mind` | Rust sidecar persistent: `cortex-mcp/src/mind/embed.rs:64-155` spawn `scripts/rust_mcp/embed_worker.py` (NDJSON stdin/stdout, 120s timeout, respawn-once), gọi tại `mind/tools.rs:664, 775`; ref Python `doc-tiny/mcp_graph_rag.py:129-137, 761-762, 841-842` | **bge-m3** dense 1024, `encode` raw (không normalize), device **cpu mặc định** (không autodetect), env `EMBEDDING_MODEL_PATH→EMBEDDING_MODEL`; response echo `dimension:1024` | **Rust ort native**, flag rollback | P02 |
| 9 | Ingest-doc | `dev sync doc` | `graphrag_ingest_langextract.py:600-607` (embed per paragraph), load `:1253-1257`; dev wiring `dev.py:1204-1232` (model default bge-m3, chunk 500); Rust twin one-shot `cortex-doc/src/embed.rs:16-89` (dim assert 1024 `:115`) | bge-m3 dense; paragraph `MAX_PARAGRAPH_CHARS` (dev 500 / argparse 1200); payload `entity_ids`+`entity_mentions` (`:607-625`); **không normalize** | **Rust ort native**, flag rollback | P02 |
| 10 | Query-doc CLI | `graphrag_query_langextract.py` | embed query `:175-184`; collection default `graphrag_entities` (`:160`) | bge-m3, cùng env chain #8 | Reuse embed seam P02 (hoặc giữ Python — quyết ở P02) | P02 |
| 11 | Livingdoc (ngoài luồng sync/MCP) | scripts thủ công | `living-doc-vectorize.py:198-259`, `-infra.py:31-61`, `living-doc-link.py:8-203` | bge-m3 **ghi vào code collection** `QDRANT_COLLECTION_CODE` — mixed-plane data có thể tồn tại | Out of scope (ghi nhận risk) | — |
| 12 | NER GLiNER — doc ingest (Python) | `dev sync doc` (default provider `gliner`, `dev.py:3601-3602`) | `entity_extractors.py:255-321` (`extract_entities_gliner`, `batch_predict_entities`, `build_gliner_model` lru_cache), labels `:12-21,177-189` | `urchade/gliner_large-v2.1`; env `GLINER_MODEL_NAME/PATH/LOCAL_ONLY`; threshold code 0.3 / dev **0.35**; batch 8 (dev: 1 + `--no-batch`); **không có device plumbing** (gliner tự chọn cuda/cpu); normalize span backfill `_normalize_entities` (`:24-64`); quirk taxonomy SSI→CRYPTO pinned ở phase-13 | P03 quyết: Rust ort hoặc giữ sidecar | P03 |
| 13 | NER GLiNER — `cortex-doc` (Rust→Python sidecar) | `cortex-doc` ingest `--entity-provider gliner` | `cortex-doc/src/providers.rs:95-215` (`GLINER_SIDECAR` inline), wiring `main.rs:138-183` | Cùng model; sidecar import `extract_entities_gliner` (byte-identity by construction) | P03 quyết | P03 |
| 14 | NER GLiNER — contract/parity | `scripts/rust_mcp/gliner_sidecar.py` (--verify), gate `compare_mind.py:278-303` (`GATE_GLINER`) | Contract `{entity,type,score,span}`; fixture ingest tắt GLiNER (regex miner) `ingest_mind_fixture.py:289-338` | Đã verify-once PASS phase-13 | Harness tái sử dụng cho P03 | P03 |

Env-var map (điểm đọc): code plane `CODE_EMBEDDING_MODEL_PATH → CODE_EMBEDDING_MODEL →
EMBED_MODEL → JINA_MODEL_PATH → jinaai/jina-embeddings-v3` (alias `EMBEDDING_MODEL` map ở
`harness_config.py:147-150`, `dev.py:750-752`); doc/mind plane `EMBEDDING_MODEL_PATH →
EMBEDDING_MODEL → BAAI/bge-m3`; device: code = `EMBED_DEVICE` (auto MPS/CUDA/CPU qua
`embed_runtime.resolve_device`), doc/mind = `EMBEDDING_DEVICE` default **cpu**, GLiNER =
không có. Qdrant: `QDRANT_CODE_PATH`, `QDRANT_DOC_PATH`, `QDRANT_COLLECTION_DOC`,
`QDRANT_API_KEY`, HNSW tuning (`local_qdrant.py:170-215`), meta cache
`MCP_COLLECTION_META_CACHE`.

## Phase map (5 phases / 2 waves)

| Phase | Wave | Scope | Gate chính |
|---|---|---|---|
| 01 | S1 | Crate `cortex-embed`: ort + tokenizers; **jina-v3** (2 đường số học #1 và #2); golden parity harness | cosine ≥ 0.999 vs Python trên corpus thật (stock) cả 2 đường |
| 02 | S1 | **bge-m3 dense** ort; swap seam mind worker (#8) + cortex-doc embed (#9, #10) sau flag `CORTEX_EMBED_BACKEND` | cosine ≥ 0.999 + P95 query-embed ≤ sidecar hiện tại; rollback flag test |
| 03 | S2 | **GLiNER ONNX**: export (fp32 trước, int8 sau), port decode Rust, so span/score/label | contract `{entity,type,score,span}` khớp Python (kể cả span backfill + SSI→CRYPTO); go/no-go rõ |
| 04 | S2 | Un-empty Rust MCP vector lanes (#6, #7) bằng ort query embed; re-baseline golden fixtures; dim-cross-plane filter fixture | MCP golden contract re-record + match; không phá phase-12/13 parity đã pass |
| 05 | S2 | Quyết định sync-path: port chunk+payload+upsert (`primary_vector_sync` + hash fallback) vào Rust sau `Embedder` trait, hay giữ Python child vĩnh viễn; benchmark; docs; dogfood add-on | Decision record + benchmark + runbook cập nhật |

## Trạng thái sau phiên 2026-09-14 (P01+P02+P03+P05; P04 defer vì dogfood)

| Phase | Verdict | Số chính | Report |
|---|---|---|---|
| 01 | **PASS** | token-id 840/840 drift=0; cosine worst 0.9999994; batch8 38.1 vs 38.1 texts/s; cold 0.46s vs 3.82s | `reports/phase01-jina-parity.md` |
| 02 | **PARTIAL → (b) RESOLVED** — parity đạt, root-cause latency đã xử lý; **flip vẫn chờ P04** | cosine doc 320 case OK; query p95 25.1 vs 41.2ms; regression +25-31ms/tool-call đã root-cause: GET `/collections` nhỏ trên keep-alive idle 20-50ms dính stall ~45ms delayed-ACK/Nagle qua ssh-tunnel colima (không phải ORT) → cache TTL 30s cho availability list; sau fix onnx p50/p95 **25.1/26.1ms — nhanh nhất** (python ref 50.3/51.6); 14 fail drift 1e-7 chờ P04 | `reports/phase02-bgem3-parity.md`, `reports/phase02b-latency-rootcause.md` |
| 03 | **GO fp32 / NO-GO int8** | fp32 848/848 exact, Δscore 1.3e-05, 1.89× nhanh; int8 mất 87.5% entity (848→106) | `reports/phase03-gliner-onnx.md` |
| 04 | **DEFERRED** | decision #7: dogfood rust-full-migration chưa xong | — |
| 05 | **NO-GO port ingest** | ingest batch8 không nhanh hơn (1.00×), rủi ro redaction/point-id/hash fallback; ort thắng ở query+NER+cold start | `reports/phase05-sync-decision.md` |

Hệ quả kiến trúc đã đổi so với plan gốc: jina-v3 **phải tự export** graph (artifact HF
không dùng được), số học là mean+normalize 8194 (không phải 2 đường 512), bge-m3 là
**CLS+normalize 8192**, và `CORTEX_EMBED_BACKEND` vẫn mặc định `python`.

## Key architectural decisions

1. **Trait `Embedder` một seam duy nhất** trong `cortex-embed`: `embed(texts, opts) ->
   Vec<Vec<f32>>` + `dimension()`; 2 backend: `OnnxEmbedder` (ort) và `SidecarEmbedder`
   (worker Python hiện tại). Chọn backend bằng env **`CORTEX_EMBED_BACKEND=python|onnx`**,
   default `python` đến khi parity + perf gate pass — rollback tức thì, cùng pattern
   `CORTEX_RUST_ANALYZER`/`CORTEX_MCP_BACKEND`.
2. ~~**2 đường số học code plane phải port đủ cả 2**~~ **(SỬA — findings C1)**: jina-v3 chỉ
   có **một** đường: mean-pool fp32 + L2-normalize, LoRA tắt (đo: 2 lane Python cosine
   1.000000, đều unit-norm). Crate vẫn giữ mode generic `mean-pool / max_length=512 /
   không normalize` cho model thay thế qua `CODE_EMBEDDING_MODEL_PATH` (khi model không có
   `.encode`), nhưng **không** sinh corpus fixture riêng cho jina-v3 theo mode đó.
3. **Tokenizer fidelity là gate số 0** trước khi chạy model — **(ĐÃ PASS, findings C4)**:
   `tokenizers` 0.23.2 tái tạo 0/20 token-id sai khác với `AutoTokenizer` trên corpus khắc,
   cho cả jina-v3 lẫn bge-m3 (đều `XLMRobertaTokenizerFast`, `tokenizer.json` đủ dùng ⇒
   `fix_mistral_regex` là no-op). Bẫy còn lại: `TruncationParams::default().max_length==512`
   ⇒ phải set tường minh 8194/8192. GLiNER = deberta-v3, chưa verify (P03).
4. **Model artifacts** — **(SỬA — findings C3)**: jina-v3 **không** có ONNX chính thức dùng
   được (graph đòi `task_id` scalar 0..4, mượn `token_type_embeddings` làm task-embedding
   ⇒ không biểu diễn được "adapter tắt"; cos tốt nhất 0.9418) ⇒ P01 tự export base graph
   bằng `torch.onnx.export` từ code đã cache, **không cần `optimum`**. bge-m3 dùng artifact
   chính thức (`onnx/model.onnx`, inputs sạch, opset 11). GLiNER export bằng
   `gliner.export_to_onnx` (đã verify chữ ký ở gliner 0.2.28). **Không model nào có int8
   chính thức** — int8 do ta `quantize_dynamic` (pkg `onnx` đã cài 1.22.0, ghi vào
   requirements.txt). fp32 trước, int8 sau — int8 chỉ nhận khi cosine ≥ 0.999 **và**
   recall@10 ≥ 0.99 trên bộ query fixture (cosine một mình không đủ với quantized model).
5. **CPU-first**: khớp mặc định hiện tại (doc/mind = cpu, nhiều analyzer = cpu); MPS/Metal
   qua ORT execution provider là follow-up, không chặn gate. Golden vectors luôn generate
   trên CPU để loại trừ drift FP non-determinism.
6. **GLiNER theo evidence, không theo premise cũ**: fp32 trước (cảnh báo int8-mất-accuracy
   của deberta trong chính gliner 0.2.28); parity so ở mức contract (span/score/label/order)
   chứ không ở mức tensor; nếu fail → giữ sidecar, ghi decision record, không vào lực.
7. **Fixture re-baseline có kiểm soát**: un-empty lane #6/#7 làm thay đổi response MCP
   Rust → golden fixtures phase-12/13 phải re-record và đánh dấu trong report; **không
   re-record trong thời gian dogfood 1 tuần của rust-full-migration đang chạy** (spire
   trên branch, flip sau khi dogfood xong).
8. **1024-dim cross-plane**: cả jina-v3 lẫn bge-m3 đều 1024-dim nên `filter_collections_for_vector`
   (size-match) có thể cross-match collection giữa 2 plane — behavior hiện tại phải được
   fixture hoá TRƯỚC khi đổi embedder, không phải sau.

## Risks & gates

| Risk | Severity | Gate |
|---|---|---|
| jina-v3 int8 không đạt cosine 0.999 (quantized) | High | fp32 gate trước; int8 gate kép cosine + recall@10; fail ⇒ giữ fp32 hoặc Python |
| Tokenizer khác số học Python (trust_remote_code, fix_mistral_regex, sentencepiece) | High | Phase-01: so token-id trước, cosine sau; fail ⇒ dừng trước khi tốn effort model |
| GLiNER int8 mất accuracy (deberta) | High | fp32 first; decode-parity trên corpus doc thật; go/no-go |
| bge-m3 ONNX không có export "chính thức" | Medium | Export bằng optimum từ .venv pin version; so parity chính là gate |
| ORT crate/ep kỷ luật phiên bản (macOS arm64) | Medium | Pin `ort` + `tokenizers` version trong workspace Cargo.toml; CI chạy cargo test |
| Thay response MCP vỡ parity phase-12/13 + dogfood đang chạy | High | Decision #7: re-baseline ngoài window dogfood; cả 2 backend cùng catalog/shape |
| Mixed-plane livingdoc data trong code collections | Low | Inventory-only; fixture hoá behavior lọc collection |
| Throughput ort thấp hơn torch trên CPU cho ingest | Medium | Benchmark P05 (batch 8/32/128); nếu thua ⇒ ingest giữ Python child, query vẫn ort |

## Verification strategy

- **Golden vector parity**: sinh từ corpus thật (stock code texts 2 đường số học + stock
  doc paragraphs + message texts), script `scripts/rust_parity/embed_parity.py` — Python
  dump vectors → JSON fixture → Rust đọc + so cosine từng cặp ≥ 0.999; token-id diff = 0.
- **Contract parity GLiNER**: cùng corpus doc, so `{entity,type,score,span}` exact
  (score tolerance 1e-3), tái dùng `compare_mind.py` GATE_GLINER pattern.
- **MCP golden re-baseline**: record lại fixture từ Python + Rust-onnx, byte-match theo
  danh sách volatile case-by-case.
- **CI**: `cargo test --workspace` + parity suites mới thêm vào required checks của plan này.
- **Perf**: P95 query-embed (mind tools) và throughput ingest (texts/s) Python vs ort,
  report vào `reports/`.

## Active-plan coordination

- `260913-2130-rust-full-migration` (code-complete, đang dogfood 1 tuần): plan này là
  Phương án A được defer từ phase-09; spike chạy song song trên branch, mọi flip fixture
  mặc định đợi dogfood xong (decision #7). Đã cập nhật relatedPlans 2 chiều.
- `260829-2322-vector-search-query-optimization` (implemented): kế thừa quyết định device
  autodetect + batch sizing (EMBED_BATCH_SIZE 8, phase-06 report); fixture lọc collection
  dùng lại bộ test của plan đó.
- `260913-1715-rust-retrieval-graph-port`: crates `cortex-retrieval` (fusion seeds) là
  điểm tiêu thụ seeds thật ở phase-04.

## Red-team notes (self, 2026-09-14)

- **Cosine 0.999 một mình không đủ với int8** → gate kép cosine + recall@10 (decision #4).
- **Quên query-side sẽ là lack luồng lớn nhất** → #6/#7 (Rust lane rỗng) là phase riêng P04,
  không gộp vào P02; fixture re-baseline tách khỏi dogfood.
- **2 đường số học code plane** — nếu chỉ port 1 đường sẽ im lặng sai vectors cho nửa
  analyzers còn lại → P01 bắt buộc cả 2 đường, fixture tách biệt.
- **`_hash_vector` message fallback** — deterministic, phải bit-replicate nếu P05 port
  message lane; đã ghi rõ trong P05 scope.
- **Điều P05 phải trả lời bằng số, không bằng thị hiếu**: giữ Python child cho ingest
  là kết quả hợp lệ nếu throughput ort thua torch CPU — decision record là deliverable.
