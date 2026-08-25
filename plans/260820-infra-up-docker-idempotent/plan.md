---
title: "infra-up Docker Idempotent Lifecycle — Qdrant & FalkorDB Containers"
status: completed
created: 2026-08-20
updated: 2026-08-25
mode: hi-plan (full)
scope: scripts/mcp-lifecycle.py, Makefile, tests, docs
relatedPlans:
  - 260818-infra-up-remote-support
  - 260817-storage-backend-adapter
blockedBy: []
blocks: []
---

# infra-up Docker Idempotent Lifecycle — Qdrant & FalkorDB Containers

## Overview

`make infra-up` hiện tại chỉ ensure local layout + probe/provision remote servers
(plan 260818). Nó **không quản lý Docker container** — Docker support đã bị
strip trong đợt docker-free cutover. Yêu cầu mới: `infra-up` phải idempotent
ensure Qdrant + FalkorDB (kèm Browser UI) chạy trên localhost:

- Container **đang chạy** → báo `[ok] running`, KHÔNG start lại, KHÔNG pull.
- Container **tồn tại nhưng stopped** → `docker start`, không pull.
- Container **chưa tồn tại** → `docker pull` (chỉ khi image chưa có local) +
  `docker run`.
- Tóm lại: không pull thừa, không start thừa, có rồi thì dùng luôn.

## Decisions (defaults, user chưa phản hồi scope challenge)

| Quyết định | Chọn | Lý do |
|---|---|---|
| Cơ chế | docker CLI (`inspect`/`start`/`run`) trong `scripts/mcp-lifecycle.py` | Idempotent tường minh, không thêm file config |
| FalkorDB UI | Image `falkordb/falkordb` có sẵn Browser UI tại port 3000 | 1 container duy nhất, "có cả ui" |
| Trigger | `infra-up` luôn ensure containers (trước remote probe) | Đúng yêu cầu "có rồi thì dùng luôn", đơn giản |
| Container names | `cortex-qdrant`, `cortex-falkordb` | Namespace tránh đụng container cùng loại của user |

## Container Specifications

### Qdrant
- Image: `qdrant/qdrant:latest` (pin được qua env `QDRANT_IMAGE`)
- Container: `cortex-qdrant`
- Ports: `127.0.0.1:6333->6333` (HTTP), `127.0.0.1:6334->6334` (gRPC)
- Volume: `cortex-qdrant-storage:/qdrant/storage` (named volume, dữ liệu sống qua restart)
- Extra: `--restart unless-stopped`

### FalkorDB
- Image: `falkordb/falkordb:latest` (env `FALKORDB_IMAGE`)
- Container: `cortex-falkordb`
- Ports: `127.0.0.1:6379->6379` (FalkorDB/Redis protocol), `127.0.0.1:3000->3000` (Browser UI)
- Volume: `cortex-falkordb-data:/var/lib/falkordb/data`
- Extra: `--restart unless-stopped`

Port mappings pin về `127.0.0.1` để không expose ra network ngoài máy.

## Idempotent Algorithm (per service)

```
docker inspect <name> --format '{{.State.Status}}'
├─ exit ok, status == "running"  → print "[ok] cortex-qdrant running (http://127.0.0.1:6333)"
├─ exit ok, status != "running"  → docker start <name> → print "[ok] started existing"
├─ exit nonzero (not found):
│    docker image inspect <image>
│    ├─ not found → docker pull <image> → print "[infra-up] pulled <image>"
│    └─ found     → (skip pull — không pull thừa)
│    docker run -d --name <name> ... <image> → print "[ok] created + started"
└─ docker daemon không chạy → print "[fail] docker daemon unreachable" (không crash infra-up nếu mọi project là local)
```

Reuse `run()` helper (scripts/mcp-lifecycle.py:165) với `capture=True, check=False`
cho mọi lệnh docker — không bao giờ raise trên nonzero exit; state machine tự quyết.

## Integration Point

`invoke_infra_up()` (scripts/mcp-lifecycle.py:396):
1. Sau `ensure_layout`, gọi `_ensure_docker_services()` (mới) — ensure cả 2 containers.
2. Nếu daemon unreachable: in `[warn] docker not available — skipping container ensure`
   và tiếp tục (project thuần local vẫn hoạt động; remote probe sẽ tự fail nếu cần).
3. Remote probe (`probe_all`) chạy sau — các project remote trỏ `127.0.0.1:6333` /
   `redis://127.0.0.1:6379` giờ sẽ thấy server lên.

`invoke_infra_down()` (line 455): thêm `docker stop cortex-qdrant cortex-falkordb`
(idempotent — container không tồn tại thì bỏ qua), giữ lại `reset_remote_clients()`.

Config override qua env (không hardcode trong argparse): `QDRANT_IMAGE`,
`FALKORDB_IMAGE`, `QDRANT_PORT`, `FALKORDB_PORT`, `FALKORDB_UI_PORT`.

## Phases

1. `phase-01-docker-ensure.md` — module docker ensure trong mcp-lifecycle.py + wire vào infra-up/infra-down
2. `phase-02-testing.md` — unit tests (mock subprocess, tất cả 3 nhánh state) + manual acceptance
3. `phase-03-docs.md` — update ReadMe/Makefile help + docs/logs entry

## Acceptance Criteria

- Chạy `make infra-up` 3 lần liên tiếp: lần 1 pull+run (nếu chưa có), lần 2 và 3
  in `[ok] running` — không có `docker pull`/`docker run`/`docker start` nào phát sinh.
- `docker stop cortex-qdrant && make infra-up` → container được start lại, không pull.
- FalkorDB UI mở được tại http://127.0.0.1:3000.
- Docker daemon off: `make infra-up` không crash, in warn.
- Không regress test hiện có (test_make_lifecycle.py, test_infra_remote.py).

## Out of Scope

- Docker compose file, Kubernetes, remote host provisioning (thuộc plan 260818).
- Pin image versions vào lockfile (env override là đủ).
- Qdrant Dashboard (chỉ có sẵn trong image tại /dashboard — không cần làm gì).

## 2026-08-25 Corrective Note

The original `/data` target did not match the current image's
`FALKORDB_DATA_PATH=/var/lib/falkordb/data`, so graph data stayed in the
container writable layer. The port-map parser also reversed Docker keys such
as `6379/tcp`, forcing recreation on every run. Both defects are corrected,
and readiness uses Redis `PING` before remote probing.
