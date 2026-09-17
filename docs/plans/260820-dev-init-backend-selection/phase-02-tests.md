# Phase 02 — Tests

Thêm vào `tests/test_dev_init_graph_provider.py` (hoặc file mới
`tests/test_dev_init_storage_backend.py` theo convention hiện có):

1. **Default local**: chạy init với input tối thiểu → config có
   `storage_backend == "local"`, không có `remote` key, không prompt remote fields.
2. **Remote đầy đủ**: chọn `remote` + `qdrant_url` + `falkordb_uri` + password →
   config có `remote` section với đúng các field, `storage_backend == "remote"`;
   config output pass `validate_backend_config()`.
3. **Remote chỉ Qdrant** / **chỉ FalkorDB**: được chấp nhận (mixed fallback
   theo factory).
4. **Remote thiếu cả hai URL**: init báo lỗi, không ghi config invalid.
5. **Re-init remote**: config cũ có `remote` → prompt defaults lấy từ config cũ
   (backend default `remote`, URL defaults từ giá trị cũ).
6. **Secrets không bị echo**: output của command không chứa giá trị api_key/password.
7. **Local path**: khi remote, các prompt `CORTEX_STORAGE_INSTANCE`/`CORTEX_DATA_HOME`
   bị skip (env không chứa các key này).

Dùng pattern `click.testing.CliRunner` giống tests init hiện có (xem
`test_code_process_environment_rejects_legacy_remote_storage`).
