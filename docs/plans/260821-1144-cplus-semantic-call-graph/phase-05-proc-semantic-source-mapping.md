# Phase 05: Pro*C source bundle, semantic bridge, and original-source mapping

## Context

The Pro*C analyzer already preserves embedded SQL facts and masks `EXEC SQL`
regions for Tree-sitter. The current Clang fallback explicitly excludes
`.pc`/`.pcc`, and parsing raw Pro*C with Clang would be invalid. Accepted C call
evidence therefore needs either reproducible precompiler output with a source
map or a rigorously validated virtual/masked input that preserves semantic and
source coordinates.

This phase consumes the stabilized Pro*C extraction plan. It must not replace
the owning SQL lexer/parser, five graph labels, nine relationship types, or
diagnostics. It completes the Pro*C lane across source identity, compiler and
precompiler context, supplied generated artifacts, source mapping, semantic
calls, enclosing functions, host/indicator variables, cursor/dynamic SQL
coverage, cache identity, payload validation inputs, and migration-impact
evidence. The exhaustive inventory is in the
[Pro*C component map](pro-c-component-map.md).

## Requirements

- Preserve original `.pc`/`.pcc` as authority for SQL/data-flow facts.
- Preserve length/newline masking for Tree-sitter structure and weak callsites.
- Define a versioned original-to-generated source-map contract.
- Prefer reproducible Oracle precompiler `.c`/`.cpp` output when available.
- Validate host-variable/generated-wrapper effects so generated implementation
  details do not appear as original business calls.
- Publish semantic C calls only when both semantic evidence and source mapping
  pass; otherwise retain weak C evidence and full SQL facts.
- Include precompiler/options/mapping versions in context and cache identity.
- Keep repository command execution disabled; precompiler execution requires an
  explicit safe provisioning workflow outside default report/repair.
- Preserve `.pc`/`.pcc` discovery, `proc`/`pro*c`/`pro-c` routing, C-versus-C++
  mode selection, original encoding/hash, and rename/delete ownership.
- Preserve all five SQL labels (`SqlStatement`, `SqlDirective`, `SqlCursor`,
  `SqlHostVariable`, `DatabaseTable`) and all nine existing SQL relationships.
- Define one immutable `ProcSourceBundle` joining original, masked, generated,
  map, compiler, redacted precompiler, dependency, worker, schema, and policy
  fingerprints.
- Consume only supplied, hash-bound generated artifacts under an allowlisted
  root; never submit raw Pro*C to Clang or persist raw precompiler commands.
- Classify generated application, macro, wrapper, runtime, declaration, and
  unmapped observations before source reconciliation.
- Reconcile original SQL regions and mapped semantic functions without choosing
  an enclosing function by name/proximity when mappings disagree.
- Resolve host and indicator variables to C semantic declarations only when
  unique; preserve ambiguous and unresolved evidence.
- Propagate cursor, dynamic SQL, unresolved table/include, and source-map gaps
  into migration/data-impact completeness.
- Keep original SQL, Tree-sitter structure, semantic C calls, graph, and vector
  sub-results independently accounted so one failed lane cannot erase another.

## Architecture

Use three coordinated inputs:

```text
original .pc/.pcc  -> Pro*C SQL facts and original spans
masked source      -> Tree-sitter structure and lexical callsites
generated/virtual C-> Clang semantic observations
                         |
                         v
              versioned source-map reconciliation
```

The source map records original/generated file identities, spans/lines,
mapping method, precompiler/tool version, configuration fingerprint,
completeness, diagnostics, and generated-only classifications. A semantic
callsite cannot enter the strict view unless it maps to an accepted original C
span or is explicitly labeled generated-only and excluded from source impact.

### Source bundle and lifecycle

Each `.pc`/`.pcc` plus configuration produces one immutable bundle:

