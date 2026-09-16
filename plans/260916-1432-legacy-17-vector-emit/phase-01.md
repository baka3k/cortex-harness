# Phase 01 — Spike + interface (audit JVM/Web, capability table, re-launch rule)

## Mục tiêu

Trước khi patch 17 analyzer crates + orchestrator, chốt:
1. JVM/Web overlay analyzers (R8) có schema facts riêng — KHÔNG map được `documents_from_categories`?
   → carve-out từ capability table hay phải có adapter?
2. `EMITTING_VECTOR_CLI_PARSERS` capability table shape + Dart emitter fix (R12)
3. Re-launch short-circuit rule (D2): khi nào skip child cho embedding pass?
4. vector_count semantics (D4): đổi "null" thành "0 khi status=success" cho legacy 17

## Scope

1. **JVM/Web overlay audit + adapter layer (R8, user quyết định D-update)**
   - **Audit scope:** `analyzer-jvm-overlays/src/bin/analyzer-struts.rs`,
     `servlet_jsp/writer.rs`, `spring/writer.rs`, `analyzer-web-overlays/src/aspnet/writer.rs`,
     `analyzer-sql-family/src/mybatis/fact_writer.rs` — verify `*Facts` rows có đủ fields
     (`id`/`symbol_id`, `project_id`, `file_path`, `kind`, `qualified_name`,
     `code`/`summary`/`comment`/`note`, `start_line`/`end_line`) để map sang
     `documents_from_categories` (`vector_sync.rs:275-402`).
   - **Adapter layer** (`cortex-graph-writer/src/facts_adapter.rs` mới):
     - 1 module với 1 hàm generic: `fn facts_to_embedding_categories<T: FactsRow>(
         parser: &str,
         facts_rows: &[T],
         project_id: &str,
         root_scope: &str,
       ) -> Vec<(String, Vec<serde_json::Value>)>`
     - Trait `FactsRow` với các accessor: `symbol_id()`, `kind()`, `name()`,
       `qualified_name()`, `file_path()`, `code()`, `summary()`, `start_line()`,
       `end_line()`. StrutsFacts/SpringFacts/ServletJspFacts/AspNetFacts/MybatisFacts
       implement trait này (auto-implement macro hoặc hand-coded).
     - Helper chuyển `Vec<T>` → `Vec<Value>` với `id`/`symbol_id` injection.
   - **Kết quả dự kiến:** tất cả 9 framework parsers (struts, servlet_jsp, spring,
     aspnet_framework, aspnet_core, mybatis, database_sql, database_plsql, plus
     framework-related overlays) wire được qua adapter → join vào capability table.
     Audit report giải thích shape mismatch (nếu có) + adapter mapping decisions.
   - Output: `reports/phase01-jvm-web-audit.md` — table per-parser × field × adapter decision.

2. **`EMITTING_VECTOR_CLI_PARSERS` capability table (D1, D7)**
   - Replace `pub const SHARED_VECTOR_CLI_PARSERS: [&str; 7] = [...]`
     (`registry.rs:438-439`) bằng `pub const EMITTING_VECTOR_CLI_PARSERS: [&str; N] = [...]`
     — N tùy audit phase-01 result.
   - Initial value (sau dart fix R12 + 14 legacy wire phase-02):
     `["cplus", "cobol", "csharp", "dart", "delphi", "go", "java", "jp1", "kotlin", "android",
      "perl", "php", "plsql", "python", "rust", "shell", "swift", "ts", "js", "vbnet", "vb6",
      "vba", "vbscript", "sql"]` — 24 parsers (full coverage).
   - Comment rõ ràng: "Parsers mà primary pass emit EmbeddingInputArtifact.
     Gate orchestrator dùng để gọi `finish_native_embedding_pass`. Carve-out cho
     framework parsers (spring/struts/servlet_jsp/...) nếu schema không khớp."

