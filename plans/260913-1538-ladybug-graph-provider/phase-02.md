# Phase 02: Provider plumbing — enum, contract, targets, factories, config, deps

## Context

Đưa `LADYBUG` vào mọi lớp selection/fail-closed hiện có theo đúng pattern `neo4j` đã đi sơ bộ. Sau phase này, `GRAPH_PROVIDER=ladybug` resolve được driver (chưa có layout/DDL — phase 03/04).

## Requirements

### `code-tiny/tools/graph/core/base.py`
- Thêm `LADYBUG = "ladybug"` vào `GraphProvider` (dòng 12-17). `KUZU` giữ nguyên value "kuzu" nhưng docstring ghi deprecated-alias; `normalize_graph_provider` map `kuzu` → `LADYBUG`.

### `code-tiny/tools/graph/core/provider_contract.py`
- `_PROVIDER_ALIASES` thêm: `"ladybug"`, `"lbug"`, `"lady-bug"`, `"kuzu"` → `LADYBUG`.
- Allow-set `{FALKORDB, NEO4J, LADYBUG}` (2 chỗ trong `normalize_graph_provider_name`, `provider_contract.py:76`).
- `isolate_graph_provider_environment` (`provider_contract.py:91-118`): provider ladybug phải strip `FALKORDB_*`/`NEO4J_*`; provider khác strip `LADYBUG_*`; thêm scoped key `LADYBUG_PROVIDER` tương tự `MCP_GRAPH_PROVIDER`.

### `code-tiny/tools/graph/core/factory.py`
- `create_driver`: branch `GraphProvider.LADYBUG` → `LadybugDriver(path=config.get("path"), graph=..., instance_id=..., owner_id=..., additional_paths=..., query_timeout_ms=...)`; bắt lỗi uri/user/password → raise hướng dẫn "ladybug is local-only".
- `create_from_env`: branch LADYBUG đọc `LADYBUG_PATH` (fallback `resolve_storage().ladybug_code_path` sau phase 03), `LADYBUG_GRAPH` default "hyper_graph".
- Xóa/đổi nhánh `KUZU NotImplementedError` (dòng 99-101) — route sang LADYBUG.

### `cortex_harness/storage/targets.py`
- `local_graph_target` (`targets.py:378`): thêm tham số `provider="falkordb"` default (giữ call sites cũ); ladybug gọi với `provider="ladybug"`.
- `_runtime_graph_target_from_env` (`targets.py:442-466`): nếu `GRAPH_PROVIDER`/`CODE_GRAPH_PROVIDER` == ladybug → `local_graph_target(env LADYBUG_PATH or "embedded", graph=LADYBUG_GRAPH or "hyper_graph", provider="ladybug")`; không tồn tại remote branch (uri set + provider ladybug → `ValueError` fail-closed).
- `remote_graph_target` default_scheme logic (`targets.py:364`): provider lạ → raise, không default bolt âm thầm.

### `cortex_harness/storage/factory.py`
- Thêm `StorageFactory.get_ladybug_driver(graph_name)` — mirror `get_falkordb_driver`; docstring module (dòng 10-11) cập nhật.

### `cortex_harness/storage/config.py`
- Env list (dòng 56) thêm `LADYBUG_PATH`, `LADYBUG_GRAPH`, `LADYBUG_QUERY_TIMEOUT_MS`, `LADYBUG_BUFFER_POOL_SIZE`, `CORTEX_GRAPH_AUTO_DDL`.
- `validate_backend_config`: nếu storage_backend=remote và graph provider chọn ladybug → `ValueError` ("ladybug is local-only; use falkordb/neo4j for remote graph").

### `tools/common/project_registry.py` + `harness_config.py`
- Project config `graph_provider` accept `ladybug` (check nơi parse/casefold — grep `graph_provider` và `GRAPH_PROVIDER` trong `tools/common/`).

### Dependencies
- `pyproject.toml` (dòng 14-26): thêm `"ladybug>=<pinned-from-spike>"` vào dependencies chính (không conditional sys_platform — wheel có cho cả 3 OS); giữ falkordblite/falkordb như hiện tại.
- `requirements.txt` + `code-tiny/requirements.txt`: dòng `ladybug` mới.

## Implementation steps

1. Sửa từng file theo requirements trên, theo thứ tự base → contract → factory → targets → storage factory → config → project_registry → deps.
2. Update tests: `tests/test_*provider*`/`test_graph_schema_preflight.py` thêm case ladybug; test fail-closed: `GRAPH_PROVIDER=ladybug` + `FALKORDB_URI` → error.
3. `python -m pytest code-tiny/tests tests -k "provider or contract or target or storage_config"` xanh.

## Acceptance

- `GRAPH_PROVIDER=ladybug LADYBUG_PATH=/tmp/x` → `GraphDriverFactory.create_from_env(GraphProvider.LADYBUG)` trả `LadybugDriver` (cần phase-01 code; test bằng fake import path).
- Unknown provider vẫn fail-closed (regression test hiện có giữ xanh).
- `pip install -e .` không vỡ trên macOS.
