# FalkorDB Docker Persistence Hardening — 2026-08-25

## Context

Running `make infra-up` misclassified healthy Qdrant and FalkorDB containers as missing every published port, then followed the stale-port branch that removes and recreates the container (`scripts/mcp-lifecycle.py:761`). FalkorDB's named volume was mounted at `/data`, while the image writes `dump.rdb` under `/var/lib/falkordb/data`; graph data therefore remained in the removed container's writable layer. Qdrant retained its collections because its volume already targeted `/qdrant/storage`. This incident corrects the persistence and idempotence assumptions documented by the [remote infra-up plan](../../plans/260818-infra-up-remote-support/plan.md#2026-08-25-persistence-hardening) and [Docker idempotence plan](../../plans/260820-infra-up-docker-idempotent/plan.md#2026-08-25-corrective-note).

## Change

- FalkorDB now mounts `cortex-falkordb-data` at the image's actual data directory, `/var/lib/falkordb/data` (`scripts/mcp-lifecycle.py:127`); the Docker command regression test rejects the old `/data` target (`tests/test_docker_ensure.py:307`).
- Published ports are read from Docker's `.NetworkSettings.Ports` JSON with the correct `<container-port>/<protocol>` key order (`scripts/mcp-lifecycle.py:594`), covered with real-shaped `3000/tcp` and `6379/tcp` fixtures (`tests/test_docker_ensure.py:147`). A running container with matching ports remains a no-op (`tests/test_docker_ensure.py:204`).
- Container readiness now checks the storage endpoint before the immediate remote probe; FalkorDB requires a Redis `PING` response on port 6379 (`scripts/mcp-lifecycle.py:697`, `tests/test_docker_ensure.py:191`).
- The lifecycle's FalkorDB probe and provisioning paths now use the driver's synchronous query boundary (`cortex_harness/storage/remote_probe.py:80`, `cortex_harness/storage/remote_probe.py:153`), so a real query failure is reported as unreachable instead of an un-awaited coroutine false positive (`tests/test_infra_remote.py:135`).

## Impact

Risk level: **high**. The fix prevents repeated `infra-up` calls from needlessly recreating healthy containers and ensures future FalkorDB graph data survives legitimate recreation. It does not recover graph data already lost with the removed writable layer; affected graphs must be restored from an available snapshot or rebuilt from source. The targeted Docker lifecycle and remote-probe suites pass: 63 tests (`tests/test_docker_ensure.py:1`, `tests/test_infra_remote.py:1`).

## Decision

Keep the existing explicit recreate path for genuinely stale port maps, but make its inputs reliable and persistence-safe. Docker's JSON inspect output was chosen over parsing a custom delimiter string because it preserves the endpoint schema without ambiguous field ordering. Redis `PING` was chosen over a plain TCP-open check for FalkorDB because readiness must prove that the storage protocol is responsive; the Browser UI remains a displayed endpoint, not the dependency readiness signal. Mounting the volume at the runtime's configured data directory was preferred over copying data between paths or relying on the container writable layer.

## References

- Plan: [remote infra-up persistence hardening](../../plans/260818-infra-up-remote-support/plan.md#2026-08-25-persistence-hardening) (`plans/260818-infra-up-remote-support/plan.md:202`)
- Plan: [Docker idempotence corrective note](../../plans/260820-infra-up-docker-idempotent/plan.md#2026-08-25-corrective-note) (`plans/260820-infra-up-docker-idempotent/plan.md:110`)
- Original Docker ensure: commit `4ad51164c29bb96952ac29fcc0aedbee8307fb77`
- FalkorDB Browser UI port-map change: commit `f2c73cfb283bffd0c8c8522a22af79a97c15609b`
