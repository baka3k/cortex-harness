# Phase 01 — Storage Backend Prompt trong dev init

## Mục tiêu

Bổ sung bước chọn `local | remote` vào `cortex_harness/dev.py` `init()` và ghi
`storage_backend` + `remote` (khi remote) vào config JSON.

## Changes

1. **Prompt backend mode** (sau prompt project code/name, trước prompt storage
   instance/data home — vì các prompt local storage chỉ có ý nghĩa với local):
   - `click.prompt("Storage backend", type=click.Choice(["local", "remote"]), default=<existing or "local">)`.
   - Default lấy từ config hiện có (`cfg.get("storage_backend", "local")`) khi re-init.
2. **Prompt remote fields** (chỉ khi remote), theo pattern `_prompt_graph_env`:
   - `qdrant_url` (prompt; rỗng = không dùng Qdrant remote)
   - `qdrant_api_key` (optional, `hide_input=True`)
   - `falkordb_uri` (prompt; rỗng = không dùng FalkorDB remote)
   - `falkordb_password` (optional, `hide_input=True`)
   - `falkordb_ssl` (`click.confirm`, default False)
   - Defaults từ `remote` section cũ khi re-init; password/api_key vẫn re-prompt
     (không echo giá trị cũ làm default明文 — nhưng đọc từ config làm default
     được vì config là plaintext; đơn giản: dùng default từ config cũ).
3. **Init-level validation** (không probe mạng): nếu remote mà cả `qdrant_url`
   và `falkordb_uri` đều rỗng → re-prompt hoặc báo lỗi thoát, không ghi config
   invalid. Tái dùng `validate_backend_config()` từ `storage/config.py` nếu gọi
   được không cần full config dict (nếu không thì replicate rule ≥1 URL).
4. **Điều kiện nhánh prompt local**: khi remote, skip prompt `CORTEX_STORAGE_INSTANCE` /
   `CORTEX_DATA_HOME` (hoặc giữ nhưng đánh dấu "[local only]" — chọn skip cho gọn).
5. **Ghi config**: thêm `"storage_backend": mode` top-level; khi remote thêm
   `"remote": {...}` chỉ với các field non-empty (trừ `falkordb_ssl` luôn ghi).
   `RemoteStorageConfig.__repr__` đã redact — không echo secrets trong log.
6. **Hint sau ghi config** (remote): `click.echo("[info] storage_backend=remote — run 'make infra-up' or 'dev doctor' to verify connectivity")`.

## Acceptance

- `dev init` flow local: output JSON giống hiện tại (không có key mới ngoài
  `storage_backend: "local"`).
- Flow remote: JSON chứa `storage_backend` + `remote` hợp lệ với
  `validate_backend_config()`.
