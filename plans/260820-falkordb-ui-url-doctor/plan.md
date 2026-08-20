---
title: "FalkorDB Browser UI — infra-up ensure + doctor hiển thị UI URL"
status: active
created: 2026-08-20
updated: 2026-08-20
mode: hi-plan --full
scope: scripts/mcp-lifecycle.py, cortex_harness/storage/remote_probe.py (read-only), tests/test_doctor_remote.py, tests/test_docker_ensure.py, ReadMe.md
relatedPlans:
  - 260820-infra-up-docker-idempotent
  - 260818-infra-up-remote-support
  - 260820-doctor-caller-config
blockedBy: []
blocks: []
---

# FalkorDB Browser UI — infra-up ensure + doctor hiển thị UI URL

## Overview

Image `falkordb/falkordb:latest` **đã tích hợp Browser UI trên port 3000**
(không cần image riêng). Container `cortex-falkordb` trong `DOCKER_SERVICES`
(`scripts/mcp-lifecycle.py:92-102`) đã map `FALKORDB_UI_PORT` 3000→3000 và
thậm chí dùng nó làm primary URL khi in trạng thái. Vậy:

- **infra-up**: không cần đổi image. Chỉ cần output rõ ràng hơn — in thêm dòng
  `Browser UI: http://127.0.0.1:3000` cho falkordb (hiện tại primary URL
  trùng UI URL nên user không biết đó là UI, và không thấy port redis 6379).
- **dev doctor (remote)**: khi project chọn `storage_backend: remote` và
  falkordb probe reachable, doctor chỉ in `falkordb_uri` (vd `localhost:6379`).
  Cần append UI URL `http://<host>:<ui_port>` để user mở luôn.

## Key Facts (research)

- `ProbeResult` = `{backend, url, reachable, message, cause}`
  (`cortex_harness/storage/remote_probe.py:28-36`); `result.url` chính là
  `falkordb_uri` raw — không parse host/port ở probe layer.
- `doctor_remote_checks` (`scripts/mcp-lifecycle.py:915-970`) gọi
  `doctor_check(f"remote:{project_id}:{result.backend}", ..., f"{result.url} — {result.message}")`.
- URI parsing chuẩn đã tồn tại ở driver: scheme `falkor/falkors/redis/rediss/unix`
  hoặc bare `host:port` (`code-tiny/tools/graph/driver/falkordb_driver.py:335-350`).
- `FALKORDB_UI_PORT` hiện chỉ được dùng trong `DOCKER_SERVICES` + docs
  (`ReadMe.md:91`). Không code nào đọc nó ở doctor/probe.
- Tests: `tests/test_doctor_remote.py` (monkeypatch + config-dir fixture),
  `tests/test_docker_ensure.py` (fake_run router cho `run`/`shutil.which`).

## Decisions

1. **Không đổi image** — `falkordb/falkordb:latest` đã có UI. Requirement
   "dùng bản falkordb có ui" được thoả sẵn; chỉ cải thiện output.
2. **UI URL chỉ là informational, không probe**: remote server có thể không
   expose port 3000. Doctor append URL vào message của check falkordb
   (reachable) dạng `... — Browser UI: http://host:3000`; không tạo check
   riêng có thể fail.
3. **Port UI mặc định 3000**, override bằng env `FALKORDB_UI_PORT` (getter
   dùng chung với docker port spec để nhất quán).
4. **Host derive từ `falkordb_uri`**: strip scheme (`falkor://`, `redis://`...)
   rồi `rsplit(":", 1)` — mirror logic của driver. UI luôn dùng `http://`
   (Browser UI không TLS theo image mặc định). `unix://` / không parse được
   → không hiện UI URL.
5. **infra-up output**: `_ensure_service` giữ primary URL (UI cho falkordb)
   nhưng thêm dòng phụ liệt kê secondary port; cụ thể falkordb in cả
   `redis://127.0.0.1:6379` và `Browser UI: http://127.0.0.1:3000`.

## Phases

- [x] Phase 01 — infra-up output liệt kê đủ redis + Browser UI URL
      (`phase-01.md`)
- [x] Phase 02 — doctor remote hiển thị Browser UI URL cho falkordb
      (`phase-02.md`)
- [x] Phase 03 — tests + docs (`phase-03.md`)

## Red Team (self-review)

- *Port 3000 không phải luôn là UI ở remote host* → chấp nhận: URL chỉ là
  hint, không probe, không fail check.
- *Host là docker hostname/VPN không resolve từ browser user* → ngoài scope;
  URL derive từ chính URI user cấu hình nên tính đúng đến nơi user cấu hình.
- *primary_port_index=1 (UI) làm "readiness" cho falkordb container* — dùng
  chính URL này chỉ để print, không probe HTTP, nên không thay đổi behavior.
- *Trùng lặp với plan 260820-infra-up-docker-idempotent (completed)* — không
  đụng state machine, chỉ thêm print; không conflict.

## Validation Checklist

- [x] `dev infra-up` local: thấy cả redis URL + Browser UI URL cho falkordb.
- [x] `dev doctor` với remote project reachable: dòng
      `remote:{id}:falkordb` có `Browser UI: http://...`.
- [x] `falkordb_uri` dạng scheme (`redis://h:6379`) và bare (`h:6379`) đều
      parse đúng host; `unix://` không crash, không in UI URL.
- [x] `FALKORDB_UI_PORT=3001` được tôn trọng ở cả infra-up và doctor.
- [x] Test suites liên quan pass: `test_doctor_remote.py`, `test_docker_ensure.py`.
