# infra-up Docker Idempotent Lifecycle — Qdrant & FalkorDB — 2026-08-20

## Context

Plan `plans/260820-infra-up-docker-idempotent/plan.md` continues the lifecycle work from `plans/260818-infra-up-remote-support`. After the docker-free cutover, `make infra-up` only ensured local layout and probed/provisioned remote servers — Docker support had been stripped, so operators with `storage_backend: "remote"` projects pointing at `http://127.0.0.1:6333` / `redis://127.0.0.1:6379` had no way to bring those local services up from the lifecycle. The new requirement: `infra-up` must idempotently ensure the Qdrant + FalkorDB containers (with FalkorDB Browser UI) are running on `127.0.0.1`, so a second or third invocation does not re-pull or re-create anything that is already healthy.

## Change

- `scripts/mcp-lifecycle.py:68` introduces `DOCKER_SERVICES`, a tuple of container specs (name, image env-var + default, host/container port pairs pinned to `127.0.0.1`, primary URL port for status reporting, named volume).
- `scripts/mcp-lifecycle.py:431` adds `_docker_available` (`docker info` exit-code check, never raises), `_container_state` (`docker inspect --format '{{.State.Status}}'` → `"running"` / `"exited"` / `None`), `_image_present` (`docker image inspect`, used to avoid redundant pulls), and `_resolved_ports` (parses env overrides with graceful fallback).
- `scripts/mcp-lifecycle.py:497` adds `_ensure_service`, the state machine per the plan: running → no-op, stopped/exited → `docker start`, missing → optional `docker pull` then `docker run -d --restart unless-stopped` with the named volume. Bind failures on `docker run` are reported but non-fatal so a natively running Qdrant/Falkor on the host port is left alone for the remote probe to discover.
- `scripts/mcp-lifecycle.py:547` adds `_ensure_docker_services`, which warns and returns when the docker daemon is unreachable (so purely-local hosts do not crash) and swallows per-service exceptions so one bad service does not break the other.
- `invoke_infra_up` (`scripts/mcp-lifecycle.py:579`) now calls `_ensure_docker_services` after `ensure_layout` and before the remote probe. `invoke_infra_down` (`scripts/mcp-lifecycle.py:634`) stops the managed containers idempotently (no-op when docker is off or containers are missing) and then closes cached remote clients as before.
- `USAGE` (`scripts/mcp-lifecycle.py:122`) updated so `make infra-up` and `make infra-down` describe the Docker lifecycle. The legacy wording about one-release compatibility aliases was removed.
- `tests/test_docker_ensure.py:1` adds 21 unit tests covering every state-machine branch (running, stopped, missing+image-cached, missing+image-absent, env image/port overrides, invalid port fallback, pull failure, run bind failure, run unexpected failure, start failure, daemon-unreachable warn-and-continue, per-service exception isolation, infra-up integration). `tests/test_make_lifecycle.py` was extended with `test_infra_up_invokes_docker_ensure_after_layout`, `test_infra_down_stops_managed_containers_when_docker_is_available`, `test_infra_down_silently_skips_missing_containers`, and existing infra-up/infra-down tests were updated to mock `_ensure_docker_services` / `_docker_available` so they stay deterministic when the host has a running docker daemon.
- `ReadMe.md:86` documents the new idempotent behavior, env overrides, port pinning, daemon-unreachable warning, and port-bind conflict semantics.

## Impact

Impact level: medium. Operators running remote projects can now point their config at `http://127.0.0.1:6333` / `redis://127.0.0.1:6379` and trust `make infra-up` to bring the local containers up — no separate Docker Compose file or hand-run `docker run` invocations. Idempotence is verified by the unit tests (no `docker pull` / `docker run` / `docker start` fire on subsequent calls when the containers are already healthy) and the manual acceptance checklist in `phase-02-testing.md`. Local-only hosts without docker are unaffected: the warn-and-continue branch keeps `ensure_layout` running so `storage-init` semantics are preserved, and any remote project pointing at `127.0.0.1` simply fails the subsequent reachability probe with a clear message. The named volumes (`cortex-qdrant-storage`, `cortex-falkordb-data`) keep storage alive across container recreation. No new credentials, ports exposed to the network, or background processes are introduced — ports stay pinned to `127.0.0.1`.

## Decision

The state machine uses plain `docker` CLI calls (`inspect` / `start` / `run` / `pull`) rather than a wrapper like docker-compose or a custom config file. That keeps the lifecycle script self-contained, makes every transition observable via the `[ok]` / `[fail]` output, and matches the rest of the lifecycle's "use the simplest tool that works" style. The FalkorDB Browser UI rides along for free because the `falkordb/falkordb` image exposes port 3000 — no second container is needed. Port pinning to `127.0.0.1` (rather than `0.0.0.0`) was a deliberate security choice: it keeps the services unreachable from other machines on the LAN. Override behavior is env-driven (not argparse) so CI scripts can set `QDRANT_IMAGE=qdrant/qdrant:v1.13.0` without touching the lifecycle entry points.

## References

- plan: `plans/260820-infra-up-docker-idempotent/plan.md:1`
- related plan: `plans/260818-infra-up-remote-support/plan.md:1` (extended infra-up/infra-down with Docker lifecycle)
- related plan: `plans/260817-storage-backend-adapter/plan.md:1` (introduced the remote Qdrant + FalkorDB adapter layer)
- tests: `tests/test_docker_ensure.py:1`, `tests/test_make_lifecycle.py:1`
