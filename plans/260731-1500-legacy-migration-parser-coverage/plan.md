---
title: "Legacy Migration Parser Coverage: Pro*C, Shell, JP1 Jobnet, INI"
status: completed
created: 2026-07-31
mode: hi-plan --full
source: external legacy-migration sample tree
target: code-tiny/tools/cplus, code-tiny/tools/shell (new), code-tiny/tools/jp1 (new), code-tiny/tools/project_topology, code-tiny/tools/sync, code-tiny/mcp
scope: Oracle Pro*C (.pc) coverage in cplus_analyzer, new shell-script analyzer, new JP1 jobnet analyzer, INI special-file descriptor, shared legacy encoding utility, full registration/test-matrix sweep
blockedBy: []
relatedPlans:
  [
    260714-1702-cobol-analyzer-parser,
    260715-1629-perl-analyzer-parser,
    260713-1638-framework-parser-integration,
    260728-0000-unified-ingest-query-contract,
  ]
---

# Legacy Migration Parser Coverage: Pro*C, Shell, JP1 Jobnet, INI

## Overview

An external Java-migration project supplies a legacy batch-system tree: Oracle Pro*C sources (`.c`/`.pc` with embedded `EXEC SQL`), JP1 job-network definitions (`.txt`, Hitachi JP1 DSL) that invoke shell scripts, shell scripts (`.sh`) that `grep` key:value `.ini` files for parameters, and raw `.DAT` data files. All of it is Shift-JIS (CP932) encoded. This plan closes the ingestion gap so `dev sync code` and unified MCP tools can see this material instead of silently skipping it.

```text
JP1 jobnet (.txt, unit=/ty=/te=)
  --te="script.sh"--> shell script (.sh)
                          --grep 'KEY' file.ini--> ini key:value (.ini)
                          --. other.sh / calls--> shell script (.sh)
Pro*C source (.c / .pc)
  --EXEC SQL--> table/operation facts (mirrors COBOL EXEC SQL handling)
.DAT data files
  --topology-only RESOURCE descriptor (no parsing)
```

## Verified Project Context

