# Phase 06: Harden and Verify Migration Acceptance

## Context

Final acceptance proves migration usefulness, determinism, safety on untrusted repositories, incremental correctness, and logical provider parity. Close documented gaps without expanding into runtime instrumentation or source transformation.

## Requirements

- Compare Framework/Core output through one contract without analyzer-specific downstream logic.
- Prove deterministic, root-independent, bounded, redacted behavior under malformed/large inputs.
- Prove incremental results equal clean full-run semantics for affected modules.
- Prove provider-neutral logical parity when Neo4j and FalkorDB are available.
- Document host/runtime/parser limits and stable operations.

## Acceptance Scenarios

1. Global.asax/HttpModule/HttpHandler and Program/middleware/endpoints are comparable through the shared vocabulary.
2. MVC5/Web API 2 and ASP.NET Core MVC/Web API routes/controllers/actions/views are comparable.
3. Web Forms page/master/control/ViewState/postback facts can be located beside Razor page/layout/partial/stateless-state targets without automatic transformations.
4. web.config and appsettings expose equivalent redacted keys, provenance, and consumers.
5. Changed/deleted code/config/view artifacts update only their dependency closure and active generation.

## Related Files

- Both analyzer packages and shared ASP.NET support
- Both fixtures and an optional paired migration fixture
- Performance/security/determinism/incremental/provider tests
- Tool READMEs and supported-tool documentation

## Implementation Steps

1. Add a compact paired fixture representing equivalent endpoints, auth, validation, services, results/views, and configuration.
2. Assert cross-analyzer compatibility for labels, directions, required properties, evidence, resolution states, and migration queries.
3. Add repeated-run, randomized-order, checkout-root, locale/timezone, and cache determinism tests.
4. Add malformed/oversized C#, project, Razor, Web Forms, XML, JSON, and resource cases; assert bounded diagnostics, partial coverage, deterministic truncation, and no orphans.
5. Test outside-root paths, symlinks, XXE/network denial, redaction, command safety, malicious project metadata, and disabled execution.
6. Test full/incremental parity for code/config/view/project changes, dependencies, deletions, renames, cache invalidation, failed staging, and mixed solutions.
7. Measure startup/workspace/parse/memory on small/medium fixtures; set evidence-based budgets.
8. Run logical graph/query parity on both providers when available; keep unavailable provider gates incomplete, not silently waived.
9. Run focused/regression tests, Python compilation, worker builds, CLI/MCP smoke tests, and `git diff --check`.
10. Complete READMEs with prerequisites, support matrix, modes, CLI, incremental/cache, coverage, security, troubleshooting, and verified commands.

## Todo

- [ ] Paired migration queries prove the shared contract.
- [ ] Determinism, malformed/large, and security suites pass.
- [ ] Full/incremental/deletion/cache parity passes.
- [ ] Performance budgets are measured/documented.
- [ ] Provider parity passes or remains explicitly gated.
- [ ] Documentation and regression evidence are complete.

## Risks

- Small fixtures can overstate support for convention-heavy applications.
- Provider availability can delay live parity.
- Host-specific performance guards can be miscalibrated.

## Success Criteria

- A migration engine compares endpoints, pipelines, controllers/pages, views, config, state, validation, and services using one schema.
- Output is deterministic, root-independent, bounded, redacted, and explicit about partial behavior.
- Full/incremental analysis agree and failed runs never replace the last valid generation.
- Promised verification passes, or external-provider gates remain clearly incomplete.

