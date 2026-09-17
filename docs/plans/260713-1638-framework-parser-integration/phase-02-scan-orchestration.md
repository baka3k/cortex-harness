# Phase 02: Add Framework Overlays to the Main Scan

## Context

`dev sync code` delegates to `incremental_sync.py`, whose current file classifier returns one parser per path. Spring, Servlet/JSP, and MyBatis need to inspect files already owned by Java/Kotlin and sometimes the same XML/configuration files. Treating them as primary owners would lose base-language facts or cause ownership/deletion conflicts.

## Requirements

- Keep primary language parsing exclusive.
- Run framework analyzers as optional overlays after their prerequisites.
- Preserve incremental manifests and deletion behavior.
- Skip non-matching projects cheaply.
- Keep `dev sync code`, `dev sync code all`, and explicit parser selection understandable.
- Do not create unused framework Qdrant collections.

## Architecture

### Two registries

Retain `ANALYZERS` for primary language analyzers and add `FRAMEWORK_ANALYZERS` for overlays. Each framework entry should define:

- `script_path`;
- `incremental_supported`;
- trigger extensions/files;
- required primary languages;
- detector name/callable or a lightweight project evidence probe;
- execution order;
- `writes_vectors=False` initially.

### Overlay selection

Build framework manifests from the global changed/deleted/impacted sets, not from the exclusive `_group_paths_by_parser()` result.

- Spring candidates: `.java`, `.kt`, `.kts`, `pom.xml`, Gradle files, Spring application config files, and Spring XML.
- Servlet/JSP candidates: `.java`, JSP-family files, `web.xml`, related properties, static targets, and build descriptors.
- MyBatis candidates: mapper `.java`/`.kt`, mapper/config XML, build files, and Spring bridge XML.

The framework's existing detector remains the final authority. A candidate trigger should cause the analyzer to evaluate the affected module; it should not assert that the project uses the framework.

### Execution order and failure behavior

Run primary language analyzers first, then framework overlays in this order:

1. Spring
2. Servlet/JSP
3. MyBatis

Base-language failure prevents dependent overlays from running. A framework failure marks the sync state dirty and reports which overlay failed; it must not roll back successful canonical language ingestion.

For full scans, framework analyzers receive the whole project through their normal discovery flow. For incremental scans, manifests contain changed, deleted, and dependency-expanded paths. The framework analyzer decides which modules need rebuilding.

### CLI behavior

- `--parsers auto`: run detected primary analyzers plus applicable overlays.
- `--parsers java,spring`: explicitly run Java and Spring.
- Selecting only a framework should either auto-add its required base parser or fail with an actionable prerequisite message. Prefer auto-add for `dev sync code` and record it in the summary.
- Sync summaries should separate `primary_parsers` and `framework_overlays`.
- Avoid assigning framework-specific Qdrant collection names when `writes_vectors=False`; report the canonical Java/Kotlin collections used for semantic seeds instead.

## Related Files

- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/sync/owner_manifest.py`
- `code-tiny/tools/sync/build_owner_manifests.py`
- `cortex_harness/dev.py`
- `code-tiny/tools/mybatis/detector.py`
- `code-tiny/tools/servlet_jsp/detector.py`
- `code-tiny/tools/spring/detector.py`
- `tests/test_incremental_sync_framework_overlays.py` (new)
- `tests/test_dev_framework_parser_discovery.py` (new)

## Implementation Steps

1. Add a framework analyzer configuration type and registry.
2. Add a candidate grouping function that supports multiple overlays per file.
3. Add prerequisite resolution and deterministic execution ordering.
4. Reuse `_build_analyzer_cmd()` for overlay analyzers, with an explicit `writes_vectors` capability to avoid misleading collection setup.
5. Pass incremental changed/deleted manifests to each applicable overlay.
6. Keep `owner_manifest.py` exclusive for primary owners; document why overlays are not added to `SUPPORTED_PARSERS`.
7. Update CLI discovery/status output in `cortex_harness/dev.py` so `dev sync code all` lists overlays accurately.
8. Add summary fields for detector evidence, skipped overlays, prerequisites, counts, duration, and failure state.
9. Add fixture tests for single-framework, mixed-framework, non-framework, incremental update, and deletion scenarios.

## Todo

- [x] A `.java` file can feed Java, Spring, Servlet/JSP, and MyBatis when evidence warrants it.
- [x] XML files can feed multiple framework overlays without changing primary ownership.
- [x] Non-framework projects do not pay full analyzer cost.
- [x] Full and incremental modes both work.
- [x] Explicit parser selection has deterministic prerequisite behavior.
- [x] Dirty sync state captures overlay failures.
- [x] CLI output distinguishes primary and overlay work.

## Risks

- Running all overlays on every Java project could increase scan time significantly; detector gating must be cheap and measured.
- Deletion-only changes may no longer have file contents for detection. Cached module evidence and prior framework state must route tombstones correctly.
- MyBatis and Spring may both emit persistence facts. Stable IDs and label semantics must avoid duplicate or contradictory nodes.
- The existing `LANG_ANALYZERS` map in `dev.py` is partly legacy; changing it without tracing callers could create stale display behavior or duplicate execution.

## Success Criteria

- One `dev sync code` command produces canonical and framework graphs from a mixed Java fixture.
- Deleting a mapper XML, JSP, controller, or Spring config removes only the affected framework facts.
- Non-framework Java fixtures run no overlay subprocesses after lightweight detection.
- Re-running without changes executes no analyzers and keeps the sync state clean.
