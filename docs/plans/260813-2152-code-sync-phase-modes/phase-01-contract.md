# Phase 01 - Freeze CLI and Orchestration Contract

## Scope

- Add failing tests for `--sync-mode both|graph|embedding` parsing and dev-command propagation.
- Add an orchestration regression that captures child commands/environments and asserts graph -> topology -> embedding order.
- Define missing-service and invalid-combination errors.

## Acceptance

- Tests fail on the current combined analyzer execution.
- The contract preserves `both` as the default.

