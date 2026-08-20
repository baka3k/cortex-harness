# Phase 01 — Docker Ensure Module

## Goal

Thêm `_ensure_docker_services()` + helpers vào `scripts/mcp-lifecycle.py`, wire
vào `invoke_infra_up()` / `invoke_infra_down()`.

## Tasks

- [ ] Định nghĩa `DOCKER_SERVICES` spec dict: name, image (env-overridable), ports (bind 127.0.0.1), volume, restart policy.
- [ ] `_docker_available()` — `docker info` exit-code check.
- [ ] `_container_state(name)` — `docker inspect --format {{.State.Status}}` → `"running"` | `"stopped"` | `None` (not found) | daemon error.
- [ ] `_ensure_service(spec)` — state machine theo plan.md:
  running → in `[ok] ... running`; stopped → `docker start`; not found →
  `docker image inspect` rồi chỉ pull khi thiếu → `docker run -d`.
- [ ] `_ensure_docker_services()` — loop 2 services; daemon unreachable → in `[warn]`, return không raise.
- [ ] `invoke_infra_up()`: gọi `_ensure_docker_services()` sau `ensure_layout`, trước remote probe.
- [ ] `invoke_infra_down()`: `docker stop` idempotent cho cả 2 container (bỏ qua nếu không tồn tại / docker off), giữ `reset_remote_clients()`.
- [ ] Update help text (line ~93) + ReadMakefile help nếu có nhắc infra-up args.

## Risks (red-team)

- **Port conflict**: user đã chạy Qdrant/FalkorDB native trên 6333/6379 → `docker run` bind fail. Xử lý: nếu `docker run` fail vì bind, in `[fail] port in use — service có thể đã chạy native, skip` và tiếp tục (probe phía sau sẽ xác nhận).
- **Name collision**: container `cortex-qdrant` đã tồn tại nhưng là container khác của user → vẫn dùng luôn theo đúng yêu cầu "có rồi thì dùng luôn"; ghi chú trong docs.
- **`latest` tag**: chấp nhận, override qua env khi cần pin.

## Notes

- Mọi lệnh docker qua `run(..., capture=True, check=False)`.
- In ra URL tiện dụng: Qdrant `http://127.0.0.1:6333`, FalkorDB UI `http://127.0.0.1:3000`.
