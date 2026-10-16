# Plan: optional `repo` scope param cho code-side MCP query tools

- **Status:** implemented (2026-10-16 — 7/7 phases; live smoke 18/18 falkordblite+ladybug; review 7/10 → 1 CRITICAL (fan-out scoped-empty crash) + 8 findings đã fix hết ở cycle 1; 1 deviation: fragment `($repo = '' OR …)` thay vì `$repo IS NULL` — LadybugDB bind None param thành BOOL, xem smoke-report.md §deviation)
- **Created:** 2026-10-16
- **Mode:** hi-plan --full (1 researcher, spawn mode)
- **Inputs:**
  - Research digest (spawn researcher, 110 tool calls): [`research/research-digest.md`](research/research-digest.md)
  - Coordinator verification: `unified_mcp.py:955-968` (FastMCP schema validation), `project_scope.py:107-132` (blank-key no-op), `primary_vector_sync.py:127-144` (repo đã nằm trong Qdrant payload), `docs/mcp-output-contract.md:297-322` (scope block shape)
  - Operator query exemplars: `MATCH (n)-[e]-(m) WHERE n.repo ENDS WITH "s3"`, `WHERE n.repo = "<abspath>"` — cả hai dạng giá trị phải hoạt động.
- **BlockedBy:** none
- **Blocks:** none
- **Related plans:**
  - `261012-project-id-scope-mechanism` — pattern anh em phía mind_mcp (doc-tiny); không chạm file chung, **mirror convention** (`scope` block + `warnings` mirror, envelope v2 additive).
  - `261013-0000-mcp-single-channel-json-contract` — envelope v2.0 đã land (b6c27d8); plan này chỉ thêm field bên trong `data`. ⚠ Cả hai sửa `docs/mcp-output-contract.md` → rebase docs cẩn thận.
  - `260728-0000-unified-ingest-query-contract` — nguồn pattern `project_id_normalized`; plan này **mở rộng** pattern, không đụng predicate cũ (PROJECT_ID_QUERY_RULES R1–R6 giữ nguyên).
- **Delegation receipts:**
  - `[delegate] role=researcher mode=spawn scope=code-side MCP repo-scope surface (tool inventory / scope pattern / params / envelope / tests / exclusions / match semantics / qdrant payload) status=done`

## Goal

Thêm một param **`repo` (optional, string)** vào các tool đọc/query của code MCP server (`unified_mcp.py` :8788):
- **Không truyền / blank** → hành vi không đổi, search toàn bộ graph (mirror R1 của PROJECT_ID_QUERY_RULES).
- **Truyền** → các node **anchor/kết quả** bị lọc theo thuộc tính `repo` đã có sẵn trên mọi code node: khớp **exact** (`n.repo = $repo`) hoặc **suffix theo segment** (`n.repo ENDS WITH '/'+$repo`) — phủ cả giá trị dạng abspath lẫn `{project_name}/{basename}`, không rơi vào false positive `s3`↔`xs3`.
- Response tự giải thích qua block `scope` additive bên trong `data` (envelope v2.0 không đổi).

Use case gốc: người dùng muốn lặp lại query `MATCH (n)-[e]-(m) WHERE n.repo ENDS WITH "s3" RETURN *` qua các tool MCP hiện có mà không phải viết raw Cypher.

## Non-goals (deferred, documented)

- **Không đụng write path** — `annotate_node` và mọi tool ghi không nhận `repo` (mirror R6: scope param chỉ READ).
- Không đổi 6 project-context tools (`project_context_service.py`) — `project_topology_writer.py` không ghi `repo` nên filter sẽ luôn rỗng; enable khi writer ghi repo (follow-up).
- Không đổi 5 metadata/contract tools (list tools, schema, registry…).
- Không thêm index cho `repo` (per-node property compare đủ ở scale hiện tại; đo lại nếu profile chỉ ra bottleneck).
- Không strict_scope/existence-gating kiểu R6a — `repo` là property filter, không phải shard selector: giá trị lạ chỉ trả 0 kết quả, **không thể tự tạo junk**.
- Không backfill `repo` cho node cũ thiếu property (payload/graph đều ghi repo từ trước; node rỗng simply không match — đúng ngữ nghĩa).

## Contract changes (docs — Phase 07)

| Rule | Nội dung mới |
|---|---|
| RR1 | Không truyền `repo` → search tất cả (predicate auto-no-op, giống R1). |
| RR2 | Có `repo` → lọc node anchor/kết quả theo `repo`; node láng giềng traversal **không** bị lọc (caller tự thấy cross-repo edges). |
| RR3 | Match = exact HOẶC suffix-bắt-đầu-bằng-`/` (helper tính sẵn `$repo_suffix`, **không concat trong Cypher** — an toàn cả 3 backend). |
| RR4 | Blank/whitespace = unscoped (mirror project_id). |
| RR5 | Vector path: post-filter Python sau over-fetch (Qdrant `match` không diễn đạt được suffix); báo `scope` hits trước/sau filter. |

## Phases

