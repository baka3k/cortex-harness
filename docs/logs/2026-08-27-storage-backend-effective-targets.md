# Storage backend effective targets — 2026-08-27

## Context

The local/remote adapter introduced per-project Qdrant and FalkorDB routing, but
the node-first journal amendment required compatibility to describe the targets
a run actually mutates, not only the requested `storage_backend` value. Mixed
fallback, endpoint changes, and force-local operation must not collapse to one
journal or generation identity (`plans/260817-storage-backend-adapter/plan.md:24`,
`plans/260817-storage-backend-adapter/plan.md:33`).

## Change

The storage layer now models credential-free graph and vector targets with
canonical local paths or normalized remote endpoints, namespace, role, TLS, and
non-secret principal identity, then derives versioned component and topology
fingerprints (`cortex_harness/storage/targets.py:73`,
`cortex_harness/storage/targets.py:79`,
`cortex_harness/storage/targets.py:123`,
`cortex_harness/storage/targets.py:216`). The factory resolves both components
before connection attempts, makes a missing remote component an explicit local
member of a mixed topology, and records force-local as a distinct topology
(`cortex_harness/storage/factory.py:313`,
`cortex_harness/storage/factory.py:346`,
`cortex_harness/storage/factory.py:373`). Validated launch overlays propagate
the sanitized descriptors and fingerprints to child processes
(`cortex_harness/storage/config.py:500`).

Generation publication and `StoreGateway` retain filesystem paths for local
lease ownership while carrying effective graph/vector compatibility separately
(`cortex_harness/storage/generation.py:56`,
`cortex_harness/storage/gateway.py:91`). Publication revalidates the reserved
compatibility envelope after caller validation and before the atomic pointer
write, preventing a mutable validation callback from retargeting a generation
(`cortex_harness/storage/generation.py:209`). A generation manifest without the
effective-topology envelope is rejected unchanged and requires source
re-ingestion; it is not upgraded in place
(`cortex_harness/storage/generation.py:180`,
`tests/test_storage_effective_targets.py:494`).

Independent review findings were resolved with fail-closed live-descriptor
validation, credential exclusion, path-safe lease keys, post-callback
revalidation, topology fencing, and regression coverage for stale descriptors,
mixed and force-local modes, cross-endpoint publication, legacy generations,
and gateway lease placement (`cortex_harness/storage/targets.py:375`,
`tests/test_storage_effective_targets.py:169`,
`tests/test_storage_effective_targets.py:189`,
`tests/test_storage_effective_targets.py:280`,
`tests/test_storage_effective_targets.py:369`,
`tests/test_storage_effective_targets.py:469`,
`tests/test_storage_effective_targets.py:509`,
`tests/test_storage_effective_targets.py:535`).

## Impact

**Risk level: high.** Backend selection, journal resumption, generation
publication, and local lease ownership now share one canonical target contract.
Switching a path, endpoint, graph, collection, role, TLS mode, mixed component,
or force-local state cannot resume or publish work for the previous topology.
Credentials and URI userinfo are excluded from persisted descriptors, while
pre-contract active generations require an intentional re-ingest. The
operational switching procedure remains stop, reconfigure, validate, and
re-ingest; there is no dual-write or cross-target replay
(`docs/DATABASE_INTEGRATION.md:77`, `docs/DATABASE_INTEGRATION.md:82`,
`docs/DATABASE_INTEGRATION.md:124`).

## Decision

Compatibility is based on effective per-component targets because the requested
backend alone cannot distinguish mixed routing or emergency override behavior.
Remote URLs are kept out of `PhysicalTargetKey` so filesystem lease semantics
remain valid; topology fingerprints are carried separately in generation
metadata. Legacy manifests are rejected rather than inferred or rewritten
because their original effective target cannot be proven. Runtime connection,
authentication, and timeout failures therefore remain errors after resolution,
not triggers for silent local fallback.

## References

- Plan: [Storage backend adapter](../../plans/260817-storage-backend-adapter/plan.md) (`plans/260817-storage-backend-adapter/plan.md:24`)
- Base local/remote adapter: `94adf9f0738e2ee42f7b9ceed5b4631cc0845abd`
- Canonical target and topology implementation: `95772b312f3c41d78a36ac69d2058c7c33997268`
- Remote Qdrant construction hardening: `c209a54d7bbc844ad0d8aee1843606615a569251`
- Descriptor validation and generation fencing: `064edeaf45ef35b1ee1ec1d49fd98c4ed576f0fc`
- Gateway topology adoption: `a49ae3379b3d264b00ca2bba0f33fd480c04be1f`
- Review regression coverage: `a6eeddb79b21a2f831901ff3fafd8f3a2210bfc9`
- Fail-closed legacy generation correction: `3f5df3a504e77b723796b0f580b9a03229713341` (storage-generation portion only; concurrent journal retry and retention policy is outside this event)
