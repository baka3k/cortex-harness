# Phase 01: Contract, inventory, and reproducible baseline

## Context

The current symptom mixes three independent signals: non-fatal Tree-sitter
diagnostics, relationship progress from the previous completed batch, and a
CPU-bound FalkorDB query. Before changing behavior, freeze the identity,
schema, integrity, and performance contracts and capture a repeatable baseline.

## Requirements

- Inventory every node label, identity property, relationship endpoint pair,
  and project-scope rule emitted by shared writers and active analyzers.
- Enumerate every query template that locates an endpoint without a label or
  otherwise prevents an index scan.
- Reproduce the slow query only on a disposable FalkorDB target.
- Define provider-neutral schema/preflight result and error semantics.
- Define which relationship types permit unresolved endpoints; default is
  required/fail-closed.

## Architecture

Produce a machine-readable inventory consumed by Phase 02, plus a small
benchmark harness that creates deterministic graph sizes and records query
plans, latency, CPU, row counts, FalkorDB version/configuration, and index
state. Do not benchmark against a registered user graph.

## Related files

- `code-tiny/tools/graph/writer/*.py`
- `code-tiny/tools/{cplus,sync}/`
- `code-tiny/scripts/setup_constraints.py`
- `code-tiny/tools/graph/driver/*.py`
- New temporary-store fixtures under `tests/` or `code-tiny/tests/`

## Implementation steps

1. Extract the emitted label/relationship inventory from writer constants and
   focused fixtures; compare it with `setup_constraints.py` and analyzer-local
   index lists.
2. Classify identity as label-local `id`, project-scoped composite identity, or
   another declared key. Record duplicates and cross-project collisions.
3. Capture `GRAPH.EXPLAIN` for current node and relationship templates at 1k,
   10k, 100k, and 500k nodes.
4. Record cold/warm p50/p95/max batch latency and demonstrate the current
   all-node-scan growth without running the 20k-file repository repeatedly.
5. Freeze typed outcomes: ready, missing, building, failed, unsupported,
   duplicate-identity, unresolved-endpoint, timeout, and ambiguous mutation.
6. Add a parser-diagnostic baseline that distinguishes error files, explicit
   `ERROR`, `MISSING`, fallback, encoding, and compile-command coverage.

## Todo

- [x] Publish the complete label/identity/relationship matrix.
- [x] List every unlabeled endpoint query and its owning writer.
- [x] Capture reproducible pre-fix explain plans and latency data.
- [x] Audit current graph duplicate identities and unresolved endpoints read-only.
- [x] Freeze typed error and optional-edge policies.
- [x] Record parser diagnostics independently of database timings.

## Risks

Fixture distributions can hide the problem if IDs or labels are unrealistically
uniform. Include missing endpoints, duplicate IDs on different labels, multiple
projects, skewed popular nodes, and both Pro*C and ordinary C++ relationships.

## Success criteria

Every emitted label and relationship has an owner and identity rule; the
current failure is reproducible with an explain plan; acceptance metrics can be
collected without modifying a user graph.
