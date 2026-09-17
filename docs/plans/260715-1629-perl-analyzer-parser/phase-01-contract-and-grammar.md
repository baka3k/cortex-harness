# Phase 01: Validate the Grammar and Freeze Contracts

## Context

The source specification requires Tree-sitter but does not identify a maintained Python grammar package, supported source extensions, grammar ABI, or concrete AST node names. Those facts must be proven before implementation to avoid building extractors against an incompatible or incomplete grammar.

## Requirements

- Select a Perl 5 Tree-sitter grammar with a Python loading path compatible with the repository's installed `tree-sitter` runtime and supported Python versions.
- Pin an accepted dependency range in `code-tiny/requirements.txt`; do not rely on an unversioned Git checkout at runtime.
- Prove parsing for packages, named subs, variable declarations, `use`/`require`/`no`, direct/qualified/method calls, comments, POD, attributes/prototypes, `__DATA__`/`__END__`, and malformed syntax.
- Freeze analyzer version, grammar/package version reporting, normalized IDs, source spans, diagnostics, coverage states, and JSON serialization rules.
- Confirm the primary ownership set before shared registry changes. Proposed MVP: `.pl`, `.pm`, `.t`.
- Keep graph/vector/provider concerns out of the analysis core.

## Architecture

Start with the smallest package that exposes a real stable boundary:

```text
code-tiny/tools/perl/
├── __init__.py
├── models.py
├── parser_runtime.py
├── perl_parser.py
├── resolver.py
├── pipeline.py
├── perl_analyzer.py
└── README.md
```

`parser_runtime.py` owns grammar loading/capability checks. `models.py` owns immutable normalized contracts and stable serialization. The parser and resolver must not import graph, vector, or credential modules.

## Related Files

- `code-tiny/requirements.txt`
- `code-tiny/tools/perl/__init__.py`
- `code-tiny/tools/perl/models.py`
- `code-tiny/tools/perl/parser_runtime.py`
- `tests/fixtures/perl-application/`
- `tests/test_perl_imports.py`
- `tests/test_perl_parser_runtime.py`
- `tests/test_perl_models.py`

## Implementation Steps

1. Build a grammar acceptance matrix: upstream maintenance, Python distribution, Tree-sitter ABI, license, install reproducibility, and fixture coverage.
2. Add a smoke fixture containing every promised Perl syntax family plus deliberately malformed/dynamic examples.
3. Record the accepted AST node/field mapping in parser constants and tests; reject unsupported grammar versions with a clear capability diagnostic.
4. Define immutable records for source spans, files, symbols, imports, references, parser capabilities, diagnostics, dependency indexes, and the analysis result.
5. Define checkout-independent stable IDs from project ID, normalized relative path, package/scope, symbol kind, and qualified name.
6. Define canonical JSON sorting, redaction, path normalization, coverage (`empty`, `partial`, `complete`), and exit-code policy.
7. Confirm `.pl`/`.pm`/`.t` ownership; document why `.pod` and extensionless scripts are deferred or update the decision with evidence.

## Todo

- [ ] Grammar/package selected and pinned.
- [ ] Grammar ABI and package versions appear in capabilities and cache fingerprints.
- [ ] AST node contract covers all MVP constructs.
- [ ] Stable IDs and serialization are deterministic.
- [ ] Source extension ownership is explicitly approved by tests.
- [ ] Core package imports without graph/vector services.

## Risks

- A grammar may parse common syntax while failing POD, prototypes, attributes, or modern Perl constructs.
- The Python package may expose a language capsule incompatible with the repository's Tree-sitter version.
- Treating grammar `ERROR` nodes as fatal for the whole project would reduce useful partial coverage.

## Success Criteria

- A clean install loads the grammar through one tested adapter and reports exact runtime/package/ABI versions.
- The representative syntax fixture parses with expected nodes and bounded errors.
- Repeated model serialization is byte-stable and contains no absolute checkout path.
- No extraction or integration phase begins until the mandatory grammar capability is proven.

