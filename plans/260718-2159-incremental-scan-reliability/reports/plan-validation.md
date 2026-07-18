---
type: plan validation
date: 2026-07-18
---

# Plan Validation: Incremental Code Scan Reliability Upgrade

## Result

**Validated and ready for implementation.** Plan status remains `pending` because no implementation has started.

## Critical Questions

| Question | Result | Evidence |
| --- | --- | --- |
| Does the plan fix the observed false lock on Windows? | Yes | Phase 02 replaces existence ownership with OS-backed `portalocker` and tests forced termination |
| Does the plan detect edits before commit? | Yes | Phase 03 covers committed, staged, unstaged, untracked, revert, delete, and rename states |
| Does it avoid rescanning the same dirty file forever? | Yes | SHA-256 inventory represents the last successfully scanned content |
| Is Git retained where it is strongest? | Yes | Git remains history/rename/delete/candidate engine; MD5 is explicitly rejected |
| Are normal monorepo modules path-correct? | Yes | Phase 04 resolves Git top-level, constrains pathspec, and emits configured-root-relative paths |
| Are nested/dirty submodules covered without parent gitlink commits? | Yes | Per-repository recursive baselines and worktree detection are required |
| Can partial submodule coverage appear as a false clean success? | No | Coverage warnings are mandatory; existing strict mode makes them fatal |
| Is invocation CWD removed from lock/state identity? | Yes | Default control cache anchors to canonical source root; explicit cache override remains |
| Is state migration conservative? | Yes | Backup + retained SHA + mandatory one-time full scan before clean v2 inventory |
| Is crash/TOCTOU consistency addressed? | Yes | Analyzer success precedes inventory publication; post-run drift blocks clean state |
| Are active plan conflicts classified? | Yes | Flutter, Perl, and ASP.NET are bidirectionally marked related; no false provider blocker added |
| Is rollback possible? | Yes | State v1 backup and one compatibility window for `--change-detection committed` |

## Completeness Check

- [x] Root-cause evidence is traceable to current source.
- [x] Scope and out-of-scope boundaries are explicit.
- [x] Architecture decisions cover lock, scope, inventory, Git, module, submodule, migration, and race behavior.
- [x] Six implementation phases have related files, ordered steps, risks, todos, and success criteria.
- [x] Red-team challenges have dispositions.
- [x] Cross-plan coordination is bidirectional for active overlapping plans.
- [x] Test and rollout matrices include Windows and POSIX gates.
- [x] Documentation and operator observability are included.
- [x] No implementation or graph-schema change is hidden inside the plan.

## Phase Dependency Order

```text
01 contracts/fixtures/state
  -> 02 stable scope + lock
  -> 03 hybrid detector + inventory
  -> 04 module/submodule topology
  -> 05 CLI/summary/docs
  -> 06 validation/rollout
```

Phase 02 can begin helper implementation after Phase 01 freezes scope/state tests. Phases 03 and 04 share state/topology contracts and should remain sequential to avoid parallel edits to `git_diff.py` and `incremental_sync.py`. Phase 05 begins after behavioral fields stabilize. Phase 06 is the release gate.

## Task Hydration

Each phase file contains implementation-ready unchecked Todo items. No repository task-creation API is available in this environment, so persistent phase Todos are the authoritative hydrated tasks; the session plan tracks creation/review only.

## Unresolved Implementation Choices

- Inventory generation storage may remain JSON or move to SQLite only if Phase 01 benchmarks demonstrate unacceptable generation size/write amplification.
- Remote/shared cache support level is finalized after filesystem-specific lock validation.
- Compatibility-window duration for `--change-detection committed` is a release-management decision, not an architecture blocker.