```text
ProcSourceBundle
  original: relative path, content hash, encoding, lossy flag
  masked: content hash, mask policy/version, alignment result
  generated: relative artifact path/hash/language (optional)
  source_map: ID/hash/provider/quality/policy (optional)
  context: compiler + redacted precompiler + dependencies + mode
  analyzer: Clang/worker/schema/policy versions
  state: sql_only | lexical_ready | semantic_eligible | semantic_complete
         | partial | stale | invalid | failed
```

Generated paths and hashes are provenance. Original relative path/span remains
the user-visible callsite identity. A changed original, masked form, generated
artifact, map, context, dependency, toolchain, schema, or policy creates a new
bundle fingerprint and invalidates the affected semantic evidence.

### Generated-artifact intake

The normal analyzer accepts a declarative manifest produced by an approved
external build/precompile workflow. Each entry binds one original source hash
and configuration to one generated C/C++ hash, map evidence, precompiler/tool
fingerprint, and C/C++ compiler context. Artifact paths must resolve below an
allowlisted root. Raw commands, environment values, credentials, connection
strings, response-file contents, and external absolute paths are rejected or
redacted before persistence and before worker admission.

### Mapping and generated-code classes

Required map quality states are `exact_span`, `exact_line`, `line_directive`,
`inferred`, `missing`, `stale`, and `invalid`. The default strict rule accepts
only `exact_span`; `exact_line` may be promoted only after the reviewed corpus
proves deterministic column reconstruction and policy records that decision.
Weaker qualities remain conservative evidence.

Every generated Clang observation is also classified as
`original_application`, `macro_expansion`, `precompiler_wrapper`,
`precompiler_runtime`, `generated_declaration`, or `unmapped_generated`.
Wrapper/runtime/unmapped classes are never projected as original application
`CALLS`, even when they resolve semantically in generated C.

### SQL, function, and variable reconciliation

Original SQL node IDs and the nine relation semantics stay unchanged. The
enclosing function join is checked across original Tree-sitter containment,
source-map evidence, and Clang function identity. Disagreement produces an
ambiguous weak join and partial coverage.

`BINDS_PARAMETER` remains the relation from `SqlStatement` to
`SqlHostVariable`. A new schema-owner-approved evidence relation may connect a
host/indicator node to a unique Clang parameter, local, field, or global. It
must carry bundle/configuration/map provenance; name-only matches remain
unresolved candidates. Cursor lifecycle and dynamic SQL remain original-source
facts, and incomplete table/host/cursor resolution propagates to data-impact
queries without changing direct C call accuracy.

### Independent failure behavior

| Pro*C lane result | SQL facts | Tree-sitter structure | Strict semantic calls |
| --- | --- | --- | --- |
| Generated artifact/map absent | Preserve | Preserve | Reject |
| SQL grammar parser unavailable | Preserve lexical facts with diagnostic | Preserve | Independently eligible if original alignment/map pass |
| Lossy decode or mask mismatch | Preserve only safely located facts | Quarantine affected locations | Reject affected bundle |
| Map inferred/stale/invalid | Preserve | Preserve weak calls | Reject original-source strict calls and remove stale evidence in staging |
| Clang timeout/crash/OOM | Preserve | Preserve weak calls | Reject request output |
| Generated wrapper/runtime | Preserve | Not an original call | Exclude from original-source strict view |

## Related files

