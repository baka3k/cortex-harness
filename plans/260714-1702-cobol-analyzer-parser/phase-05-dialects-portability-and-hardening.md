# Phase 05: Harden Dialects, Portability, and Validation

## Context

The MVP proves one local grammar/runtime and a representative semantic path. The source specification additionally claims ANSI, IBM Enterprise, Micro Focus, and GnuCOBOL coverage across z/OS, Linux, and Windows. Those claims require an explicit compatibility matrix, portable grammar delivery, scale/error recovery, and provider parity.

## Requirements

- Validate supported syntax across the four named COBOL families.
- Cover fixed/free formats, continuation, comments, sequence areas, compiler directives, and representative encodings.
- Provide verified grammar loading for Darwin, Linux, and Windows or a reproducible supported build/install process for each.
- Deepen SQL/CICS extraction only where source fixtures and grammar evidence support it.
- Measure parse quality, error recovery, full-scan performance, and incremental fan-out.
- Validate logical graph/MCP parity against Neo4j and FalkorDB when available.
- Publish operational setup, limitations, troubleshooting, and extension guidance for JCL/DB2/CICS/IMS analyzers.

## Architecture

Keep dialect and platform behavior behind configuration/data tables rather than branching semantic logic throughout the analyzer. The runtime reports platform, architecture, grammar checksum/version, analyzer version, detected format/dialect, error-node count, and degraded capabilities in every summary.

Compatibility acceptance is evidence-based:

- `supported`: fixture parses and expected semantic graph passes;
- `partial`: useful facts are produced with named diagnostics/limitations;
- `unsupported`: deterministic preflight or syntax diagnostic with remediation.

No platform or dialect is labeled supported solely because the parser loads.

## Related Files

Modify:

- `code-tiny/tools/cobol/parser_runtime.py`
- `code-tiny/tools/cobol/parser.py`
- `code-tiny/tools/cobol/resolver.py`
- `code-tiny/tools/cobol/cfg.py`
- `code-tiny/tools/cobol/semantics.py`
- `code-tiny/tools/cobol/pipeline.py`
- `code-tiny/tools/cobol/README.md`
- `tests/fixtures/cobol-application/`

Create:

- `tests/fixtures/cobol-dialects/`
- `tests/test_cobol_dialect_matrix.py`
- `tests/test_cobol_source_formats.py`
- `tests/test_cobol_runtime_platforms.py`
- `tests/test_cobol_error_recovery.py`
- `tests/test_cobol_performance.py`
- `tests/test_cobol_provider_parity.py`
- `docs/specs/cobol-analyzer.md`

Potentially add, only after deciding the distribution strategy:

- platform-specific grammar artifacts under `code-tiny/tools/cobol/lib/`
- a reproducible grammar build script under `scripts/`

## Implementation Steps

1. Define a dialect/source-format fixture matrix with expected parse status, diagnostics, and semantic invariants.
2. Add offset-preserving normalization and decoding tests for representative mainframe/local source forms.
3. Decide and implement the grammar distribution strategy: bundled per-platform artifacts, Python package, or documented reproducible build output with checksum verification.
4. Remove or contain the deprecated pointer API when a supported Tree-sitter binding path is available; retain compatibility tests for the bundled Darwin library.
5. Expand SQL statement operation/table/host-variable extraction and CICS command/program/file/resource extraction conservatively.
6. Test malformed files, partial ASTs, missing copybooks, large include fan-out, dynamic calls, and graph-write recovery.
7. Establish medium-project full/incremental timing and memory baselines with documented thresholds.
8. Run fake-driver parity continuously and live Neo4j/FalkorDB logical parity after the migration blocker clears.
9. Run the complete parser/sync/MCP regression suite and document explicit exclusions for unavailable services/platforms.
10. Publish the compatibility matrix, runtime setup, copybook configuration, graph schema, CLI examples, limitations, and future analyzer seams.

## Todo

- [ ] Approve platform grammar distribution and licensing/provenance requirements.
- [ ] Record supported/partial/unsupported status for each dialect/format pair.
- [ ] Define performance thresholds from a representative repository size.
- [ ] Verify live provider parity or document service-specific exclusions.
- [ ] Confirm all source-spec success criteria map to passing tests or explicit future-extension exclusions.

## Risks

- A grammar artifact may be portable across architectures but not operating systems or Tree-sitter ABI versions.
- Dialect coverage can become open-ended without a finite fixture matrix.
- EBCDIC/code-page handling can corrupt source evidence if decoding and byte offsets diverge.
- Deep DB2/CICS semantics can accidentally expand into the future analyzers excluded from this plan.
- Performance tests based only on synthetic fixtures may miss real copybook fan-out and dynamic-call patterns.

## Success Criteria

- Every named dialect/source-format combination has a documented, tested support status.
- Darwin, Linux, and Windows each have a verified grammar path or a reproducible supported installation path with clear preflight output.
- Error recovery preserves valid surrounding facts and never promotes error-derived facts to high confidence without evidence.
- SQL/CICS extraction meets the bounded specification scope and retains raw statements for future analyzers.
- Performance and incremental invalidation baselines are recorded and pass agreed thresholds.
- Neo4j/FalkorDB logical parity passes or remaining service exclusions are explicit and traceable.
- Documentation is sufficient for another developer to install, scan, query, troubleshoot, and extend the COBOL analyzer.
