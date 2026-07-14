# Phase 02: Parse Structure, Data, and Copybooks

## Context

Control-flow and cross-program analysis depend on accurate program boundaries, paragraph order, data scopes, file declarations, and copybook-expanded symbol tables. This phase extracts those facts while preserving original source provenance.

## Requirements

- Extract identification, environment, data, and procedure divisions.
- Extract programs, sections, paragraphs, file assignments, data entries, level numbers, PIC clauses, and storage sections.
- Preserve original bytes, source ranges, source format, and dialect hints.
- Resolve `.cpy`/`.copy` files through configured search roots and deterministic extension order.
- Support nested copybooks, cycle detection, missing includes, and scoped symbol merging.
- Retain a canonical copybook node and original definition evidence after symbols are visible to a program.
- Handle `COPY REPLACING` only to the level proven by fixtures; otherwise emit a clear partial-resolution diagnostic.

## Architecture

`parser.py` converts AST nodes into source facts without cross-file mutation. `resolver.py` builds:

- project program index;
- per-program division/section/paragraph index in source order;
- data symbol tables for working-storage, local-storage, linkage, and file sections;
- copybook include graph and reverse-dependency index;
- file-control to file-description bindings.

Copybook expansion produces a resolved view, not duplicated canonical definitions. Each imported definition records both include-site evidence and original-copybook evidence.

## Related Files

Create:

- `code-tiny/tools/cobol/resolver.py`
- `tests/test_cobol_structure_extraction.py`
- `tests/test_cobol_data_extraction.py`
- `tests/test_cobol_copybook_resolution.py`
- `tests/test_cobol_file_binding.py`

Modify:

- `code-tiny/tools/cobol/models.py`
- `code-tiny/tools/cobol/parser.py`
- `code-tiny/tools/cobol/pipeline.py`
- `tests/fixtures/cobol-application/`

## Implementation Steps

1. Detect fixed/free format and dialect/compiler directives without discarding original offsets; keep uncertain detection as metadata.
2. Extract `PROGRAM-ID` and optional identification metadata, including nested/multiple program units where the grammar exposes them.
3. Extract environment file-control assignments and data-section entries with level, name, PIC, usage/value/redefines/occurs metadata when present.
4. Index procedure sections and paragraphs in exact source order, preserving empty paragraphs and section membership.
5. Implement copybook search configuration, include identity, nested expansion, cycle/depth guards, and reverse dependencies.
6. Merge copybook data definitions into consuming scopes while retaining canonical definition identity and include provenance.
7. Bind `SELECT`/assignment facts to file descriptions and emit unresolved/ambiguous diagnostics.
8. Add golden and negative tests for duplicate names, qualification, nested copybooks, cycles, missing includes, replacement, and malformed entries.

## Todo

- [ ] Define default copybook extension/search-order behavior and CLI overrides.
- [ ] Add fixed/free format and continuation fixtures.
- [ ] Verify imported definitions are not duplicated across consumers.
- [ ] Verify copybook cycles terminate and report the full include chain.
- [ ] Verify symbol IDs remain stable when unrelated files are added.

## Risks

- COBOL name qualification and repeated group names can make unqualified lookup ambiguous.
- `COPY REPLACING` can alter tokens before parsing and invalidate source offsets if implemented as naive text substitution.
- Mixed encodings and sequence/indicator areas can shift ranges unless normalization maintains an offset map.

## Success Criteria

- The fixture reconstructs each program, division, section, paragraph, data item, copybook, and file binding with stable IDs and evidence.
- Working-storage, local-storage, linkage, and file-section symbols remain separately scoped.
- Nested copybooks resolve transitively, cycles/missing includes are diagnostic, and original definition provenance is retained.
- Paragraph ordering is deterministic and ready for CFG construction.
- Partial/error-node parses never generate high-confidence definitions without evidence.
