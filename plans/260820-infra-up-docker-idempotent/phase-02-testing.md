# Phase 02 — Testing

## Tasks

- [ ] Unit tests theo pattern `tests/test_infra_remote.py` (load module by path, mock):
  - [ ] container running → KHÔNG gọi docker start/run/pull.
  - [ ] container exited → gọi `docker start`, không pull.
  - [ ] container not found + image có local → `docker run`, KHÔNG pull.
  - [ ] container not found + image thiếu → pull rồi run.
  - [ ] docker daemon off → `[warn]`, infra-up exit 0, layout vẫn được ensure.
  - [ ] infra-down stop idempotent khi container không tồn tại.
- [ ] Chạy lại toàn bộ `tests/test_make_lifecycle.py` + `tests/test_infra_remote.py` (không regress).

## Manual Acceptance (không automate)

- [ ] `make infra-up` lần 1 (máy sạch): pull + run 2 container.
- [ ] `make infra-up` lần 2, 3: chỉ in `[ok] running`, `docker ps` không có container mới.
- [ ] `docker stop cortex-qdrant && make infra-up`: start lại, không pull.
- [ ] Mở http://127.0.0.1:3000 (FalkorDB UI) và http://127.0.0.1:6333/dashboard.
- [ ] `docker volume ls` thấy `cortex-qdrant-storage`, `cortex-falkordb-data`; restart container không mất dữ liệu.
- [ ] `make infra-down` stop cả 2; `make infra-up` lại → start existing.
