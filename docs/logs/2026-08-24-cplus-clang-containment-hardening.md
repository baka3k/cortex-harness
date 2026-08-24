# C/C++ Clang containment and signature-v2 hardening — 2026-08-24

## Context

The legacy C/C++ recovery path could replace Tree-sitter structure with a
whole-file LIBCLANG payload when incomparable diagnostic counts appeared
better. The containment plan freezes Tree-sitter as the only structural owner,
then requires overload-safe identities and a falsifiable structural
differential before any Clang semantic evidence can advance
(`plans/260824-1411-cplus-clang-containment-hardening/plan.md:24`).

## Change

- Parse-quality policy/schema v2 names cross-backend structural selection as a
  forbidden outcome (`code-tiny/tools/common/parse_quality.py:19`), while the
  protocol-1 recovery worker now records diagnostic-only completion and always
  retains the baseline Tree-sitter payload
  (`code-tiny/tools/cplus/parse_recovery.py:659`).
- Legacy LIBCLANG structural payloads are quarantined as a whole before graph
  mutation (`code-tiny/tools/common/payload_validation.py:443`), the C/C++
  cache identity is cut over to the Tree-sitter-only policy
  (`code-tiny/tools/cplus/cplus_analyzer.py:102`), and incompatible generations
  can be durably fenced from load or publication
  (`cortex_harness/storage/generation.py:48`).
- The shared `cplus-function-v2` identity encodes normalized parameter types,
  qualifiers, template arity, linkage, and only the required discriminator;
  project/generation remain physical storage coordinates
  (`code-tiny/tools/cplus/function_identity.py:12`,
  `code-tiny/tools/cplus/function_identity.py:33`). Both the legacy adapter and
  protocol-2 semantic worker now build endpoints through that contract
  (`code-tiny/tools/cplus/clang_parser.py:286`,
  `code-tiny/tools/cplus/semantic_worker.py:311`).
- The shadow path now emits a deterministic four-stage structural differential
  that preserves multiplicity and stable node/relation properties, classifies
  adapter loss and identity collisions, and fails closed on validated or
  persisted drift (`code-tiny/tools/cplus/semantic_shadow.py:147`,
  `code-tiny/tools/cplus/semantic_shadow.py:256`,
  `code-tiny/tools/cplus/semantic_shadow.py:391`). Regression coverage includes
  overload joins, type/kind/property drift, duplicate identities, adapter loss,
  and persisted shadow artifacts (`tests/test_cplus_clang_differential.py:33`,
  `tests/test_cplus_clang_differential.py:131`).

## Impact

Risk level: **high**. C/C++ structural ownership, cache compatibility, function
identity, and generation eligibility all change. The safety posture improves:
Clang can add typed semantic observations but cannot remove, replace, or mutate
canonical Tree-sitter structure, and unexplained differential loss is blocking.
Existing LIBCLANG-structured generations must remain fenced and signature-v2
requires a clean-generation cutover. The focused suite reports 215 passing
tests plus 10 passing subtests
(`plans/260824-1411-cplus-clang-containment-hardening/reports/implementation-status.md:34`).
Mandatory review exhausted its three-cycle cap at 9.0/10 with no critical
finding; its final callsite-identity precedence finding was corrected and the
focused suite rerun afterward.

## Decision

Keep the rollout at **RECONVENE**. Phases 1–2 establish containment and adapter
contracts, but Phases 3–5 remain owned by unfinished concurrency, graph-write,
and provider-adapter plans (`plans/260824-1411-cplus-clang-containment-hardening/plan.md:11`).
No live provider target or immutable promotion horizon was authorized, so no
semantic generation was published, activated, rolled back, deleted, or
relabeled current
(`plans/260824-1411-cplus-clang-containment-hardening/reports/implementation-status.md:43`).
Whole-payload Clang replacement and name/arity identity were rejected because
they cannot preserve structural ownership or overload uniqueness; inventing a
second scheduler/publisher was rejected because it would bypass the declared
single-owner dependencies.

## References

- plan: ./plans/260824-1411-cplus-clang-containment-hardening/plan.md
- plan baseline commit: beedc01f06855bcb3ee90f2ec50e36112076d11d
- implementation baseline commit: 34894098e9ffdfddd7c72521ae5e142702a81415
- implementation status: plans/260824-1411-cplus-clang-containment-hardening/reports/implementation-status.md:1
