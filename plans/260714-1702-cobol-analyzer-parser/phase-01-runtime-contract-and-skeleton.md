# Phase 01: Establish the Runtime Contract and Tool Skeleton

## Context

The target directory has a working Darwin grammar binary but no analyzer. Before semantic extraction, the project needs a stable Python package, a tested grammar loader, a versioned fact contract, and representative fixtures. This phase must not write to the graph.

## Requirements

- Keep all new COBOL tool code under `code-tiny/tools/cobol`.
- Expose the existing analyzer CLI shape, including project, graph provider, Qdrant, full/incremental manifests, cache, and logging arguments.
- Load the bundled `tree_sitter_cobol` symbol with deterministic preflight failures.
- Support an explicit grammar-library override for non-Darwin and development builds.
- Record grammar/analyzer versions and syntax diagnostics.
- Define deterministic nodes, edges, evidence, diagnostics, and summary models.
- Create the multi-file fixture and AST golden snapshots before writing semantic extractors.

## Architecture

`cobol_analyzer.py` is the only executable entry point. It delegates runtime discovery to `parser_runtime.py` and staged analysis to `pipeline.py`. `models.py` defines an internal schema version and source-evidence type. `parser.py` initially exposes syntax-tree traversal and grammar-node inspection only.

The loader resolution order is:

1. CLI `--cobol-language-library`;
2. `COBOL_LANGUAGE_LIBRARY`;
3. bundled compatible artifact under `lib/`.

Preflight validates path, platform/architecture, dynamic loading, exported symbol, Tree-sitter construction, and a minimal parse. It returns actionable diagnostics and never reaches graph setup on failure.

## Related Files

Create:

- `code-tiny/tools/cobol/__init__.py`
- `code-tiny/tools/cobol/cobol_analyzer.py`
- `code-tiny/tools/cobol/models.py`
- `code-tiny/tools/cobol/parser_runtime.py`
- `code-tiny/tools/cobol/parser.py`
- `code-tiny/tools/cobol/pipeline.py`
- `tests/fixtures/cobol-application/`
- `tests/test_cobol_analyzer_imports.py`
- `tests/test_cobol_parser_runtime.py`
- `tests/test_cobol_fact_contract.py`
- `tests/test_cobol_ast_golden.py`

Modify:

- `requirements.txt` only if a compatible Tree-sitter constraint is required
- `code-tiny/requirements.txt` only if a compatible Tree-sitter constraint is required

Reference:

- `code-tiny/tools/flutter/models.py`
- `code-tiny/tools/flutter/protocol.py`
- `code-tiny/tools/flutter/dart_parser.py`
- `code-tiny/tools/graph/cli.py`

## Implementation Steps

1. Define schema version `1`, stable identity inputs, source evidence, confidence, diagnostics, and deterministic summary counts.
2. Build a fixture with two programs and copybooks covering every syntax family promised by the specification, plus malformed and dialect-specific cases.
3. Capture AST node names and error ranges for the supplied grammar; explicitly include `GO TO DEPENDING ON`, `PERFORM THRU`, `EXEC SQL`, and `EXEC CICS` compatibility cases.
4. Implement runtime resolution and preflight, containing the deprecated pointer conversion in one compatibility function.
5. Add the analyzer CLI skeleton and a `--preflight`/dry parse path that performs no graph mutation.
6. Add import, runtime, unsupported-platform, invalid-library, deterministic serialization, and AST golden tests.

## Todo

- [x] Approve fact schema version `1` and stable-ID components.
- [x] Record that this checkout has no bundled binary and document the portable language-pack runtime plus native checksum reporting.
- [x] Add the representative fixture and grammar/error-node contract tests.
- [x] Verify two identical analyses produce byte-equivalent facts and summaries.
- [x] Verify runtime failures occur before graph/Qdrant connections are created.

## Risks

- The current pointer-based Tree-sitter API is deprecated and may be removed by a dependency upgrade.
- The binary filename implies CPython 3.10/Darwin even though it exports a plain grammar symbol; assumptions must be proven by preflight rather than inferred from the name.
- Grammar error recovery can produce plausible child nodes inside invalid statements.

## Success Criteria

- The bundled grammar parses the minimal and representative Darwin fixtures on both supported local architectures where available.
- Missing/incompatible grammar scenarios fail with stable diagnostic codes and remediation text.
- The package imports without opening graph or vector connections.
- The fixture AST goldens expose all extractor-relevant node types and explicitly identify unsupported/error-node cases.
- Fact serialization, IDs, evidence, and summary ordering are deterministic.
