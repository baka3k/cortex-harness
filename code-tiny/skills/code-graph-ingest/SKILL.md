---
name: code-graph-ingest
description: Parse and ingest source code into FalkorDB and Qdrant using the Rust analyzer binaries in `rust/target/release/`. Use when asked to scan repositories, build call graphs, store symbols/relations in FalkorDB, push embeddings into Qdrant, tune batching/caching, or run any of the 24+12+topology analyzer languages. Phase-08 (analyzer-layer-rust-cutover) retired the Python analyzer entry points; the Rust binary is the only supported backend.
---

# Code Graph Ingest (Rust analyzer cutover)

## Workflow
1) Identify language(s) and root folder(s) to scan.
2) Ensure Rust analyzer binaries are built: `cargo build --release -p analyzer-<lang>` (or the umbrella `cargo build --release` for the whole workspace).
3) Decide where to write:
   - FalkorDB only, Qdrant only, or both.
   - Set connection env vars or pass CLI flags.
4) Optionally run a dry-run to count files.
5) Run the language-specific Rust analyzer binary directly (or `dev sync code` / `incremental_sync.py` for the orchestrator path).
6) Validate results (FalkorDB writes + Qdrant collection updated).

## Analyzer selection (Rust binaries — `rust/target/release/`)
- Kotlin: `analyzer-kotlin`
- Java: `analyzer-java`
- TypeScript: `analyzer-ts`
- JavaScript: `analyzer-js`
- PHP: `analyzer-php`
- SQL: `analyzer-sql`
- PL/SQL: `analyzer-plsql`
- C#: `analyzer-csharp` (spawns the Roslyn worker; requires the .NET SDK)
- C/C++: `analyzer-cplus` (spawns the Python clang plane under `tools/cplus/`)
- COBOL: `analyzer-cobol`
- Dart: `analyzer-dart`
- Project topology: `analyzer-topology`
- Framework overlays: `analyzer-spring`, `analyzer-servlet-jsp`, `analyzer-mybatis`,
  `analyzer-struts`, `analyzer-aspnet-framework`, `analyzer-aspnet-core`,
  `analyzer-fastapi-django`, `analyzer-express-js`, `analyzer-laravel`,
  `analyzer-database-schema`

## Cutover semantics (post-phase-08)
- `CORTEX_RUST_ANALYZER=unset` → Rust binary auto-selected (binary must be built).
- `CORTEX_RUST_ANALYZER=rust` → Rust binary selected (hard error if missing).
- `CORTEX_RUST_ANALYZER=python` (or any other value) → **loud "retired" error**.
  The Python analyzer plane was archived at the phase-08 cutover commit.
  Rollback path: `git revert <phase-08-commit>` then rebuild Rust binaries.

## Key behaviors to remember
- FalkorDB writes happen only when `--falkordb-*` credentials are provided.
- Qdrant writes happen only when `--qdrant-url` is provided (orchestrator-level
  embedding pass via `cortex-embed` for shared-7 lineage; legacy 17 parsers
  currently report `vectors=0 vector_status=disabled` — see
  `reports/phase08-cutover.md` for the embed-regression disposition).
- Parsers support caching and resume; disable explicitly if needed.
- C/C++ analyzer spawns the Python clang plane (`tools/cplus/clang_parser.py`
  + `tools/cplus/semantic_worker.py` + `tools/cplus/proc_analyzer.py`) — the
  Python clang plane is intentionally retained, only the `cplus_analyzer.py`
  entry was retired.
- C# analyzer spawns the Roslyn worker (`tools/csharp/roslyn_worker/`) and
  bootstraps the build; the dotnet SDK is required.
- Dart analyzer accepts `--mode dart|flutter` (no `all`); orchestrator chooses
  the mode per invocation.

## References
- `references/env.md` for environment variables and defaults.
- `references/analyzers.md` for full CLI flags and per-language specifics.
- `references/examples.md` for common command templates (now points to the
  Rust binaries, not the deleted Python entries).