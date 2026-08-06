# Phase 01: Local Storage Contract and Dependencies

## Context

The current configuration model derives network URLs from host and port fields,
and the project advertises Python 3.10 compatibility. The local storage cutover
needs one canonical path model before graph or vector call sites are changed.

## Requirements

- Establish project-root-relative Qdrant and FalkorDB paths.
- Preserve registry-controlled graph and collection names.
- Pin a verified `falkordblite` version and raise Python to its supported
  minimum.
- Prevent local data from entering Git.
- Reject ambiguous configurations that contain both local paths and legacy
  remote endpoints.

## Architecture

Create a small shared storage configuration layer under
`cortex_harness/storage/` with immutable resolved settings:

- active project root;
- Qdrant base, code, and document paths;
- FalkorDB `.rdb` path;
- logical code/document graph and collection names.

Path resolution order is explicit CLI value, active project config, environment
variable, then project-root default. Resolve and normalize once at the launcher
boundary, then pass values through process environments or typed configuration.

## Related Files

- `pyproject.toml`
- `requirements.txt`
- `code-tiny/requirements.txt`
- `doc-tiny/requirements.txt`
- `.gitignore`
- `cortex_harness/dev.py`
- `scripts/mcp_runtime_config.py`
- `code-tiny/tools/common/harness_config.py`
- `code-tiny/tools/common/project_registry.py`
- new `cortex_harness/storage/__init__.py`
- new `cortex_harness/storage/config.py`
- configuration tests under `tests/`

## Implementation Steps

1. Select and pin a `falkordblite` release that installs on the supported
   operating systems and verify its actual import/API surface.
2. Raise `requires-python` and installer/runtime checks to Python 3.12 or the
   higher minimum required by that pinned release.
3. Replace the default `falkordb` server dependency with `falkordblite` for the
   local runtime; retain Neo4j/remote extras only if they are isolated from the
   default startup import path.
4. Add typed path resolution with defaults:
   `./local_qdrant_db`, `./local_qdrant_db/code`,
   `./local_qdrant_db/doc`, and
   `./local_falkordb_db/cortex.rdb`.
5. Update `dev init` and config serialization to store paths instead of
   Qdrant/FalkorDB network fields while preserving source, embedding, graph,
   and collection configuration.
6. Update MCP runtime overlay generation so both launchers produce identical
   local path semantics.
7. Detect legacy URL/host/port-only configs and stop with export/re-ingest
   guidance; do not silently target an empty local database.
8. Ignore the local database directories and temporary sidecar/lock files.
9. Add configuration tests for relative paths, absolute Windows/POSIX paths,
   invocation outside the project root, overrides, and conflicting legacy
   fields.

## Todo

- [ ] Pin and verify FalkorDBLite package/API and supported platforms.
- [ ] Raise Python requirement and installer checks.
- [ ] Add shared path configuration.
- [ ] Replace interactive host/port prompts with path prompts.
- [ ] Update runtime environment resolution.
- [ ] Add Git ignores and configuration tests.

## Risks

- A Python minimum bump affects existing developer environments and installers.
- Active configs are user-owned; automated migration must preserve them or
  create an explicit backup rather than overwriting in place.
- Relative paths can target different folders unless the resolver is anchored
  to the active project root.

## Success Criteria

- One testable resolver determines every local database path.
- A new config contains no required database URL, host, port, password, or TLS
  field.
- Python and dependency metadata agree across root, code, and document packages.
- Existing remote-only configs fail safely with actionable migration guidance.