- `_scan_c_family_files` (`code-tiny/tools/cplus/cplus_analyzer.py:2489-2499`) matches only `.c/.h/.hpp/.cpp/.cc/.cxx/.hh/.hxx/.rc/.rc2` — **`.pc` is not scanned**, so Pro*C files (e.g. `Sample02.pc`) are silently skipped today. No `EXEC SQL` handling exists anywhere under `code-tiny/tools/cplus/`.
- `_SOURCE_EXTENSIONS` (`code-tiny/tools/sync/incremental_sync.py:1205-1225`) also omits `.pc`, `.sh`, and `.ini` (it has `.properties/.yml/.yaml/.json` but not `.ini`).
- `ANALYZERS` (`incremental_sync.py:81-101`) has no `shell` or `jp1` entry; no `tools/shell` or `tools/jp1` package exists.
- COBOL already has the exact pattern to mirror for `EXEC SQL`: `code-tiny/tools/cobol/parser.py:191-195` extracts `operation`/`targets`/`host_variables` into a `ParsedStatement`; `code-tiny/tools/cobol/semantics.py:231-244` turns that into a `CobolSqlStatement`/`CobolCicsCommand` node plus a `DEFINES` edge from the enclosing paragraph. `cplus_analyzer.py` should reuse the same shape (`CplusSqlStatement`, `DEFINES` edge from the enclosing function).
- No shared encoding-detection utility exists. `cobol/parser.py` has a cp037/cp1252 fallback with COBOL-keyword scoring; `cplus/rc_parser.py` (`code-tiny/tools/cplus/rc_parser.py:54-89`) has BOM sniffing plus a `("utf-8", "cp932")` fallback chain, but nothing shares it. Sample `.pc`/`.sh`/`.ini` files from the source tree are Shift-JIS; reading them as UTF-8 produces mojibake in comments/strings and can corrupt Japanese string literals used as evidence text.
- JP1 jobnet files use the generic `.txt` extension (`JBCWV013_ALL.txt`) — a project can have unrelated `.txt` files, so ownership must be decided by **content sniffing** (first non-blank line matches `^unit=`), never by extension alone.
- Shell scripts resolve their companion `.ini` path dynamically, e.g. `` `${BZZ_BT_ENVPATH}/${WK_MK_MNG_KEY}-${WK_MK_MNG_PTN_KEY}.ini` `` where `WK_MK_MNG_KEY`/`WK_MK_MNG_PTN_KEY` are themselves read out of another `.ini` via `grep`. Static resolution of the final path is not always possible — the analyzer must record the raw expression as an **unresolved dynamic reference** diagnostic rather than fabricate a fake edge.
- `.ini` samples are flat `KEY:VALUE` pairs (no `[section]` headers), not standard INI — a small dedicated parser is warranted rather than reusing a generic `configparser`-based one.
- `DescriptorRole` (`code-tiny/tools/project_topology/models.py:62-72`) has no `DATA`/`RAW` role; `RESOURCE` is the closest fit and has precedent for Android/Delphi/Flutter resource files. No existing coverage entry treats `.DAT` files at all.
- Full analyzer registration requires touching 7 files in lockstep (per `/memories/repo/cortex-harness-notes.md`): `code-tiny/tools/sync/incremental_sync.py` (`ANALYZERS`, `_select_parser_for_path`, `_SOURCE_EXTENSIONS`), `code-tiny/tools/sync/owner_manifest.py` (`SUPPORTED_PARSERS` + detection function), `cortex_harness/dev.py` (`LANG_ANALYZERS`, `LANG_EXTENSIONS`), `code-tiny/mcp/framework_registry.py` (`CAPABILITIES`), `code-tiny/tools/project_topology/registry.py` (`PRIMARY_SPECIAL_FILE_COVERAGE`), `tests/test_mcp_acceptance_matrix.py` (`ACCEPTANCE_MATRIX`, `PRIMARY_TO_PROFILE`), `tests/test_common_analyzer_registry.py` (`EXPECTED_PRIMARY`).
- `code-tiny/tools/perl/perl_analyzer.py` is the reference skeleton for a new lightweight analyzer: argparse CLI (`--root`, `--project-id`, incremental manifests, Neo4j/Qdrant args), a `pipeline.py` producing a fact result, `build_graph_rows()` mapping to the canonical `LanguageCodeWriter` row shape, and `safe_cache_root()` for incremental caching.
- `code-tiny/tools/project_topology/parsers/make.py` is the reference skeleton for a **lightweight, non-graph** special-file descriptor (`DescriptorParseOutput` with `descriptors`/`dependencies`, no Neo4j/Qdrant writer) — the right shape for `.ini` and the `.DAT` resource entry.
- The sample source tree lives outside the repo (`/Users/baka3k/JavaMigration/...`) and must not be copied verbatim into fixtures (unknown license/confidentiality); fixtures must be small synthetic files that reproduce the same structural shapes (EXEC SQL block, JP1 `unit=`/`ar=` DAG, shell `grep '...ini'`, flat `KEY:VALUE` ini).
- `neo4j-to-falkordb-migration` (in_progress) owns live provider parity; this plan targets the existing provider-neutral `LanguageCodeWriter`/`GraphDriver` contract only, same precedent as the completed COBOL plan.
- `260715-1629-perl-analyzer-parser` (pending) and `260728-0000-unified-ingest-query-contract` (pending, itself blocked) touch the same 7 registration files / `framework_registry.py` CAPABILITIES map — coordinate before merging to avoid clobbering each other's dict entries.

## Scope Model

