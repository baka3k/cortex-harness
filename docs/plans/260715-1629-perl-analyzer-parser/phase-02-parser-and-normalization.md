# Phase 02: Implement Deterministic Perl Extraction

## Context

This phase implements the source specification's syntax-level responsibilities. Extraction remains AST-based and side-effect free; regex may normalize already extracted text but must not silently replace the mandatory Tree-sitter parser.

## Requirements

- Deterministically scan supported sources while pruning ignored/generated/cache directories.
- Enforce root containment, symlink policy, encoding handling, and per-file/total input budgets before parsing.
- Preserve package boundaries when one file declares multiple packages.
- Extract named subroutines, declaration ranges, package/scope, parameters/prototypes/attributes when the grammar exposes them, and leading documentation.
- Extract `my`, `our`, and `local` declarations with lexical/package/dynamic-local scope and declaration source range.
- Normalize `use`, `require`, and `no` while preserving raw text and dynamic/conditional status.
- Extract direct, qualified, indirect, and `->` call references without inventing target identities.
- Optionally extract comments and inline POD under explicit flags and budgets.
- Continue past malformed files with bounded diagnostics and accurate partial coverage.

## Architecture

```text
scanner -> source bytes -> Tree-sitter tree -> scope-aware AST visitor
        -> normalized file/symbol/import/reference records
        -> canonical sorted AnalysisResult JSON
```

The visitor maintains file, package, subroutine, and lexical block scopes using AST ranges. Source positions are normalized to the repository's one-based public span convention. Raw parser byte offsets remain internal.

## Related Files

- `code-tiny/tools/perl/models.py`
- `code-tiny/tools/perl/parser_runtime.py`
- `code-tiny/tools/perl/perl_parser.py`
- `code-tiny/tools/perl/pipeline.py`
- `tests/fixtures/perl-application/`
- `tests/test_perl_scan.py`
- `tests/test_perl_parser.py`
- `tests/test_perl_golden_output.py`
- `tests/test_perl_error_recovery.py`

## Implementation Steps

1. Implement sorted source discovery for the approved extensions with separate ignored-directory and ignored-file rules.
2. Add safe byte loading, encoding policy, size budgets, root containment, and parser-error counting.
3. Implement a package-boundary visitor and generate qualified names from active package and lexical scope.
4. Extract named subroutines and variable declarations with stable IDs and exact evidence spans.
5. Extract import/dependency statements, including version-only `use`, dynamic `require`, conditional forms, and `no` pragmas without over-normalizing.
6. Extract call/reference shapes: bare calls, `Package::sub`, `$coderef->()`, `$object->method`, `SUPER::method`, and dynamic symbolic forms.
7. Extract common OO evidence such as `bless` and constructor-shaped `new` calls as reference metadata only; do not infer a complete class/type system.
8. Add optional bounded comment/POD extraction and diagnostics for unsupported or truncated documentation regions.
9. Sort/deduplicate all records and write golden normalized JSON assertions for the fixture.

## Todo

- [ ] Scanner ordering and exclusions are deterministic.
- [ ] Package/subroutine/variable/import/reference extractors have focused tests.
- [ ] Multi-package and nested-scope files preserve correct ownership.
- [ ] Dynamic constructs remain explicit and unresolved.
- [ ] POD/comments are optional and budgeted.
- [ ] Malformed files do not abort unrelated files.
- [ ] Golden output is checkout-independent.

## Risks

- Perl permits declarations and calls in syntactically ambiguous contexts.
- One source file may switch package context repeatedly.
- POD and `__DATA__` regions can be represented differently across grammars.
- Alternate encodings may prevent exact text recovery even when bytes parse.

## Success Criteria

- The fixture produces the exact promised normalized records and source spans.
- Identical inputs produce identical JSON regardless of discovery order or checkout root.
- Parse errors, unsupported constructs, and budget truncation yield stable diagnostics and `partial` coverage where appropriate.
- No test requires Perl execution, network access, graph services, or credentials.

