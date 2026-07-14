# COBOL analyzer specification

## Contract

`code-tiny/tools/cobol/cobol_analyzer.py` is the only executable entry point. Imports and `--preflight` perform no graph or vector mutations. A normal run stages deterministic schema-version-1 facts in memory, serializes the artifact, writes namespaced nodes and typed relationships through `LanguageCodeWriter`, and only then applies file-scoped tombstones and advances the dependency cache.

The parser preserves original source bytes and reports one-based line/column plus byte ranges. Fixed-format sequence and indicator areas are removed only from the extraction view; evidence offsets continue to refer to the original source.

## Resolution

- Programs, procedure sections, paragraphs, storage sections, and data items are indexed before cross-reference resolution.
- Copybooks resolve by canonical file identity with nested-include closure and cycle detection. Imported definitions keep their copybook file evidence and are referenced from the consuming program rather than duplicated.
- Literal `CALL` targets resolve against the project program index. Identifier calls remain `COBOL_DYNAMIC_CALL` diagnostics.
- `PERFORM THRU` emits the range entry and a separate `RETURNS` continuation. `GO TO` remains non-returning; `DEPENDING ON` candidates are dynamic and lower-confidence.
- `ALTER` records uncertainty without replacing static history. Natural fall-through is emitted only when no terminal transfer ends the paragraph.
- Unqualified data references with multiple candidates are diagnostic and are not guessed.

## Incremental safety

The dependency index maps each program/copybook to included copybooks. Changed and deleted seeds are expanded through reverse dependencies. The old index participates in deletion invalidation, so deleting a copybook still reparses former consumers. Fatal runtime, parse-stage, or write failures leave previous graph facts and the last dependency index intact.

## Unified MCP

`cobol`, `cobol85`, `ibm-cobol`, and `gnucobol` route through the general code backend. The query profile adds COBOL labels, searchable properties, and control/data relationships only when a COBOL alias is selected. General parser defaults are unchanged.

## Validation baseline

The deterministic fixture contains multiple programs, nested and cyclic copybooks, all four storage/file areas, static and dynamic calls, `PERFORM THRU`, dynamic `GO TO`, `ALTER`, file I/O, SQL, CICS, and malformed source. The local regression threshold is five seconds for the fixture; this is a guard against accidental algorithmic regressions, not a production throughput claim. Live Neo4j/FalkorDB parity requires configured services and remains an environment-gated validation step; fake-driver logical parity runs in the normal unit suite.
