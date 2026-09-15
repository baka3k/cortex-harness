# Smoke Report — delegation liveness (phase-01, 2026-09-15)

Môi trường: scratch project `/tmp/cleanup-smoke-project` (copy fixture
`tests/fixtures/web-overlays/fastapi_django`, đã `dev init`), binary
`rust/target/release/cortex-dev` (build Sep 15 20:23). **Đính chính isolation (reviewer fix #7):**
env `CORTEX_STORAGE_INSTANCE=cleanup-smoke` KHÔNG override giá trị đã ghi trong config scratch —
Run 1 resolve `--falkordb-path .../instances/default/...` (path instance default của user, xem
`smoke1-falkordb.log:4`); embedded driver fail-closed **trước khi streaming** nên không có graph
write, nhưng claim "không đụng default" chỉ chắc chắn cho Run 2 (ladybug, path
`.../instances/cleanup-smoke/...` đúng như cấu hình). Bài học cho smoke sau: patch config
scratch TRƯỚC run đầu (như đã làm từ Run 2).

## Run 1 — embedded FalkorDB provider (config init mặc định)

```
dev sync code --project-dir /tmp/cleanup-smoke-project all   → exit 0 (dev wrapper), sync FAILED
[state] marked dirty: graph schema/project setup failed before streaming:
  embedded FalkorDB (FALKORDB_PATH) driver is Python-plane
[failure] outcome=failed phase=discovering
```

- **0** dòng `python-plane delegation` — đường này fail-closed CỨNG, không delegate
  (`rust/crates/cortex-sync/src/graphops.rs:132`).

## Run 2 — ladybug provider (patch config scratch: GRAPH_PROVIDER=ladybug)

```
[cortex-sync] python-plane delegation: graph target resolution:
  ladybug provider requires embedded storage resolution (Python-plane)
```

- **DELEGATION FIRED** (`orchestrator.rs:276`) → Python `incremental_sync.py` nhận
  control. Delegated run fail tiếp ở store bootstrap (`no LadybugDB store at
  .../cleanup-smoke/ladybug/code/code.lbug/my_project`) — kỳ vọng: instance scratch
  chưa qua migrate/bootstrap; không ảnh hưởng kết luận về liveness của delegation.
- Log: `reports/smoke2-ladybug.log` (bản lưu; gốc ở scratch `/tmp/cleanup-smoke-project/`).

## Run 3 — KHÔNG chạy (deviation có chủ đích so với gate "3 kỳ sync")

Mục đích gate 3 kỳ là chứng minh delegation CHẾT (tiền đề xoá nhánh A). Run 2 đã chứng minh
ngược lại — delegation SỐNG trên đường ladybug local. Run incremental thứ 3 đòi hỏi
bootstrap store thật (migrate/bootstrap instance scratch) — ngoài scope audit, không làm
tăng độ chắc chắn cho quyết định disposition. → **phase-03 khoá nhánh B** (giữ + inventory).

## Không smoke (code-evidence đủ, tránh đụng state user)

| Luồng | Kết luận | Evidence |
|---|---|---|
| `dev sync doc` | Python ingest live | `cmds/sync.rs` doc lane spawn `graphrag_ingest_langextract.py`; chạy thật cần GLiNER/jina model download |
| `dev mcp start` | auto-flip Rust; pattern-match Python server vẫn tham chiếu `unified_mcp.py`/`mcp_graph_rag.py` | `cmds/mcp.rs:35-46`, `mcp_state.rs:21-22` — user stack đang có state instance `cortex`; không start/stop hộ |
| `dev harness` | spawn `harness/scripts/*.py` | `cmds/harness.rs` (refs-digest researcher) |

## Dọn dẹp sau smoke

Fixture gốc `tests/fixtures/web-overlays/fastapi_django` đã được trả về trạng thái sạch
(xoá `.cortext-harness/` + `src/.cache/` do lần `init` nhầm đích tạo ra — `git status`
trên tests/fixtures rỗng). Scratch `/tmp/cleanup-smoke-project` + instance
`~/.cortext-harness/v1/instances/cleanup-smoke` xoá sau khi report này commit.
