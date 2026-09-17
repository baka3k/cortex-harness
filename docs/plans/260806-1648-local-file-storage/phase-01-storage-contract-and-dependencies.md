# Phase 01: Local Storage Contract and Dependencies

## Context

The current configuration model derives network URLs from host and port fields,
and the first local implementation resolves database paths below each source
checkout. That works for one smoke-test project, but it does not provide stable
physical ownership for multiple projects, named harness instances, or several
MCP processes. The local storage cutover needs one canonical identity and path
model before graph or vector call sites are accepted.

## Requirements

- Establish `Path.home() / ".cortext-harness"` as the cross-platform data root
  with versioned instance and owner directories.
- Make centralized user-data storage the default for every registered source
  project; never infer a database root from that project's directory.
- Preserve registry-controlled graph and collection names.
- Separate physical store ownership (`instance_id` + `owner_id`) from logical
  project scope (`project_id`).
- Pin a verified `falkordblite` version and raise Python to its supported
  minimum.
- Prevent local data from entering Git.
- Reject ambiguous configurations that contain both local paths and legacy
  remote endpoints.
- Migrate existing repository-local data non-destructively.

## Architecture

Evolve the shared storage configuration layer under
`cortex_harness/storage/` to expose immutable resolved settings:

- active project root and resolved machine data root;
- storage schema version and stable instance ID;
- validated code/document/additional owner IDs;
- Qdrant base and owner paths;
- owner-specific FalkorDB `.rdb` paths;
- logical code/document graph and collection names.

Path resolution order is explicit CLI value, active project config, environment
variable, then `Path.home() / ".cortext-harness"`. Relative explicit overrides
remain project-root-relative for compatibility. Resolve and normalize once at
the launcher boundary, then pass absolute values through process environments
or typed configuration. The default branch does not inspect the current
working directory, source project, Cortex Harness checkout, `LOCALAPPDATA`,
`APPDATA`, `XDG_DATA_HOME`, or macOS application-support folders.

The no-override defaults are exact and testable, while the account component is
always discovered dynamically:

| Platform | Data root |
| --- | --- |
| macOS | `/Users/<current-account>/.cortext-harness` |
| Linux | `/home/<current-account>/.cortext-harness` |
| Windows | `C:\\Users\\<current-account>\\.cortext-harness` |

For ten source projects, `resolve_storage(...)` returns the same physical
instance/owner paths regardless of the projects' current directories. Only the
registry-resolved graph, collection, payload scope, and source-location
metadata differ. A source move therefore updates the registry and does not
trigger a database-directory migration.

The canonical tree is:

```text
<data-root>/v1/instances/<instance-id>/
├── manifest.json
├── qdrant/<owner-id>/
├── falkordb/<owner-id>/data.rdb
└── backups/<timestamp>/...
```

`project_id` never appears as a required directory component. Each owner store
contains many registry-resolved project graphs or collections, preserving
cross-project queries inside that owner while preventing lock collisions
between owners.

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
- new `cortex_harness/storage/layout.py`
- new `cortex_harness/storage/lease.py`
- new `cortex_harness/storage/migration.py`
- configuration tests under `tests/`

## Implementation Steps

1. Select and pin a `falkordblite` release that installs on the supported
   operating systems and verify its actual import/API surface.
2. Raise `requires-python` and installer/runtime checks to Python 3.12 or the
   higher minimum required by that pinned release.
3. Replace the default `falkordb` server dependency with `falkordblite` for the
   local runtime; retain Neo4j/remote extras only if they are isolated from the
   default startup import path.
4. Add typed resolution for `CORTEX_DATA_HOME`, schema `v1`,
   `CORTEX_STORAGE_INSTANCE`, and validated owner IDs. When no override exists,
   use `Path.home() / ".cortext-harness"` rather than the Cortex Harness
   repository, indexed source checkout, caller's current directory, or a
   platform-specific application-data directory.
   Implement this independently from project config discovery functions that
   walk upward for `.cortext-harness/config`.
