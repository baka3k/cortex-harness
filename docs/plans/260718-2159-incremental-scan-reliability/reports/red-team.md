---
type: red-team review
date: 2026-07-18
---

# Red-Team Review: Incremental Code Scan Reliability Upgrade

## Verdict

**GO with recorded safeguards.** The draft addresses the confirmed root causes and does not require graph-provider changes. The review found no unresolved architecture blocker, but completion must remain gated on Windows/POSIX crash tests and representative inventory benchmarks.

## Challenges and Dispositions

| Objection | Severity | Disposition in final plan |
| --- | --- | --- |
| Hashing an entire monorepo every run can create an I/O spike | High | Git remains candidate accelerator; full SHA is reconciliation/hash mode. Phase 06 records files hashed and benchmark results |
| A cross-platform file-lock library does not provide distributed consensus on every SMB/NFS configuration | High | Cache is root-local by default; remote cache is warned/documented and environment-gated |
| PID-based reclaim can steal a live lock after PID reuse | High | Rejected. `portalocker` OS ownership is authoritative; PID metadata is diagnostic only |
| Adding only Git status causes an unchanged dirty file to scan forever | High | A published SHA-256 inventory is the successful content baseline |
| State v1 contains no inventory and may already have skipped dirty/submodule files | High | Migration preserves the old SHA/backup but requires one conservative full scan before clean v2 state |
| Inventory written after analysis could record content the analyzer did not read | Critical | Pre/post stat/hash validation; drift leaves state dirty and requests rescan |
| Inventory published before analyzers finish could legitimize partial writes | Critical | New inventory generation is created only after required analyzers succeed; state pointer advances last |
| Parent and explicitly configured child roots can double-ingest files with independent locks | High | Root-overlap detection deduplicates child runs as `covered_by_parent` |
| Recursive submodules can be sparse, uninitialized, conflicted, LFS-backed, or missing historical commits | High | Coverage diagnostics are mandatory; partial by default, fatal under `--strict`; no automatic mutation |
| Git line parsing is unsafe for tabs, quoting, Unicode, and rename records | High | New collector uses NUL-delimited status output and path-domain tests |
| `normcase` applied globally would break Linux case-sensitive identity | Medium | Apply only to internal identity on Windows; retain display/open path casing |
| Full scan and inventory could retain different extension/exclusion policies | High | Version the filter contract and force reconciliation after policy changes |
| Changing exit code 2 handling globally can alter unrelated commands | Medium | Make non-retryable codes configurable/scoped to code sync |
| A single huge JSON inventory can cause write amplification | Medium | Use immutable generations with benchmark/size guard; select storage representation in Phase 01 based on evidence, not speculative SQLite migration |
| Pre/post validation cannot guarantee perfect snapshot isolation | Medium | Document eventual consistency; do not claim immutable snapshot semantics |

## Residual Risks

- Remote filesystem behavior remains an environment exclusion until tested on the user's actual storage.
- Recursive submodules can materially increase first migration/full-scan time.
- Default hybrid behavior intentionally scans more than the legacy commit-only flow; additive summary provenance and one compatibility mode are required for rollout.
- Existing repository-wide tests have environment-dependent parser failures; validation must distinguish baseline exclusions from new regressions.

## Required Acceptance Gates

- Forced owner termination followed by immediate lock acquisition on Windows and POSIX.
- Dirty/untracked/revert/re-edit matrix passes without repeated unchanged work.
- Module and nested submodule fixtures pass with parent gitlink unchanged.
- State v1 cannot become clean without the migration bootstrap.
- Mid-run source changes cannot advance baseline/inventory.
- Inventory complexity and hash counts match the plan's bounded behavior.