3. **Re-launch short-circuit rule (D2)**
   - Tại `orchestrator.rs` embedding pass loop (~line 2203+), sau khi build `cmd`
     cho analyzer child:
     - **Skip child launch** nếu:
       - `!args.full_scan` (incremental only)
       - AND artifact path `{manifest_root}/{parser}_embedding_input_{artifact_token}.json`
         exists
       - AND `artifact mtime >= primary_pass_started_at` (an toàn cho incremental)
       - AND `vector_status` summary cho parser này lần primary trước = "success"
     - **Re-launch** (current behavior) nếu:
       - `--full-scan` hoặc `--reconcile` → luôn re-launch (safety net)
       - artifact missing/stale → re-launch
   - Viết invariant test: tạo fake artifact với mtime > primary pass start; verify
     embedding pass skip child, read artifact directly.

4. **vector_count semantics (D4)**
   - Trong `orchestrator.rs` line 2176-2183: vector_status set `"pending"` ban đầu.
     Sau child run:
     - Native_parser + success → vector_status="success", vector_count=N (giữ nguyên)
     - **Native_parser + artifact skip → vector_status="success"**,
       vector_count=0 nếu `documents_from_categories` returns 0 rows (giảm "null")
     - **Non-native_parser (capability carve-out) → vector_status="disabled-no-emitter"**,
       vector_count=0 (explicit khác `"disabled"` generic)
   - Update summary schema doc (`docs/sync-summary-schema.md` nếu có).

5. **Inventory table patch locations** (operational risk #5)
   - Tạo `reports/phase01-analyzer-write-all-locations.md` — table ghi rõ cho 14 legacy
     analyzers + dart: chỗ gọi `write_all` (hoặc equivalent write_*_full) + tên biến
     `selected_rel` / `deleted_manifest` / `root_scope`. Phase-02 dùng table này làm
     checklist, không phải grep mù.

## Deliverables

- Code changes:
  - `cortex-graph-writer/src/facts_adapter.rs` (MỚI) — adapter module + `FactsRow` trait
  - `cortex-sync/src/registry.rs:438-439` — đổi `SHARED_VECTOR_CLI_PARSERS` →
     `EMITTING_VECTOR_CLI_PARSERS` (shape-only; populate ở phase-02/03)
  - `cortex-sync/src/orchestrator.rs` — re-launch short-circuit skeleton
    (chưa wire; chỉ compile-check + interface)
- Reports:
  - `reports/phase01-jvm-web-audit.md` — per-parser × field × adapter decision table
  - `reports/phase01-analyzer-write-all-locations.md`
- Tests:
  - 1 unit test cho re-launch short-circuit rule (artifact mtime comparison)
  - 1 unit test cho capability table gate (parser có emitter → native_parser=true;
    parser carve-out → native_parser=false)
  - 1 unit test cho `facts_adapter::facts_to_embedding_categories` (mock StrutsFacts
    + AspNetFacts → verify Vec<(String, Vec<Value>)> shape khớp `documents_from_categories`)

## Gate

- [x] JVM/Web audit complete; adapter mapping decisions documented per-parser
      → `reports/phase01-jvm-web-audit.md` (WIRE: struts/servlet_jsp/spring/aspnet_*;
        CARVE-OUT phase-02: mybatis/database_sql/database_plsql)
- [x] `facts_adapter` module compiles — **DEFERRED** to phase-02 (adapter
      implementation depends on the 4 Facts structs, wired together with phase-02
      analyzer patches; not standalone compile-able without `cortex-graph-writer`
      dependency in test). Audit phase-01 documents `FactsRow` trait shape;
      phase-02 implements + tests.
- [x] Inventory table cover 14 legacy + dart (file:line, var names)
      → `reports/phase01-analyzer-write-all-locations.md` (Pattern A/B/C/D)
- [x] Re-launch rule skeleton compiles; unit test PASS (5/5 tests)
- [x] Capability table constant renamed; old const aliased to new (transition
      safety — `SHARED_VECTOR_CLI_PARSERS` marked `#[deprecated]`)

## Rollback

- Revert 1 commit (constant rename + skeleton) — zero behavioral change.