| Phase | File | Delivers | Size |
|---|---|---|---|
| 01 | [phase-01-repo-scope-helper.md](phase-01-repo-scope-helper.md) | Helper `repo` trong `project_scope.py`: `prepare_repo_scope_parameters` + `repo_scope_predicate(alias)` + blank→None; unit tests | S |
| 02 | [phase-02-fastmcp-backend.md](phase-02-fastmcp-backend.md) | `fastmcp_server.py`: `repo` param + predicate vào mọi query template + scope block | L |
| 03 | [phase-03-cplus-backend.md](phase-03-cplus-backend.md) | `cplus_mcp.py`: như Phase 02 cho backend cplus | L |
| 04 | [phase-04-android-java-backends.md](phase-04-android-java-backends.md) | `android_mcp.py` + `java_mcp.py`: như Phase 02 | M |
| 05 | [phase-05-unified-proxy-metadata.md](phase-05-unified-proxy-metadata.md) | `unified_mcp.py` direct tools + pass-through; `tool_metadata.py` catalog; **live-schema guard test = acceptance gate** | M |
| 06 | [phase-06-vector-postfilter.md](phase-06-vector-postfilter.md) | `semantic_search` + `explore_graph` hybrid: over-fetch + Python post-filter theo RR5 | M |
| 07 | [phase-07-docs-smoke.md](phase-07-docs-smoke.md) | `docs/REPO_QUERY_RULES.md` + note trong `mcp-output-contract.md` + live smoke falkordblite (ENDS WITH) + session log | S |

Dependency order: 01 → {02, 03, 04, 06} (song song được sau 01); 05 sau 02–04 (guard test phủ hết surface); 07 cuối.

## Shared conventions (từ research-digest + PROJECT_ID_QUERY_RULES — theo nguyên văn)

- Response additions **chỉ bên trong `data`**; envelope v2.0 `{ok,data,error}` bất biến (`docs/mcp-output-contract.md`).
- Scope block code-side mirror shape mind-side (mode/echo/warnings) nhưng field tối giản: `scope.repo` (giá trị request), `scope.filter_applied` (bool), `warnings[]` (mirror của `scope.warnings`). Không strict, không registry lookup.
- Param: typed signature + FastMCP tự derive schema; **bắt buộc** thêm vào signature backend nếu không param bị validation drop (`unified_mcp.py:962-968`).
- Predicate Cypher đọc giữ nguyên mẫu project_id: `AND ($repo IS NULL OR X.repo = $repo OR X.repo ENDS WITH $repo_suffix)`; `$repo_suffix` do helper inject, không có key `repo` trong params → không inject suffix → predicate no-op (giống cơ chế `*_normalized`).
- Helper trung tâm duy nhất ở `tools/common/project_scope.py` — cấm tự viết filter rải rác (checklist #1 của PROJECT_ID_QUERY_RULES).
- Test: pytest root duy nhất (`pythonpath=["code-tiny","doc-tiny"]`, `pyproject.toml:40-42`); budget theo `dev-shared/test-budget.md` ({typical, boundary, invalid} × chiều input, parameterize).

## Red-team notes (self, disclosed — --full optional red-team gộp tại đây)

1. **Query 2-alias (s/t)** (`fastmcp_server.py:1600-1601`): predicate repo phải đặt **chỉ trên anchor**, không trên cả 2 endpoint — đặt cả hai sẽ giết cross-repo CALLS (caller ở repo khác là kết quả hợp lệ). Phase 02–04 có bảng anchor-per-tool; implementer xác minh từng tool.
2. **Param-drop trap**: tool nào thiếu `repo` trong signature backend → silent drop (FastMCP schema coercion). Acceptance gate = live-schema guard test (Phase 05) assert `repo` tồn tại trên **mọi** query tool — tool fail test = bug, không phải skip.
3. **falkordblite ENDS WITH chưa verify** (researcher chỉ live-test được LadybugDB 0.20.4). Phase 07 smoke bắt buộc trên falkordblite (dev default) trước khi merge; nếu hụt support (rất khó — ENDS WITH là core Cypher) → fallback thiết kế: precompute suffix match bằng `RIGHT()`/Python post-filter. Không block phase 01–06.
4. **Post-filter under-fill** (Phase 06): repo nhỏ → sau filter có thể < limit dù còn kết quả. Over-fetch 4×; khi 0/few sau filter → warning trong `scope.warnings` gợi ý thu hẹp bằng project_id + repo. Trade-off đã chấp nhận: chọn 1 code path thống nhất (suffix không diễn đạt được bằng native filter).
5. **Node `repo=""`** (payload ghi `str(source.get("repo") or "")`): không match repo nào — đúng ngữ nghĩa "thuộc repo không xác định", không cần xử lý riêng.
6. **Đặt predicate nhầm phía traversal** làm KẾT QUẢ SAI NGẦM (thiếu caller cross-repo) chứ không lỗi — review phase 02–04 phải đối chiếu bảng anchor trước khi merge.
7. **Envelope size**: `scope` block +`warnings` làm phình response khi fan-out lớn → block là object cố định ~5 field, không per-row.

## Open questions (không block implementation)

1. Warning "repo 'X' khớp 0 node" có nên kèm gợi ý các giá trị repo đang tồn tại (cần 1 query `DISTINCT n.repo` chỉ chạy khi kết quả rỗng)? Đề xuất: có, chỉ khi result empty — chi phí 1 query hiếm khi chạy. Chốt ở Phase 02 khi thấy response thực tế.
2. `get_ipc_message` / `find_screen_workflows` (tool đặc thù) có node mang repo không — xác minh trong Phase 04; nếu không mang, loại khỏi surface và ghi vào docs.