- `code-tiny/tools/cplus/proc_analyzer.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/cplus/bootstrap_compile_commands.py`
- `code-tiny/tools/cplus/clang_worker.py`
- `code-tiny/tools/cplus/clang_parser.py`
- `code-tiny/tools/cplus/parse_recovery.py`
- `code-tiny/tools/common/analyzer_cache.py`
- `code-tiny/tools/common/parse_quality.py`
- `code-tiny/tools/common/payload_validation.py`
- `code-tiny/tools/graph/schema/manifest.py`
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/tool_metadata.py`
- `code-tiny/mcp/cplus/services/graph_service.py`
- `code-tiny/mcp/cplus/services/impact_service.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/sync/owner_manifest.py`
- `cortex_harness/dev.py`
- call-evidence/context/cache contracts from Phases 01-03
- Pro*C SQL graph schema owned by `260804-1640-port-proc-cplus-to-code-tiny`
- `tests/test_cplus_proc_sql.py`
- `tests/test_cplus_graph_runtime.py`
- `tests/test_legacy_migration_e2e.py`
- new Pro*C source-map fixtures and tests
- [Pro*C component map](pro-c-component-map.md)

## Implementation steps

1. Freeze the owner plan's Pro*C discovery, aliases, C/C++ mode, decoding,
   lexical region, masking, SQL facts, five labels, nine relationships,
   diagnostics, node/relation identities, payload, graph, vector, and source
   span contracts as reviewed inputs.
2. Add regression fixtures for comments, escaped and raw literals, multiline
   SQL, embedded PL/SQL, semicolons in literals/comments, unterminated blocks,
   UTF-8/CP932/lossy content, and normalized byte/line alignment. Any scanner
   correction remains coordinated with the Pro*C owner.
3. Define and version `ProcSourceBundle`, `ProcArtifactManifest`,
   `ProcSourceMap`, map entry/quality, generated-code class, bundle state,
   diagnostic categories, and deterministic identity/fingerprint rules.
4. Inventory supplied precompiler output, line directives, sidecar maps,
   metadata, include/macro inputs, C/C++ mode, and tool/options fingerprints
   across representative repositories. Record coverage and gaps without
   invoking the precompiler.
5. Implement bounded manifest ingestion: canonicalize original/generated paths,
   enforce roots and size/count limits, verify hashes and configuration, redact
   options, reject secret-bearing or executable inputs, and return typed
   eligibility reasons.
6. Implement source-map providers for available explicit map/manifest and line
   directive evidence behind one provider-neutral contract. Preserve exact,
   line-only, inferred, missing, stale, conflicting, and invalid outcomes.
7. Evaluate virtual length-preserving masked input only as a fallback
   experiment. Reject it for strict publication unless semantic identity,
   diagnostics, and original location parity pass the full reviewed corpus for
   the applicable Pro*C dialect/toolchain cohort.
8. Extend the isolated semantic worker request with source-bundle/map IDs and
   generated input. Raw `.pc`/`.pcc`, unsafe paths, raw commands, credential
   options, and unverified artifact hashes must fail before worker execution.
9. Classify generated Clang functions and calls as original application, macro,
   precompiler wrapper/runtime, generated declaration, or unmapped. Use explicit
   evidence and mapping regions; do not rely only on helper name deny-lists.
10. Reconcile generated semantic functions/calls to original file/spans and
    stable original callsite identity. Keep generated spans/artifact hashes as
    provenance and emit ambiguity when maps/configurations conflict.
11. Reconcile original SQL regions to semantic enclosing functions. Resolve host
    and indicator variables to unique semantic C declarations through an
    additive evidence contract; retain ambiguous/unresolved joins and cursor or
    dynamic SQL incompleteness.
12. Extend context/cache/incremental contracts with original, masked, generated,
    map, compiler, redacted precompiler, include/dependency, worker, schema, and
    policy fingerprints. Prove exact invalidation and downgrade cleanup.
13. Prepare concrete-label payload records and endpoint evidence for all five
    labels and nine relations plus source bundles/maps/semantic joins. Phase 06
    performs canonical schema/journal publication; Phase 05 must produce fully
    validated, deterministic staging artifacts.
14. Preserve graph/vector separation: only approved original SQL text/summary
    is eligible for embeddings; generated code, commands, credentials, wrappers,
    and runtime helper bodies are excluded.
15. Extend MCP/impact fixtures for caller→function→SQL→table, cursor lifecycle,
    host/indicator bindings, dynamic SQL, missing maps, and coverage-aware
    negative conclusions under `cplus` and every Pro*C alias.
16. Produce a Pro*C component report with discovery/routing, decode/mask, SQL
    regression, artifact/context census, map-quality distribution, accepted and
    rejected calls, generated filtering, joins, cache invalidation, security,
    graph/vector staging, and query results before Phase 06.

## Todo

- [x] Freeze discovery, decode, mask, SQL, five-label, nine-relation, payload,
  graph, vector, routing, and incremental owner contracts.
- [x] Complete the adversarial Pro*C lexical/alignment regression corpus.
- [x] Define and version source bundle, artifact manifest, source map, mapping
  quality, generated class, lifecycle state, diagnostics, and identities.
- [x] Inventory generated outputs, maps, contexts, modes, and precompiler
  evidence without executing build/precompile commands.
- [x] Ingest supplied generated C/C++ and mapping evidence safely.
- [x] Extend the isolated worker with bundle-bound generated input.
- [x] Reconcile semantic functions/calls to original spans and classify all
  generated-only observations.
- [x] Reconcile SQL enclosing functions, host/indicator declarations, cursor,
  dynamic SQL, and data-impact completeness.
- [x] Add full bundle/cache/dependency/incremental invalidation.
- [x] Prepare concrete-label validated graph/vector staging artifacts.
- [x] Preserve every SQL golden fact across semantic failure modes.
- [x] Publish the complete Pro*C component, mapping, and regression report
  ([phase-05-report.md](phase-05-report.md)).

## Risks

- Oracle precompiler versions/options can substantially change generated code.
- Line directives may not provide byte-accurate macro/callsite mapping.
- Generated runtime calls can pollute migration impact results.
- Legacy encodings can break length or byte assumptions.
- The current lexical scanner and raw-string/embedded SQL edge cases may have
  gaps that become visible only when exact source mapping is enforced.
- Generated output may not be retained by legacy builds, or may differ by
  precompiler options, environment, database mode, and platform.
- Generated runtime calls may resemble application helper names, making a pure
  deny-list unsafe.
- Host variables can be shadowed, macro-expanded, struct-qualified, or supplied
  through generated declarations.
- Current payload validation's generic Pro*C fallback can hide concrete label
  and endpoint mistakes.
- Precompiler configurations may contain database credentials or external paths.

Mitigate with explicit mapping quality, generated-only classes, original-source
gold assertions, encoding-aware spans, versioned fingerprints, structural plus
provenance-based generated classification, semantic declaration identities,
concrete-label validation, option redaction, artifact containment, and
fail-closed strong publication.

## Success criteria

- Every reviewed accepted semantic Pro*C call maps to the expected original
  file/span; exact reviewed mapping has 100% pass rate.
- Missing, inferred, stale, or invalid maps cannot produce strict source calls.
- Generated runtime wrappers remain distinguishable and do not masquerade as
  original business callsites.
- Existing embedded SQL nodes and relationships have zero golden regression.
- Mapping/precompiler changes invalidate exactly the affected semantic cache.
- `.pc`/`.pcc` discovery, aliases, C/C++ mode, decoding, mask alignment,
  diagnostics, rename/delete, and incremental ownership pass reviewed tests.
- All five SQL labels and nine relations retain original IDs, endpoint
  semantics, graph/provider parity, and vector cleanup behavior.
- Every accepted Pro*C semantic observation belongs to one immutable source
  bundle and configuration with redacted compiler/precompiler provenance.
- Raw Pro*C, unverified/external generated artifacts, secret-bearing options,
  and unsafe maps never reach the worker or persisted evidence.
- Enclosing functions and unique host/indicator declarations reconcile by
  semantic/map evidence; ambiguous joins remain visible and weak.
- Dynamic SQL, unresolved tables/cursors/hosts, and partial maps prevent unsafe
  negative migration/data-impact conclusions.
- SQL-only, lexical-only, semantic-complete, partial, stale, invalid, and failed
  bundle states have deterministic graph/vector/publication behavior ready for
  Phase 06.
