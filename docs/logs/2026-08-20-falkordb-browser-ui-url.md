# FalkorDB Browser UI URL in infra-up and doctor — 2026-08-20

## Context

The `falkordb/falkordb:latest` image already ships the Browser UI on port 3000,
and `DOCKER_SERVICES` already mapped it — but the output made it unusable:

- `infra-up` printed a single primary URL per container, and for
  `cortex-falkordb` that primary is the **UI** port (`primary_port_index: 1`).
  So `[ok] cortex-falkordb running (http://127.0.0.1:3000)` gave no hint that
  this was the UI, and never showed the redis port 6379 at all.
- `dev doctor` on a `storage_backend: remote` project printed only the raw
  `falkordb_uri` (e.g. `localhost:6379`), so the user had no UI address to open.

No code read `FALKORDB_UI_PORT` outside the docker port spec
(`plans/260820-falkordb-ui-url-doctor/plan.md:41`).

## Change

- Added an optional `endpoints` spec key — `(label, scheme)` per port, aligned
  with `ports` — declared only for `cortex-falkordb`
  (`scripts/mcp-lifecycle.py:105`).
- `_endpoint_lines` renders one indented `label : url` line per endpoint and
  returns `[]` for specs without labels, so Qdrant is untouched
  (`scripts/mcp-lifecycle.py:557`). `zip(..., strict=True)` so a port added
  without a matching label raises instead of silently vanishing from output.
- `_report_ready` prints the state message plus the breakdown, wired into all
  three reach-running branches — already-running, restarted, newly created
  (`scripts/mcp-lifecycle.py:577`).
- `_falkordb_ui_url` derives the UI host from a falkordb URI, mirroring the
  parsing in `falkordb_driver.py` (`scripts/mcp-lifecycle.py:966`): scheme form
  (`falkor/falkors/redis/rediss`) or bare `host:port`, userinfo and path
  stripped, bracketed IPv6 preserved. Returns `None` — no hint — for `unix://`,
  unknown schemes, empty host (`:6379`), and bare unbracketed IPv6 (`::1`).
- `doctor_remote_checks` appends `— Browser UI: <url>` to the falkordb check
  message only when the probe is reachable (`scripts/mcp-lifecycle.py:1056`).
- Factored the port-env fallback into `_env_port` (`scripts/mcp-lifecycle.py:528`)
  so `_resolved_ports` and the new `falkordb_ui_port()` share one definition of
  "invalid value falls back to default" and cannot drift on `FALKORDB_UI_PORT`.
- Tests: `tests/test_docker_ensure.py:353` (`TestEndpointOutput`),
  `tests/test_docker_ensure.py:441` (`TestFalkordbUiPort`),
  `tests/test_doctor_remote.py:181` (`TestFalkordbUiUrl`),
  `tests/test_doctor_remote.py:232` (`TestDoctorBrowserUiHint`).
- Docs: `ReadMe.md:90` now shows the sample output block and documents that
  `FALKORDB_UI_PORT` drives the advertised URL in both commands.

## Impact

Risk level: **low** — additive output only; no state machine, probe, or check
verdict changed.

- `infra-up` for falkordb now prints both endpoints under the status line;
  Qdrant output is byte-identical to before (pinned by
  `test_qdrant_output_has_no_endpoint_lines`).
- Remote-project doctor output gains a clickable UI URL on reachable falkordb.
- Verified end-to-end against a real on-disk remote config: unreachable →
  no hint; reachable (driver faked at the network boundary) →
  `remote:ui_demo:falkordb - redis://localhost:6379 — reachable — Browser UI:
  http://localhost:3001` with `FALKORDB_UI_PORT=3001` honored.
- 89 tests pass across `test_doctor_remote.py`, `test_docker_ensure.py`,
  `test_infra_remote.py`. The 2 failures in `test_dev_lifecycle_commands.py`
  are pre-existing — confirmed identical on a stashed baseline tree.
- `dev doctor` on this repo still reports "Required checks passed."

## Decision

**UI URL is informational, never probed.** A remote host is under no obligation
to publish port 3000, so a probe would produce failing checks for a working
deployment. The hint rides along in the existing check's message instead of
becoming a check of its own (`plans/260820-falkordb-ui-url-doctor/plan.md:51`).

**No image change.** The requirement "use a falkordb build with UI" was already
satisfied; only the output needed work. Alternative considered and rejected:
switching to a separate UI image, which would add a container for no gain.

**Hint derives from `result.url`, not `remote_config.falkordb_uri`.** They are
the same string by construction (`remote_probe.py:76`), but deriving from the
URL actually being displayed means the shown host and the advertised UI host
cannot disagree.

**Attached to reachable only.** An unreachable falkordb almost certainly has no
UI either; advertising one would be misleading. Pinned by
`test_unreachable_falkordb_has_no_ui_url`.

Full-mode code review found three real defects, all fixed before commit:
`zip()` truncation on label/port mismatch; `_falkordb_ui_url` returning
malformed URLs (`http://:6379:3000`, `http://[::3000`) instead of `None` for
four edge inputs; and `mock.patch.dict("os.environ", {}, clear=False)` being a
no-op that left tests exposed to the developer's exported ports.

## Known gap (pre-existing, not addressed)

`doctor_remote_checks` interpolates `result.url` verbatim
(`scripts/mcp-lifecycle.py:1050`), so a password embedded in the userinfo of a
`falkordb_uri` is printed in doctor output. This predates
this change — the new UI hint strips userinfo correctly — and scrubbing the
existing field is a behaviour change to an already-displayed value, so it is
left for a dedicated change rather than folded in here.

## References

- Plan: [FalkorDB Browser UI URL](../../plans/260820-falkordb-ui-url-doctor/plan.md)
- Cross-link: [infra-up Docker idempotent lifecycle](2026-08-20-infra-up-docker-idempotent.md)
- Cross-link: [doctor caller-aware scan](2026-08-20-doctor-caller-aware-scan.md)
- Commit: `f2c73cf`
