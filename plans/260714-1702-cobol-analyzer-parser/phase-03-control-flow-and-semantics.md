# Phase 03: Build Resolution, Control Flow, and Semantic Facts

## Context

The main value of the analyzer is reconstruction of COBOL behavior rather than syntax inventory. This phase resolves procedure references, builds paragraph-level control flow, and emits program, data, file, SQL, CICS, and cross-program facts without writing them to a live graph.

## Requirements

- Resolve paragraph/section targets for `PERFORM`, `GO TO`, and `ALTER`.
- Distinguish `PERFORM` call/return behavior from non-returning `GO TO` flow.
- Model `PERFORM THRU`, fall-through, loops, conditionals, dynamic targets, and terminal statements.
- Resolve static program calls and retain dynamic calls explicitly.
- Resolve variable references and basic read/write intent from procedure statements.
- Extract file I/O and preserve embedded SQL/CICS operations as semantic nodes.
- Emit deterministic, project-scoped, namespaced nodes and uppercase typed relationships.
- Carry confidence and uncertainty for every inferred or dynamic relationship.

## Architecture

`cfg.py` operates only after paragraph/section indexes are complete. Each paragraph has an ordered statement sequence and explicit successor set. The builder emits source-backed edges for branches and separately derives natural fall-through/return edges.

`semantics.py` maps resolved facts to the graph contract:

- canonical `File` plus COBOL semantic labels;
- structure via `DEFINES` and `INCLUDES`;
- symbol use via `REFERENCES`;
- external calls via `CALLS`;
- control via `PERFORMS`, `PERFORMS_THRU`, `RETURNS`, `GOES_TO`, `GOES_TO_DYNAMIC`, `FALLS_THROUGH`, `ALTERS`, `CONDITIONAL`, and `EXITS`;
- resources via `READS` and `WRITES`.

Unresolved references are diagnostics or explicit unresolved facts. They must not create fake destination nodes that appear resolved in MCP results.

## Related Files

Create:

- `code-tiny/tools/cobol/cfg.py`
- `code-tiny/tools/cobol/semantics.py`
- `tests/test_cobol_symbol_resolution.py`
- `tests/test_cobol_cfg.py`
- `tests/test_cobol_call_resolution.py`
- `tests/test_cobol_semantic_facts.py`
- `tests/test_cobol_embedded_sql_cics.py`

Modify:

- `code-tiny/tools/cobol/models.py`
- `code-tiny/tools/cobol/parser.py`
- `code-tiny/tools/cobol/resolver.py`
- `code-tiny/tools/cobol/pipeline.py`
- `tests/fixtures/cobol-application/`

## Implementation Steps

1. Convert procedure AST nodes into ordered statement facts with paragraph ownership and branch/source evidence.
2. Resolve static `PERFORM` targets and `PERFORM THRU` inclusive paragraph ranges; emit continuation/return edges to the caller successor.
3. Add fall-through only for non-terminal paths and represent `EXIT`, `STOP RUN`, and `GOBACK` explicitly.
4. Resolve static and `DEPENDING ON` `GO TO` targets; tag dynamic candidate edges with selector evidence.
5. Represent `ALTER` as a dynamic modification edge and mark affected CFG regions as uncertain rather than rewriting history.
6. Add IF/EVALUATE conditional successors and loop metadata for `PERFORM UNTIL`/`VARYING` where grammar evidence is sufficient.
7. Build a project program index for literal calls; resolve provable constants and retain other identifier calls as dynamic/unresolved.
8. Resolve qualified/unqualified data references with read/write/access metadata and ambiguity diagnostics.
9. Bind `OPEN`, `CLOSE`, `READ`, and `WRITE` operations to resolved COBOL file facts.
10. Create SQL/CICS statement nodes with operation text, host-variable/resource references, and conservative basic targets.
11. Validate exact nodes/edges against hand-authored golden graphs and invariant tests.

## Todo

- [ ] Approve the paragraph-level CFG invariant set.
- [ ] Add golden cases for nested performs, backward branches, unreachable paragraphs, and perform ranges crossing sections.
- [ ] Verify dynamic calls and branches never appear as high-confidence resolved edges.
- [ ] Verify every semantic edge has source evidence and project scope.
- [ ] Verify two identical analyses yield the same graph fact ordering and IDs.

## Risks

- `PERFORM THRU` and fall-through can create misleading cycles if return continuation is modeled like a normal branch.
- `ALTER` changes runtime control flow and cannot be made fully static.
- Data-flow intent for COBOL verbs varies by operand position and dialect.
- Embedded SQL/CICS grammar nodes may preserve syntax without exposing every domain-specific component.

## Success Criteria

- Golden CFGs distinguish perform/return, go-to, dynamic go-to, fall-through, conditional, alter, and exit behavior.
- Static calls resolve across programs; dynamic calls remain explicit and uncertain.
- Variable and file access facts resolve where unambiguous and diagnose ambiguity otherwise.
- SQL/CICS facts preserve source and basic operation/target/host-variable metadata without claiming full DB2/CICS analysis.
- Semantic facts match the namespaced node and typed-edge contract and contain no fabricated resolved targets.