5. Derive `QDRANT_CODE_PATH`, `QDRANT_DOC_PATH`,
   `FALKORDB_CODE_PATH`, and `FALKORDB_DOC_PATH` from instance and owner. Keep
   single-path overrides only for compatibility, tests, and migration.
6. Add a versioned `manifest.json` containing schema version, instance ID,
   owner inventory, created timestamp, and path provenance. Do not put secrets,
   PIDs, or mutable project inventories in it.
7. Update `dev init` and config serialization to store instance ID and only an
   optional data-root override while preserving source, embedding, graph, and
   collection configuration.
8. Update MCP runtime overlay generation so both launchers select an explicit
   owner and produce identical absolute local paths.
9. Detect legacy URL/host/port-only configs and stop with export/re-ingest
   guidance; do not silently target an empty local database.
10. Add a non-destructive migration command for
    `./local_qdrant_db/{code,doc}` and `./local_falkordb_db/*.rdb`. Default to
    dry-run; require owners stopped; copy, hash, reopen, inventory, then switch;
    retain the old paths.
11. Keep legacy local database directories and temporary sidecar/lock files
    ignored even though canonical defaults move outside Git worktrees.
12. Add configuration tests for platform defaults, schema version, instance
    isolation, owner sanitization, relative/absolute overrides, invocation
    outside the project root, conflicting legacy fields, and migration
    idempotency.
13. Add a ten-project fixture whose source roots are unrelated directories.
    Assert that all projects share the same code/doc owner paths, receive
    distinct logical targets, and retain those physical paths after one source
    directory is moved.
14. Add fake-home tests for macOS, Linux, and Windows path shapes. Assert the
    resolver appends only `.cortext-harness`, honors `CORTEX_DATA_HOME`, and
    never serializes the developer account or an absolute path fixture as a
    default.
15. Add a collision test with `<project>/.cortext-harness/config` present.
    Assert configuration discovery may read it while database resolution still
    returns `<fake-home>/.cortext-harness/...`.

## Todo

- [x] Pin and verify FalkorDBLite package/API and supported platforms.
- [x] Raise Python requirement and installer checks.
- [x] Add versioned data-root, instance, and owner configuration.
- [x] Split FalkorDB paths by code/document owner.
- [x] Replace interactive host/port prompts with path prompts.
- [x] Update runtime environment resolution.
- [x] Add dry-run/copy/verify layout migration.
- [x] Add Git ignores and configuration tests.

## Risks

- A Python minimum bump affects existing developer environments and installers.
- Active configs are user-owned; automated migration must preserve them or
  create an explicit backup rather than overwriting in place.
- Relative paths can target different folders unless the resolver is anchored
  to the active project root.
- Owner IDs derived from display names, ports, or PIDs would create unstable
  paths; accept only persisted validated IDs.
- Copying a live embedded store can produce an invalid backup; migration and
  backup must prove the owner is stopped before filesystem copying.

## Success Criteria

- One testable resolver determines every local database path from data root,
  schema version, instance, and owner.
- Two projects in one owner resolve to the same physical store but distinct
  registry graph/collection targets; two instances or owners resolve to
  distinct physical paths.
- Ten source projects in ten locations use one centralized Cortex Harness data
  root by default. No database directory is created in any source checkout.
- The default root is derived from the current account as
  `Path.home() / ".cortext-harness"` on macOS, Linux, and Windows; no username
  or absolute home path is hardcoded.
- Moving or deleting a source checkout never silently moves or deletes its
  indexed database content.
- A new config contains no required database URL, host, port, password, or TLS
  field.
- Python and dependency metadata agree across root, code, and document packages.
- Existing remote-only configs fail safely with actionable migration guidance.
- Existing repository-local stores migrate without deletion, and a second run
  is a verified no-op rather than a duplicate copy.