| Gate | Included capability |
| --- | --- |
| MVP (end of Phase 03) | Shared legacy-encoding read utility (CP932/Shift-JIS aware); `.pc` added to `cplus_analyzer` file scan; Pro*C `EXEC SQL` extraction as `CplusSqlStatement` nodes + `DEFINES` edges; new `shell` analyzer parsing function defs, script-to-script invocation (`. x.sh`, `sh x.sh`, `./x.sh`), and `grep '<KEY>' <path>.ini` references (static path resolved when possible, else recorded as unresolved diagnostic) |
| Full (end of Phase 05) | New `jp1` analyzer (content-sniffed `.txt`) modeling `unit=`/`ty=`/`ar=(f=,t=)` job DAGs with `CALLS` edges into shell-script files referenced by `te=`; `.ini` lightweight descriptor parser (flat `KEY:VALUE`); `.DAT` topology-only `RESOURCE` descriptor entry; full 7-file registration sweep for `shell`/`jp1`/`ini`; acceptance-matrix and registry tests updated and passing |

Excluded from this plan:

- Executing/interpreting shell scripts or JP1 jobnets (static analysis only).
- Parsing `.DAT` binary/fixed-width record layouts (only a topology-level resource descriptor is added).
- Full POSIX shell grammar (only the subset needed for invocation and `grep`-based config reads: function defs, `.`/`source`, direct script invocation, `grep` pattern extraction).
- A dedicated JP1/shell/ini MCP server, graph backend, or vector collection — reuse existing provider-neutral writer and Qdrant plumbing.
- Live Neo4j/FalkorDB parity validation (owned by `neo4j-to-falkordb-migration`).
- Resolving shell variable expressions that require actual runtime environment values (e.g. externally-injected `@BOSAPDIR@`/`@DBLOGDIR@` placeholders) beyond recording them as literal unresolved references.

## Target Architecture

### Shared encoding utility (foundation)

New `code-tiny/tools/common/legacy_encoding.py`:

```python
def read_legacy_text(path: str) -> tuple[str, str, list[Diagnostic]]:
    ...  # BOM sniff -> utf-8 -> cp932 (Shift-JIS) -> cp1252 errors="replace"
```

Mirrors the BOM-sniff + fallback-chain shape already in `cplus/rc_parser.py`, generalized so `cplus_analyzer`, the new `shell`/`jp1` analyzers, and the `.ini` descriptor parser all share one tested implementation instead of re-inventing encoding handling per parser.

### Pro*C coverage in `cplus_analyzer`

- Extend `_scan_c_family_files` to also match `.pc` (and `.pcc` if encountered).
- Reuse `read_legacy_text` wherever the analyzer currently opens source files directly.
- Add an `EXEC SQL` statement extractor mirroring `cobol/parser.py:191-195` (`operation`, `targets`, `host_variables`) and a semantics step mirroring `cobol/semantics.py:231-244`: emit a `CplusSqlStatement` node and a `DEFINES` edge from the enclosing C function.
- Register `.pc` across the 7-file checklist under the existing `cplus` parser key (no new parser name needed).

### New `shell` analyzer

`code-tiny/tools/shell/shell_analyzer.py` following the `perl_analyzer.py` skeleton (CLI, `pipeline.py`, `build_graph_rows()`, `safe_cache_root()`):

- Nodes: `ShellScript`, `ShellFunction`.
- Edges: `CALLS` for `. other.sh` / `source other.sh` / `sh other.sh` / `./other.sh`; `REFERENCES` for `grep '<KEY>' <path>` against a `.ini`-suffixed path — when the path contains unresolved shell variables, still emit the edge with a `resolved: false` property and the raw expression, instead of dropping it.
- Register `shell` across the 7-file checklist (`.sh` extension).

### New `jp1` analyzer

`code-tiny/tools/jp1/jp1_analyzer.py`:

