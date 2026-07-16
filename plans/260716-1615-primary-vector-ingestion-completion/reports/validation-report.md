---
type: validation report
date: 2026-07-16
---
# Validation Report: Primary Analyzer Vector Ingestion

## Summary

Rust, Go, Swift, Perl, and primary Dart now invoke a shared incremental-safe Qdrant adapter after staged graph persistence. Framework overlays remain graph-only and declare their primary semantic seed collections. Configured vector failures are fatal; an absent Qdrant URL remains a supported graph-only run.

## Findings

### Contract and implementation

- Deterministic Qdrant point IDs use parser, project, root scope, and semantic symbol identity, never batch order or absolute checkout path.
- Payloads include the required project, parser, root, language, repository, file, symbol, node-type, name, qualified-name, and bounded searchable-text fields.
- Common credential assignments and private-key blocks are redacted before embedding and payload creation.
- Embedding and HTTP dependencies remain lazy so graph-only execution does not require vector packages.
- Points are embedded and upserted before stale deletion. Cleanup filters include project, parser, and root scope; incremental cleanup also filters changed/impacted/deleted paths.
- Dart vectors are emitted only for `--mode dart`; Flutter overlay runs remain graph-only.
- Primary registry entries explicitly declare `writes_vectors=True`. Overlay entries retain `writes_vectors=False` and expose collections derived from their prerequisite parsers.
- Root sync commands pass `--embed-model jinaai/jina-embeddings-v3` by default, and incremental analyzer commands pass the resolved model explicitly to Dart, Go, Perl, Rust, and Swift.
- Analyzer `[SCAN_RESULT]` vector counts are retained in incremental-sync summaries while subprocess output is streamed with only a bounded diagnostic tail held in memory.

### Automated verification

| Command | Result |
| --- | --- |
| `python -m pytest -q tests/test_primary_analyzer_vector_contract.py tests/test_semantic_graph_expansion.py tests/test_framework_mcp_search.py` | 12 passed, 19 subtests passed |
| `.venv/bin/python -m unittest discover -s tests -p 'test_perl*.py' -v` | 15 passed |
| `python -m pytest -q tests --ignore-glob='tests/test_cobol*' --ignore=tests/test_incremental_sync_cobol.py --ignore=tests/test_dev_cobol_parser_discovery.py --ignore-glob='tests/test_perl*'` | 154 passed, 39 subtests passed |

The focused suite covers deterministic mapping and root-scoped identity, structured-secret redaction, text bounds, batching, delete-only incremental cleanup, cross-scope filters, configured failure propagation, all five new analyzer calls, registry strategy, Dart overlay exclusion, root/incremental environment propagation, optional graph-only Qdrant behavior, and full-scan command selection. The broader suite includes existing retrieval and framework routing coverage.

### Named overlay retrieval acceptance

These checks combine named expansion contracts with fixture-generated evidence. Spring, Servlet/JSP, and MyBatis fixture facts contain real primary Java `source_symbol_id` values connected by `SEMANTIC_OF`, with project and source paths asserted. ASP.NET Core compilation produces canonical C# links. Dart and Flutter modes produce the same stable symbol identities and relationships, so Dart vectors directly seed the Flutter view. Struts' XML-only action facts do not expose a canonical Java anchor; its named query is therefore served by the existing framework-filtered graph property/full-text fallback rather than a speculative Qdrant collection.

| Named query | Primary seed | Reached overlay fact | Result |
| --- | --- | --- | --- |
| `spring order service transaction` | Java `java-order-service` | `SpringBean` | Pass |
| `servlet login endpoint` | Java `java-login-servlet` | `ServletEndpoint` | Pass |
| `mybatis catalog statement` | Java `java-catalog-repository` | `MyBatisStatement` | Pass |
| `struts checkout action` | Framework-filtered graph property search | `StrutsAction` | Pass via graph fallback |
| `flutter home route` | Dart `dart-home-widget` | `FlutterRoute` | Pass |
| `aspnet core orders endpoint` | C# `csharp-orders-controller` | `HttpEndpoint` | Pass |
| `aspnet framework home endpoint` | C# `csharp-home-controller` | `HttpEndpoint` | Pass |

The named expansion matrix is exercised by `test_named_framework_queries_expand_from_primary_language_seeds`. Fixture-backed anchors are asserted by `test_mixed_fixture_produces_framework_facts_without_graph_services`, Dart/Flutter identity parity by `test_flutter_overlay_reuses_primary_dart_symbol_identities`, compiled C# links by `test_semantic_links_target_canonical_csharp_ids_only_when_compilation_succeeds`, and Struts-capable framework-filtered property lookup by the framework MCP search contract.

### Live Qdrant smoke verification

A temporary local collection was created against `http://127.0.0.1:6333`, exercised, asserted, and deleted in a `finally` cleanup.

| Scenario | Observed count |
| --- | ---: |
| Initial two-point full sync | 2 |
| Idempotent repeat | 2 |
| Incremental deletion of one path | 1 |
| Full refresh after adding another project/root scope | 3 total |
| Other project/root points preserved | 1 |

### Environment note

The system Python environment cannot load the pinned Perl grammar; the repository `.venv` passes all 15 Perl tests. COBOL parser-runtime preflight currently fails in both environments with `COBOL_RUNTIME_PARSE_FAILED`, causing the pre-existing COBOL parser-derived suites to produce empty facts. No COBOL source or writer implementation changed in this plan; the shared adapter and focused COBOL command regression pass independently. This existing runtime issue is outside the vector-ingestion scope and remains visible rather than being masked.

## Recommendations

- Use the repository `.venv` for Perl analyzer validation and execution.
- Repair or repin the portable COBOL grammar in its owning plan before using parser-derived COBOL regressions as a release gate.
- Keep framework overlays graph-only unless a named retrieval acceptance case demonstrates an unanchored fact that primary semantic seeds plus graph expansion cannot reach.

## Unresolved Questions

- The COBOL portable grammar/runtime mismatch requires follow-up in `260714-1702-cobol-analyzer-parser`; it does not originate from this implementation.
