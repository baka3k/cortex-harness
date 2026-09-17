# Phase 05: Harden, Document, and Verify Acceptance

## Context

The final phase closes correctness, security, resource, provider-parity, documentation, and regression gaps. It does not broaden the analyzer into runtime or full semantic analysis.

## Requirements

- Bound file sizes, total bytes, AST depth/work, output counts, diagnostics, documentation extraction, wall time, and memory with stable truncation behavior.
- Never execute Perl, module hooks, build scripts, `eval`, generated code, or external commands from analyzed sources.
- Enforce root containment and safe symlink behavior for source, manifest, cache, and output paths.
- Redact credentials/secrets from comments, POD, code previews, diagnostics, cache, vector payloads, and graph properties.
- Measure deterministic output, incremental efficiency, and representative fixture performance.
- Verify logical parity across Neo4j and FalkorDB after the migration gate is available.
- Document support, limitations, install/runtime versions, CLI, JSON/graph contracts, incremental/cache semantics, and troubleshooting.

## Related Files

- `code-tiny/tools/perl/README.md`
- `README.md`
- `docs/specs/sync-code.md`
- `code-tiny/mcp/Readme.md`
- `code-tiny/docs/guide_tool_integrate.md` only if implementation reveals a new shared integration requirement
- Perl tests and representative/medium fixtures under `tests/`
- existing graph-provider test utilities

## Implementation Steps

1. Calibrate accepted-output budgets from minimal and medium fixtures; separate them from operational abort guards.
2. Add hostile/malformed input tests: deep nesting, huge heredocs/POD, invalid bytes, symlink escapes, outside-root manifests, dynamic references, and secret-shaped content.
3. Verify truncation produces deterministic prefixes, accurate counters, partial coverage, and no orphan relationships.
4. Compare normalized output across repeated runs, shuffled discovery order, different checkout roots, cache modes, and full/incremental analysis.
5. Measure medium-fixture parse time, peak memory, cache effect, and one-file incremental scope; record baselines without promising unsupported scale.
6. Run logical node/edge/query parity against Neo4j and FalkorDB when the blocking migration is ready; otherwise keep the plan pending rather than claiming completion.
7. Update tool README, supported-parser lists, sync-code specification, and MCP documentation with exact aliases, extensions, capabilities, and limitations.
8. Run focused tests, relevant provider/MCP/sync regressions, `py_compile`, `git diff --check`, and one end-to-end full plus changed/deleted fixture sync.

## Todo

- [ ] Security and path-containment tests pass.
- [ ] Budgets and truncation are measured and deterministic.
- [ ] Secrets are redacted before every output adapter.
- [ ] Full/incremental/cache outputs are equivalent for affected scope.
- [ ] Neo4j/FalkorDB logical parity is verified after the blocker clears.
- [ ] README and supported-tool docs match actual commands and limitations.
- [ ] Focused and relevant regression suites pass.

## Risks

- Secret detection can over-redact useful source or under-redact credentials embedded in documentation.
- Provider parity may expose transaction or query-shape differences outside the Perl package.
- Performance baselines from only tiny fixtures can hide pathological Perl source shapes.

## Success Criteria

- The analyzer meets every main-plan success criterion with test or command evidence.
- Resource-limit behavior is deterministic, safe, and accurately reflected in coverage/diagnostics.
- Neo4j and FalkorDB produce equivalent logical Perl facts and relevant MCP results.
- Documentation commands execute successfully in a clean environment.
- Only intended Perl/integration files and the required cross-plan metadata are changed.