- Detection: content sniff, not extension — first non-blank line matches `^unit=`; `_select_parser_for_path` for `.txt` must sniff before claiming the file (never claim all `.txt`).
- Nodes: `Jp1Unit` (one per `unit=` block, properties: `type` from `ty=`, `comment` from `cm=`, `exec_target` from `te=`).
- Edges: `NEXT` for `ar=(f=A,t=B)` sequencing; `CALLS` from a `Jp1Unit` to the `ShellScript` file named in `te="...sh"` (cross-parser edge — resolve target file path relative to project root, record unresolved if the referenced path can't be located).
- Register `jp1` across the 7-file checklist with the content-sniff guard.

### `.ini` and `.DAT` (lightweight, non-graph)

- `code-tiny/tools/project_topology/parsers/ini.py` mirroring `parsers/make.py`: parse flat `KEY:VALUE` lines into `descriptors`, role `CONFIGURATION`, `ParseDepth.IDENTITY`. Register a `CoverageEntry` in `project_topology/registry.py`.
- Add a `.DAT` `CoverageEntry` with role `RESOURCE`, `ParseDepth.IDENTITY`, no content parsing — path/size metadata only.

## Risks / Red-Team Notes

- **`.txt` content-sniff false positives/negatives**: a non-JP1 `.txt` starting with `unit=` (unlikely but possible in generic config trees) would be misclassified. Mitigation: require at least the `unit=`, `{`, and one `ty=` line within the first ~5 lines before accepting, and make the sniff opt-out via a per-project ignore list if it misfires (Phase 04 requirement).
- **Dynamic `.ini` path resolution is inherently incomplete** (shell variables built from other files' contents). Do not attempt full shell variable interpolation — cap effort at simple `${VAR}`/`` `cmd` `` substitution from variables assigned earlier in the *same* file; anything else becomes an `unresolved` diagnostic. Over-engineering a shell interpreter here is explicitly out of scope.
- **Encoding heuristic ambiguity**: CP932 and UTF-8 can both decode certain byte sequences without error but produce wrong text. Mitigation: prefer UTF-8 only if it decodes *and* round-trips cleanly; otherwise fall back to CP932; log which encoding was chosen per file as a diagnostic so downstream users can spot misdetections instead of silently trusting mojibake.
- **Registration-file merge conflicts** with `260715-1629-perl-analyzer-parser` and `260728-0000-unified-ingest-query-contract` (both pending, touch the same dict-based registries). Mitigation: keep each new dict entry as a minimal, independently-mergeable line; do not reformat surrounding entries.
- **Fixture provenance**: real project files are external and Shift-JIS; committing them verbatim risks license/confidentiality issues and non-portable encoding in the repo. Mitigation: hand-author small synthetic fixtures (Phase 01/03/04/05) that reproduce the structural shapes only (EXEC SQL block, JP1 `unit=`/`ar=` DAG, shell `grep` pattern, flat ini), including at least one CP932-encoded fixture to exercise the encoding utility.
- **Scope creep into full shell/JP1 semantics** (e.g. full POSIX grammar, JP1 scheduling semantics like `sd=`/`wth=` wait conditions): explicitly excluded above; only structural nodes/edges needed for dependency/impact tracing are in scope.

## Validation Summary (from scope-challenge interview)

- Deliverable: full implementation plan (not audit-only).
- Priority: single combined plan covering Pro*C, JP1, shell, and INI together (no strict sequential priority between formats).
- Pro*C depth: `EXEC SQL` extracted as first-class graph relations (mirrors COBOL), not just basic C recognition.
- Encoding: CP932/Shift-JIS detection is in-scope and foundational (Phase 01), since sample sources are not UTF-8.

## Phases

1. [Phase 01: Shared Legacy-Encoding Utility](phase-01-shared-legacy-encoding-utility.md)
2. [Phase 02: Pro*C (.pc) EXEC SQL Coverage](phase-02-proc-exec-sql-coverage.md)
3. [Phase 03: Shell Script Analyzer](phase-03-shell-script-analyzer.md)
4. [Phase 04: JP1 Jobnet Analyzer](phase-04-jp1-jobnet-analyzer.md)
5. [Phase 05: INI Descriptor, DAT Resource Entry, Registration Sweep](phase-05-ini-dat-registration-sweep.md)

## Completion

Completed 2026-07-31. Implemented shared legacy decoding, Pro*C SQL facts, shell and JP1 analyzers, INI/DAT topology coverage, primary analyzer registration, MCP capabilities, vector synchronization, incremental cleanup, and project-root path confinement.

Validation: `28 passed, 78 subtests passed` across the focused parser, topology, integration, registry, and MCP acceptance suites. Shell and JP1 dry-run CLIs also passed with the argument shape emitted by incremental sync.
