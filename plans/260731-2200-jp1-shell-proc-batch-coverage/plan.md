---
title: "JP1/Shell/Pro*C/INI Batch-Chain Coverage (Java Migration Source)"
status: draft
created: 2026-07-31
mode: hi-plan
source: gap analysis vs /Users/hieplq1.rpm/JavaMigration/REDACTED sample tree (00.CustomerSupply)
target: code-tiny/tools/ (cplus + new tools/shell, tools/jp1, tools/batchconfig) + project_topology registry + common/message_scan.py + common/source_inventory.py + mcp/framework_registry.py
scope: >
  Extend parser coverage so the full mainframe/UNIX batch chain used by REDACTED
  (JP1 job-net definition -> shell script -> Pro*C compiled program -> Oracle DB,
  with .ini key:value config and .dat data/delivery folders) is discovered, parsed,
  graph-indexed, and traceable end-to-end for Java migration impact analysis.
blockedBy: []
relatedPlans:
  [
    260731-1030-rust-extraction-layer,
    260731-1700-multi-language-rust-extraction,
    260714-1702-cobol-analyzer-parser,
    260725-1703-project-topology-context-tools,
  ]
---

# JP1/Shell/Pro*C/INI Batch-Chain Coverage

## Problem Statement

User asked whether the current C++ parser (`cplus_analyzer.py`) already covers a
real customer source tree (`JavaMigration/REDACTED/00.CustomerSupply`) that mixes:

| Sample file | Type | Currently covered? |
|---|---|---|
| `02.Cソース/.../BZZAAB01.c` | plain C | ✅ yes (`.c` in cplus extension set) |
| `02.Cソース/.../BZZAAB02.pc` | **Pro*C** (`EXEC SQL` embedded SQL, Oracle precompiler) | ❌ **no** — `.pc` is not in any extension map |
| `00.JP1定義/ジョブネット/BATCH/ALL/CWV/JBCWV013_ALL.txt` | **JP1/AJS job-net unit definition** (`unit=...{ty=n; el=...; ar=...; te="...sh"}`) | ❌ **no** — no JP1 parser/tool exists |
| `01.SHELL/00.sh/ALL/BBSEAB01.sh` | POSIX shell batch script | ❌ **no** — no shell analyzer registered anywhere |
| `03.INI/env/ALL/BBSEAB01_06_01.ini` | pseudo-INI, `KEY:VALUE` lines (not `[section]`) | ❌ **no** — no ini/config tool, and format differs from standard INI |
| `04.DAT/...` | delivery/output data folders | ❌ not modeled (acceptable — no code to parse, needs only resource/path registration) |

## Evidence Gathered

- `code-tiny/tools/cplus/cplus_analyzer.py` hardcodes file discovery to
  `.c .cc .cpp .cxx .h .hh .hxx .hpp (+ .rc .rc2)` (`_is_cpp_file`, directory
  walk filter ~L2495). **No `.pc` anywhere in the tool.**
- `code-tiny/tools/common/message_scan.py::_PARSER_EXTENSIONS` (the second,
  independent extension→language map used for message/IPC scanning) also has
  no `.pc`, no shell, no ini/JP1 entry.
- `code-tiny/tools/project_topology/registry.py::PRIMARY_SPECIAL_FILE_COVERAGE`
  has entries per language (`cobol`, `cplus`, `csharp`, ...) but nothing for
  shell/JP1/ini.
- Only `code-tiny/tools/cobol/parser.py` has any `EXEC SQL` handling, and it is
  COBOL-specific (`EXEC SQL ... END-EXEC`), not reusable as-is for Pro*C's
  C-embedded `EXEC SQL ...;` / `EXEC ORACLE ...;` directives.
- Sample `.pc`/`.sh`/`.ini`/JP1 `.txt` files are **Shift-JIS/CP932 encoded**
  (confirmed by reading raw bytes — Japanese comment headers render as mojibake
  under naive UTF-8 decode). The repo already has a proven fallback-decode
  pattern in `code-tiny/tools/cplus/rc_parser.py` (`try utf-8, then cp932`,
  plus a UTF-16 BOM check) — reuse this, do not invent a new one.
- Shell scripts read config via `grep 'KEY' file.ini | awk -F: '{print $2}'`,
  i.e. the `.ini` format is `KEY:VALUE` per line — **not** `configparser`
  `[section]key=value` syntax. A generic INI parser would silently return
  nothing useful; a dedicated key:value line parser is required.
- JP1 unit-definition files have no reliable extension (`.txt` here, but JP1
  exports are often extensionless or `.def`) — detection must be
  content-sniffed (`^unit=` at file start) rather than extension-based.
- The JP1 `te="@BOSAPDIR@/sh/ALL/XXX.sh"` field is the cross-link from a
  job-net leaf unit to the shell script it executes — this is the key edge for
  batch-chain tracing (job-net → shell). The shell script in turn invokes the
  Pro*C-compiled executable (same base name, e.g. `BZZAAB02` binary built from
  `BZZAAB02.pc`) and reads `.ini` config by convention
  (`${BZZ_BT_ENVPATH}/${KEY}-${KEY}.ini`).

## Cross-Plan Dependency

`260731-1030-rust-extraction-layer` (draft) and `260731-1700-multi-language-rust-extraction`
(in-progress) are actively migrating the **cplus** extraction/parse layer to
Rust (`rust-analyzer-core`). This plan's Phase 1 (Pro*C support) touches the
same file (`cplus_analyzer.py`) and the same conceptual boundary (file
discovery + pre-parse preprocessing happens **before** the tree-sitter/clang
walk, so it is backend-agnostic). To avoid churn conflicts:

- Phase 1 only touches **file discovery / extension registration** and a new
  **pre-parse text preprocessor** (`_preprocess_proc_directives`), not the
  AST-walk/extraction internals that the Rust plans are porting.
- Both `260731-1030-rust-extraction-layer/plan.md` and
  `260731-1700-multi-language-rust-extraction/plan.md` `relatedPlans` should
  list this plan so the Rust port carries Pro*C preprocessing forward when it
  eventually ports `cplus` fully (tracked as a follow-up note, not a new phase
  here).

## Goal

Enable full batch-chain trace:

```
JP1 job-net unit (.txt, ty=n)
   └─ el/ar edges → leaf unit (ty=j, te="...sh")
        └─ EXECUTES → shell script (.sh)
             └─ CALLS → Pro*C-compiled program (.pc source)
                  └─ EXEC_SQL → Oracle table/procedure (existing sql/plsql analyzers)
             └─ READS_CONFIG → .ini key (KEY:VALUE)
```

so that Java-migration impact analysis can answer "if I change this DB table /
this .pc program, which JP1 job-nets and shell scripts are affected" — the
same class of query `get_api_call_chain` / `find_callers_of_endpoint` already
answer for web endpoints.

## Phase Breakdown

- [phase-01-proc-support.md](phase-01-proc-support.md) — `.pc` (Pro*C) discovery + EXEC SQL preprocessing + CP932 decode in `cplus_analyzer.py`
- [phase-02-shell-analyzer.md](phase-02-shell-analyzer.md) — new `tools/shell` analyzer for `.sh` batch scripts
- [phase-03-jp1-analyzer.md](phase-03-jp1-analyzer.md) — new `tools/jp1` analyzer for JP1/AJS unit-definition files
- [phase-04-ini-dat-coverage.md](phase-04-ini-dat-coverage.md) — `KEY:VALUE` ini parser + `.dat` resource registration
- [phase-05-batch-chain-trace.md](phase-05-batch-chain-trace.md) — cross-language edges + graph/MCP trace wiring

## Non-Goals

- Not building a full JP1/AJS GUI-parity parser (only the unit-definition
  export subset seen in samples: `unit=`, `ty=`, `cm=`, `el=`, `ar=`, `te=`,
  `sd=`, timing fields as opaque attributes).
- Not writing an Oracle Pro*C precompiler (no `EXEC SQL` execution/validation) —
  only symbol/host-variable/statement-kind extraction for graph edges.
- `.dat` binary/delivery folders get path/resource nodes only, no content parsing.

## Risks & Open Questions (red-team pass)

- **Byte-length-preserving preprocessing (Phase 1) is fragile.** If an
  `EXEC SQL` statement's replacement text is longer/shorter than the
  original, downstream line/column numbers for everything after it in the
  file shift, silently corrupting symbol locations. Mitigation: pad with
  spaces to match original byte length exactly, and add a regression test
  asserting a known symbol *after* an `EXEC SQL` block keeps its original
  line number.
- **JP1 detection by content-sniff (`^unit=`) risks false positives** on
  unrelated `.txt`/config files that happen to start similarly. Mitigation:
  require the sniff to also find at least one nested `{ ... ty=... }` block
  within the first N bytes before classifying as JP1.
- **Templated `.ini` path resolution (Phase 4) is inherently approximate**
  (`${VAR}` interpolation isn't evaluated, only pattern-matched to a
  directory scope). This trades precision for coverage — acceptable per user
  goal (batch-chain *tracing* for impact analysis, not exact runtime
  simulation), but should be clearly labeled `note="templated..."` in the
  graph so consumers don't mistake it for a resolved reference.
- **Cross-plan touch conflict on `cplus_analyzer.py`**: Phases 1 and the
  Rust-extraction plans (`260731-1030`, `260731-1700`) both modify this file.
  Mitigated by scoping Phase 1 to file-discovery + pre-parse preprocessing
  only (not the AST-walk/extraction internals being ported to Rust) and by
  the bidirectional `relatedPlans`/note added to both Rust plans.
- **Shell/Pro*C name-matching for cross-language `CALLS` edges (Phase 5) is
  best-effort (filename-stem matching)**, not a real linker/build-graph
  resolution. Acceptable for impact-analysis tracing but should be flagged as
  `confidence=heuristic` on the edge, distinct from `confidence=verified`
  edges resolved from `compile_commands.json` elsewhere in the cplus pipeline.
- **Scope is large (5 phases, 4 new/extended tools).** If time-boxed, Phase 1
  (Pro*C) delivers the most value alone (unblocks C++ coverage claim), and
  Phase 5 (trace wiring) has the least value without Phases 2–4 already done
  — recommended execution order matches phase numbering; do not skip ahead
  to Phase 5.

## Validation Interview Summary (scope confirmed with user)

1. **Scope priority**: cover all 4 gaps (Pro*C, JP1, Shell, INI/DAT) — confirmed.
2. **Pro*C integration approach**: extend existing `cplus_analyzer.py` rather
   than a standalone tool — confirmed.
3. **Primary objective**: full batch-chain trace (JP1 → shell → Pro*C → DB),
   not just basic symbol indexing — confirmed, drives Phase 5's inclusion.
4. **Encoding/INI handling**: CP932/Shift-JIS auto-detect and a custom
   `KEY:VALUE` ini parser are both required (not deferred) — confirmed, drives
   Phase 1.2 and Phase 4.1.
