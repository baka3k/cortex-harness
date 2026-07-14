# COBOL analyzer

The CortexHarness COBOL analyzer owns `.cbl`, `.cob`, `.cpy`, and `.copy` files. It runs as one staged primary-language analyzer: parse all source, resolve copybooks and project symbols, build paragraph control flow, then write the complete staged batch through the provider-neutral graph driver.

## Runtime

The default runtime is the `cobol` grammar shipped by `tree-sitter-language-pack`. The tested development versions are Python 3.10, `tree-sitter` 0.25.x, grammar ABI 14, and `tree-sitter-language-pack` 1.12.x. The package supplies platform wheels for Windows, Linux, and Darwin.

Resolution order:

1. `--cobol-language-library PATH`;
2. `COBOL_LANGUAGE_LIBRARY`;
3. a compatible artifact in `tools/cobol/lib`;
4. `tree-sitter-language-pack`.

An explicit native library must export `tree_sitter_cobol`. Preflight validates loading, the Tree-sitter binding/grammar ABI, and a minimal parse before any graph or Qdrant connection is created.

Native grammar overrides and embedding models are executable-code trust boundaries. Use only artifacts/models approved by the project operator; the preflight records the native library SHA-256 for auditability. Qdrant targets must be absolute HTTP(S) URLs without embedded credentials, and collection names are restricted to safe path characters.

```powershell
python code-tiny/tools/cobol/cobol_analyzer.py --root C:\src\legacy --preflight
python code-tiny/tools/cobol/cobol_analyzer.py --root C:\src\legacy --project-id legacy --dry-run
dev sync code --parsers cobol
```

Additional copybook roots may be repeated:

```powershell
python code-tiny/tools/cobol/cobol_analyzer.py --root C:\src\legacy --copybook-root C:\shared\copybooks
```

## Facts

Nodes are project-scoped and use stable IDs: `CobolProgram`, `CobolSection`, `CobolParagraph`, `CobolDataItem`, `CobolCopybook`, `CobolFile`, `CobolSqlStatement`, and `CobolCicsCommand`. Source files retain the canonical `File` label.

Relationships include `DEFINES`, `INCLUDES`, `REFERENCES`, `CALLS`, `PERFORMS`, `PERFORMS_THRU`, `RETURNS`, `GOES_TO`, `GOES_TO_DYNAMIC`, `FALLS_THROUGH`, `ALTERS`, `CONDITIONAL`, `EXITS`, `READS`, and `WRITES`. Every edge records project scope, source range, confidence, and whether the target is dynamic.

Copybook dependencies are persisted under `.cortex/cobol`. A changed or deleted copybook invalidates direct and transitive consumers. Graph cleanup occurs only after parsing, resolution, normalization, and the replacement write succeed.

## Compatibility

| Family / format | Status | Evidence and limitation |
| --- | --- | --- |
| ANSI-style fixed/free | supported | Program, data, paragraph, CFG, and terminal-flow fixtures pass. |
| IBM Enterprise fixed | partial | Sequence areas and `CBL` directives are handled; SQL/CICS content is recovered from raw source when the grammar returns an error node. |
| Micro Focus free | partial | `$SET` dialect detection and core structure pass; vendor extensions outside the fixture remain diagnostic. |
| GnuCOBOL free | supported | `>>SOURCE FORMAT IS FREE` and core semantic fixture pass. |

Malformed syntax preserves surrounding facts and emits `COBOL_SYNTAX_*` diagnostics. Dynamic calls, computed branches, `ALTER`, and unresolved targets never create high-confidence fabricated destinations. `COPY REPLACING` text is preserved but substitution is intentionally partial and diagnostic.

The analyzer retains raw SQL/CICS statements and extracts conservative operation/resource metadata; it is not a DB2, CICS, JCL, or IMS analyzer. EBCDIC cp037 decoding is supported when source keywords validate the decode, but broad code-page conversion remains outside the current compatibility matrix.
