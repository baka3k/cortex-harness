# Phase 04 — Remote wizard: mặc định local Docker endpoints khi để trống

## Mục tiêu

Khi `dev init` chọn `storage_backend: remote` và người dùng Enter trắng tất cả,
wizard phải tự điền các endpoint mặc định trùng với container Docker mà
`dev infra-up` quản lý, thay vì báo lỗi
`remote config must specify at least qdrant_url or falkordb_uri` và bắt retry.

## Verified Current State

- Wizard hiện tại: `cortex_harness/dev.py:2267-2317` — prompt URL/key/URI/password/TLS
  với default rỗng (hoặc giá trị cũ từ config), rồi gọi
  `validate_backend_config("remote", candidate)`; nếu cả hai URL trống → lỗi + retry loop.
- Docker specs trong `scripts/mcp-lifecycle.py:68-93` (`DOCKER_SERVICES`):
  - `cortex-qdrant`: host port `QDRANT_HTTP_PORT` (default **6333**), image `qdrant/qdrant:latest`.
  - `cortex-falkordb`: host port `FALKORDB_PORT` (default **6379**), image `falkordb/falkordb:latest`.
- `FalkorDBDriver(uri=...)` nhận URI dạng `host:port` (`storage/factory.py:154-162`);
  `QdrantRemote(url=...)` nhận URL đầy đủ `http://host:port`.
- Không có username field nào trong `RemoteStorageConfig` (chỉ `qdrant_api_key`,
  `falkordb_password`, `falkordb_ssl`) — "để rỗng username" nghĩa là bỏ qua prompt
  credentials hoàn toàn.

## Design

Trong block "Remote backend" của `dev init` (`cortex_harness/dev.py`):

1. **Defaults tự nhiên** (thứ tự ưu tiên): giá trị cũ trong `existing["remote"]`
   → env `QDRANT_HTTP_PORT` / `FALKORDB_PORT` (đồng bộ với `infra-up`) →
   `http://localhost:6333` / `localhost:6379`.
   Prompt hiển thị default rõ ràng:
   ```
   Qdrant URL [http://localhost:6333]:
   FalkorDB URI [localhost:6379]:
   ```
   Enter trắng = chấp nhận default → `validate_backend_config` luôn pass,
   không còn nhánh lỗi/retry "must specify at least one".

2. **Bỏ prompt credentials khi endpoint là local** (không phải secrets cho
   localhost Docker): sau khi có URL/URI cuối, nếu host là `localhost` /
   `127.0.0.1` / `::1` (hoặc user chấp nhận default) thì **không hỏi**
   `qdrant_api_key`, `falkordb_password`, `falkordb_ssl` — ghi rỗng/False luôn.
   Chỉ hỏi 3 field này khi user nhập endpoint non-localhost (giữ hành vi hiện tại
   cho remote thật, including `hide_input=True`).

3. **Hint sau khi ghi config**: khi endpoint là local Docker default, echo gợi ý
   `run 'dev infra-up --provision' to start local Qdrant/FalkorDB containers`.

4. **Không đổi** `validate_backend_config()` trong `storage/config.py` — mặc định
   wizard đảm bảo URL luôn non-empty; contract runtime giữ nguyên (an toàn cho
   config viết tay).

## Files Changed

| File | Thay đổi |
|---|---|
| `cortex_harness/dev.py` (block 2267-2317) | Default endpoints + skip credential prompts cho localhost + hint infra-up |
| `tests/test_dev_init_storage_backend.py` | Thêm test: Enter trắng → config nhận `http://localhost:6333` + `localhost:6379`, không credential prompts; non-localhost → vẫn hỏi credentials |

## Tests

- Blank input (fresh config) → `remote = {qdrant_url: "http://localhost:6333", falkordb_uri: "localhost:6379"}`, không prompt key/password/TLS, không error/retry.
- Blank input với env `QDRANT_HTTP_PORT=16333` → default URL `http://localhost:16333`.
- Re-init project remote hiện có → defaults lấy từ `existing["remote"]` (không bị override về 6333/6379).
- Nhập `https://q.example.io:6333` → vẫn prompt API key/password/TLS như cũ.
- `validate_backend_config` không đổi hành vi (tests hiện tại pass nguyên).

## Risks / Edge Cases

- User thực sự muốn remote-trống (chỉ Qdrant, bỏ FalkorDB): giờ Enter trắng sẽ bật
  cả hai default local. Hỗ trợ bằng cách nhập explicit `skip`? → Giữ đơn giản:
  nhập URL riêng, hoặc sau init sửa config. Ghi chú trong hint.
- Env port override chỉ áp cho default hiển thị; URL user nhập tay được tôn trọng nguyên văn.
