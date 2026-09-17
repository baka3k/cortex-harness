# Code Sync Graph/Embedding Phase Prediction

Date: 2026-08-13  
Depth: quick  
Verdict: CAUTION

## Executive summary

Splitting code sync into graph and embedding phases fits the existing analyzer contract because primary analyzers already persist graph facts before writing vectors. The safe implementation is an orchestrator-level two-pass run: graph-capable analyzers first, framework overlays and project topology second, and graph-disabled vector passes last. The main risks are doubled parsing cost, partial graph/vector generations, and ambiguous incremental baselines; specialized graph-only and embedding-only modes therefore require `--full-scan` in this iteration.

## Agreements

- Preserve `both` as the default so existing `dev sync code` behavior remains compatible.
- Drain every graph journal before topology starts.
- Run topology after primary and framework graph facts exist so `EXPOSES_API` and `EXPOSES_ENDPOINT` links are retained.
- Disable vectors in the graph pass by removing Qdrant settings, and disable graph writes in the vector pass through the canonical `CORTEX_DISABLE_GRAPH` boundary.
- Report graph and vector stages separately; do not present skipped vectors as successful embeddings.

## Conflicts

| Topic | Architect | Security | Performance | UX | Devil's advocate | Resolution |
|---|---|---|---|---|---|---|
| Re-run analyzers for vectors | Accepts clean phase boundary | No material concern | Warns about doubled parse time | Prefers predictable progress | Challenges cache reuse across all analyzers | Use existing analyzer caches for the second pass and expose separate phase timing; avoid a cross-analyzer payload refactor in this change. |
| Partial success | Wants explicit generation state | Requires no cross-project leakage | Prefers resumable vectors | Wants actionable status | Notes a failed vector pass can trigger unnecessary graph work | Keep the run non-zero on selected-stage failure, preserve graph facts already committed, and support an explicit full embedding-only retry. |
| Incremental specialized modes | Requires independent baselines | No material concern | Favors incremental support | Wants simple commands | Calls a shared baseline unsafe | Restrict `graph` and `embedding` modes to `--full-scan`; leave independent incremental graph/vector baselines for a later design. |

## Risk summary

| Risk | Severity | Persona | Mitigation |
|---|---|---|---|
| Graph and vector stores represent different source snapshots | High | Architect | Hold one source inventory lock, verify unchanged sources after all selected stages, and expose phase status in the summary. |
| Primary parsing runs twice in `both` mode | High | Performance | Reuse analyzer caches on the vector pass, keep message/vector work out of the graph pass, and record durations. |
| Topology runs too early and misses links to symbols/endpoints | High | Architect | Place topology after all graph-producing primary/framework analyzers, before vector-only passes. |
| Embedding-only accidentally mutates FalkorDB | Medium | Security | Set `CORTEX_DISABLE_GRAPH=1`, skip graph setup/journals/overlays/topology, and test child environments. |
| Graph-only accidentally opens Qdrant local storage | Medium | Performance | Remove local/remote Qdrant environment keys and omit collection arguments in the graph phase. |
| CLI mode is misunderstood | Medium | UX | Use one choice option: `--sync-mode both|graph|embedding`; show validation error unless specialized modes use `--full-scan`. |

## Persona details

### Architect

- Concerns: topology owns links to facts produced by other graph writers; one global baseline cannot safely represent independent incremental graph/vector completion.
- Recommendations: split execution stages in the orchestrator, preserve journal gates, constrain specialized modes to full scan.
- Confidence: high.

### Security

- Threats: an embedding-only command could open or mutate the wrong graph if graph-provider arguments leak into child analyzers; Qdrant payload scoping must remain unchanged.
- Severity: medium.
- Mitigations: canonical graph-disable environment, existing project-scoped collection names, no new credential surfaces.

### Performance

- Bottlenecks: two parser subprocess runs per primary language when both graph and Qdrant are enabled.
- Metrics impact: parse orchestration can approach 2x for analyzers without effective caches; embedding cost itself is unchanged.
- Alternatives: a future normalized parse-artifact interface could feed both writers in one parse, but it spans every analyzer and is too broad for this change.

### UX

- Issues: multiple booleans would create invalid combinations and confusing help.
- Edge cases: missing graph storage in graph mode, missing Qdrant storage in embedding mode, embedding failure after graph success.
- Accessibility concerns: none; terminal messages must identify stage and remediation.

### Devil's advocate

- Assumptions challenged: all analyzer caches are reusable and safe for a second vector-only invocation.
- Simpler alternatives: graph-only full scan followed by a separate embedding-only full scan already provides operator control, but does not make the default `both` order automatic.
- Worst case: the second pass reparses a large monorepo, then fails vectors and leaves a graph-complete but overall failed run.

## Recommendations

1. Add `--sync-mode both|graph|embedding`, defaulting to `both`.
2. Require `--full-scan` for `graph` and `embedding` until independent stage inventories exist.
3. Implement `both` as graph primary -> framework overlays -> topology -> vector-only primary.
4. Add `vector_embeddings` and `sync_mode` to the JSON summary while retaining existing fields.
5. Add regression tests for CLI propagation, phase ordering, graph/Qdrant isolation, and invalid combinations.

## Next steps

Proceed with the mitigations above. Do not claim incremental graph-only or embedding-only support in this release.
