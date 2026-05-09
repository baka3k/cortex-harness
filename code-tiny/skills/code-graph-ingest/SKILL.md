---
name: code-graph-ingest
description: Parse and ingest source code into Neo4j and Qdrant using the Python analyzers in graph-code-tiny. Use when asked to scan repositories, build call graphs, store symbols/relations in Neo4j, push embeddings into Qdrant, tune batching/caching, or run Kotlin/Java/C#/C++/TypeScript/JavaScript/PHP/SQL/PL/SQL analyzers.
---

# Code Graph Ingest (graph-code-tiny)

## Workflow
1) Identify language(s) and root folder(s) to scan.
2) Ensure Python deps are installed from `requirements.txt` and Tree-sitter is available.
3) Decide where to write:
   - Neo4j only, Qdrant only, or both.
   - Set connection env vars or pass CLI flags.
4) Optionally run a dry-run to count files.
5) Run the language-specific analyzer.
6) Validate results (Neo4j writes + Qdrant collection updated).

## Analyzer selection
- Kotlin: `tools/kotlin/kotlin_analyzer.py`
- Java: `tools/java/java_analyzer.py`
- TypeScript: `tools/ts/ts_analyzer.py`
- JavaScript: `tools/js/js_analyzer.py`
- PHP: `tools/php/php_analyzer.py`
- SQL: `tools/sql/sql_analyzer.py`
- PL/SQL: `tools/plsql/plsql_analyzer.py`
- C#: `tools/csharp/csharp_analyzer.py`
- C/C++: `tools/cplus/cplus_analyzer.py`

## Key behaviors to remember
- Neo4j writes happen only when `--neo4j-*` credentials are provided.
- Qdrant writes happen only when `--qdrant-url` is provided.
- Parsers support caching and resume; disable explicitly if needed.
- C/C++ analyzer supports extra export files (event map, call stats, possible/unresolved calls).
- Kotlin uses embedder vector size from the model; Java/C#/C++ assume 768.

## References
- `references/env.md` for environment variables and defaults.
- `references/analyzers.md` for full CLI flags and per-language specifics.
- `references/examples.md` for common command templates.
