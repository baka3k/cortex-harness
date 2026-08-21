# Phase 01: Semantic edge contract, gold corpus, and containment

## Context

The current C/C++ path collects Tree-sitter `call_expression` nodes and later
resolves names using scope, file, include, macro, and arity scoring. The graph
can therefore publish `CALLS` without semantic callee identity. Existing
quality provenance declares a strong-edge policy but the call writer does not
enforce it. The first phase must make the graph honest before adding Clang
coverage.

## Requirements

- Define one versioned callsite/evidence schema and edge taxonomy.
- Preserve existing source/control metadata and stable site identity where
  compatible; do not include `parse_run_id` in merge identity.
- Enforce that lexical and heuristic evidence cannot become `CALLS`.
- Keep weak evidence queryable as possible, indirect, or unknown.
- Add semantic coverage and completeness models before consumers can request
  strict negative impact answers.
- Build a reviewed call corpus that exercises semantic ambiguity rather than
  only parser damage.
- Keep all changes provider-neutral and compatible with the common payload and
  graph schema owners.
- Define a first-class `ProcSourceBundle` identity joining original `.pc`/`.pcc`,
  masked bytes, optional generated C/C++, source-map evidence, compiler and
  redacted precompiler context without making generated paths user identities.
- Preserve the five Pro*C SQL labels and nine relationships while making
  concrete-label payload validation and SQL-versus-C evidence independence
  explicit.

## Architecture

Introduce a call-evidence record between parser extraction and graph writing.
Tree-sitter produces `lexical_candidate` records with no semantic callee ID.
The graph writer accepts `CALLS` only from evidence that declares an approved
semantic provider, `direct_resolved`, caller/callee semantic identities, and a
complete TU/configuration fingerprint.

Coverage is a first-class record keyed by project, revision, language,
translation unit/configuration, analyzer/policy version, and status. Consumer
code must distinguish `complete`, `partial`, `ineligible`, `failed`, and
`not_analyzed`.

## Related files

- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/cplus/proc_analyzer.py`
- `code-tiny/tools/cplus/clang_parser.py`
- `code-tiny/tools/common/payload_validation.py`
- `code-tiny/tools/common/parse_quality.py`
- `code-tiny/tools/graph/schema/`
- `code-tiny/tools/graph/writer/language_writer.py`
- `code-tiny/mcp/cplus/cplus_mcp.py`
- `code-tiny/tools/common/workflow_impact_scorer.py`
- `tests/test_cplus_graph_runtime.py`
- new fixtures under `tests/fixtures/cplus_semantic_calls/`
- [Pro*C component map](pro-c-component-map.md)

## Implementation steps

1. Inventory every producer and consumer of `CALLS`, `POSSIBLE_CALLS`,
   `CALLS_FUNCTION_POINTER`, and `UNKNOWN_CALL`; classify write, traversal,
   scoring, explanation, and cleanup behavior.
2. Finalize the versioned `CallSiteEvidence`, `SemanticCoverage`, resolution
   class, provenance, and stable identity contracts in one canonical module.
3. Specify linkage/configuration-aware semantic symbol identity and stable
   callsite identity, including macro and original/generated source spans.
4. Adapt Tree-sitter output to `lexical_candidate` without semantic promotion.
5. Enforce call promotion in validation and again in the graph writer so direct
   callers cannot bypass the invariant.
6. Make unsupported legacy cache/payload versions fail closed or migrate
   explicitly; bump schema/cache fingerprints.
7. Add compatibility query fields and coverage states without yet enabling
   semantic publication.
8. Build a reviewed corpus for overloads, methods, ADL, templates, macros,
   virtual calls, function pointers, callbacks, internal linkage, configuration
   variants, missing context, and Pro*C.
9. Capture current precision/recall, edge-class counts, query behavior, and
   false positive/negative examples before changing publication.
10. Freeze the Pro*C original-source, decode, mask, SQL-region, five-label,
    nine-relationship, diagnostic, and cache payload contracts as versioned
    inputs. Define `ProcSourceBundle`, map quality, generated-code class, and
    original callsite identity before any Clang adapter is changed.
11. Add `.pc`/`.pcc` containment cases proving masked Tree-sitter calls remain
    `lexical_candidate`, legacy caches cannot create strong calls, and SQL facts
    survive the call-edge downgrade unchanged.
12. Replace the generic `ProcStatement` validation fallback with an agreed
    label-aware contract for `SqlStatement`, `SqlDirective`, `SqlCursor`,
    `SqlHostVariable`, and `DatabaseTable`; implementation may land with the
    guarded writer in Phase 06, but the schema is frozen here.

## Todo

- [x] Inventory call-edge producers and consumers.
- [x] Finalize callsite/evidence/coverage schema and identity rules.
  (canonical module: `code-tiny/tools/common/call_evidence.py`)
- [x] Add reviewed semantic call corpus and baseline artifact.
  (`tests/fixtures/cplus_semantic_calls/` with `expected.json` + `baseline.json`)
- [x] Downgrade Tree-sitter/name heuristic output to weak evidence.
  (resolved heuristic candidates now publish `POSSIBLE_CALLS` site edges with
  `resolution_class=lexical_candidate`; unresolved stay `UNKNOWN_CALL` with
  `resolution_class=unresolved`)
- [x] Enforce semantic-only `CALLS` at validation and writer boundaries.
  (`payload_validation.validate_cplus_payload` migrates/demotes/quarantines call
  rows; `LanguageCodeWriter.write_calls_with_site` re-enforces via
  `enforce_strong_call_row`)
- [x] Add cache/schema compatibility behavior and focused tests.
  (`PAYLOAD_SCHEMA_VERSION` 1.0→1.1; cplus `_PARSE_CACHE_VERSION` bumped so
  legacy cached call payloads fail closed; `tests/test_cplus_call_evidence.py`)
- [x] Prove current queries expose rather than hide incomplete coverage.
  (Phase 01 scope: `SemanticCoverageRecord`/`coverage_is_complete` contract
  added; MCP query integration lands with Phase 04 views)
- [x] Freeze `ProcSourceBundle`, map-quality, generated-class, and original
  callsite identity contracts. (in `call_evidence.py`)
- [x] Baseline all five Pro*C labels, nine relations, diagnostics, and masked
  Tree-sitter call classes. (corpus `proc.pc` + `baseline.json`; existing
  `test_cplus_proc_sql.py` green)
- [x] Specify label-aware Pro*C validation and failure isolation.
  (`proc_nodes` rows must carry one of the five concrete labels; unrecognized
  labels quarantine as `INVALID_RECORD` instead of collapsing to
  `ProcStatement`)

## Risks

- Downgrading calls without staged replacement can leave stale strong edges.
- Existing consumers may interpret fewer strict edges as a parser regression.
- A schema that stores only flattened caller/callee edges will lose the
  configuration and callsite evidence needed by later phases.

Mitigate with a schema-versioned staging fixture, before/after cardinality
reports, explicit compatibility responses, and last-generation rollback. No
active user graph is modified in this phase.

## Success criteria

- Zero Tree-sitter-only or heuristic evidence can pass validation as `CALLS`.
- Every accepted strong call carries the required evidence fields, even though
  semantic publication remains disabled until Phase 02.
- The corpus has reviewed expected classes and targets for every required
  ambiguity cohort.
- Cache and graph schemas reject or explicitly migrate legacy call evidence.
- Existing Pro*C SQL golden facts remain unchanged.
- Every `.pc`/`.pcc` call observation declares original/masked/generated source
  provenance; absent generated/map evidence cannot become strict `CALLS`.
- The Pro*C corpus has reviewed expectations for SQL facts, masking, calls
  around SQL regions, source identity, and generated-only classifications.
