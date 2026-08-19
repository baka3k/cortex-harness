# Phase 05: INI Descriptor, DAT Resource Entry, Registration Sweep

## Context

Remaining gaps: the flat `KEY:VALUE` `.ini` files (e.g. `batch_entry_settings.ini`) referenced by shell scripts have no descriptor at all, `.DAT` data directories have no topology entry, and the earlier phases leave several 7-file-checklist rows to double-check end-to-end. This phase closes both content gaps with the lightweight `project_topology` descriptor pattern (no graph writer, no vector embedding — these are configuration/resource metadata, not code) and does a final sweep/test pass across all four new/extended parsers.

## Requirements

- New lightweight descriptor parser `code-tiny/tools/project_topology/parsers/ini.py` mirroring `parsers/make.py`'s `DescriptorParseOutput` shape: parse `KEY:VALUE` lines (ignore `#`-comment lines and blank lines) into a `descriptors` list of `{key, value, line}`; role `DescriptorRole.CONFIGURATION`, `ParseDepth.IDENTITY`. Read through `read_legacy_text` (Phase 01).
- Register a `CoverageEntry("ini", ("*.ini",), (DescriptorRole.CONFIGURATION,), ParseDepth.IDENTITY, "parser-special-files/ini")` in `code-tiny/tools/project_topology/registry.py`.
- Add a `.DAT` entry: `CoverageEntry("dat", ("*.DAT", "*.dat"), (DescriptorRole.RESOURCE,), ParseDepth.IDENTITY, "parser-special-files/dat")` — path/size metadata only, explicitly no content parsing (binary/fixed-width legacy data).
- Cross-check the shell `REFERENCES` edges from Phase 03 against the ini descriptor: when a shell script's resolved `.ini` reference matches a scanned `.ini` file, the shell analyzer's edge target should line up with this descriptor's file node (verify via an integration test, not a new coupling in the shell analyzer itself — the shell analyzer only needs the file path, not ini contents).
- Full registration sweep: re-verify every row of the 7-file checklist for `cplus`(`.pc`), `shell`, `jp1`, and `ini`/`dat` is present and consistent — run `tests/test_mcp_acceptance_matrix.py` and `tests/test_common_analyzer_registry.py` and fix any drift.
- Update `code-tiny/README.md` / `Design.md` analyzer tables (the same tables enumerated in the earlier research, e.g. `code-tiny/README.md:25`) to list the new `shell`/`jp1` parsers and the `.pc` extension under `cplus`, consistent with how `cplus`/`perl` are already documented there.
- End-to-end fixture test: build a small synthetic multi-file fixture project (jobnet → shell → ini, plus one `.pc` file) and run `dev sync code` (or the equivalent test harness entry point used by other analyzer suites) over it, asserting the full node/edge graph resolves as expected, including at least one intentionally-unresolved reference to prove diagnostics surface instead of silently dropping data.

## Architecture

No new executable — this phase is descriptor registration plus cross-cutting verification. The only new runtime code is `parsers/ini.py`, which follows the exact `DescriptorParseOutput` contract already used by `parsers/make.py`.

## Related Files

Create:
- `code-tiny/tools/project_topology/parsers/ini.py`
- `tests/fixtures/legacy-migration-e2e/` (combined jobnet + shell + ini + pc fixture project)
- `tests/test_project_topology_ini_parser.py`
- `tests/test_legacy_migration_e2e.py`

Modify:
- `code-tiny/tools/project_topology/registry.py` (`PRIMARY_SPECIAL_FILE_COVERAGE` — add `ini`, `dat` entries)
- `code-tiny/README.md`, `code-tiny/Design.md` (analyzer tables)
- `tests/test_mcp_acceptance_matrix.py`, `tests/test_common_analyzer_registry.py` (final consistency fixes only, no new parsers expected here if Phases 02-04 registered correctly)

Reference:
- `code-tiny/tools/project_topology/parsers/make.py` (descriptor parser pattern)
- `code-tiny/tools/project_topology/models.py:62-72` (`DescriptorRole` enum)
