---
title: "Add LadybugDB as cross-platform embedded graph provider (win32 local default, macOS/Linux opt-in)"
status: implemented-phases-01-06 (phase 06 dogfood pending — see phase-06-results.md)
created: 2026-09-13
target: "code-tiny/tools/graph, cortex_harness/storage, doc-tiny"
blockedBy: []
blocks: []
relatedPlans:
  - "260719-0100-mcp-query-capability-hardening"
phaseBlockedBy: {}
predictionReport: "reports/prediction_report_20260913-152932.md"
---

# LadybugDB graph provider (Windows-first cutover, macOS/Linux opt-in)

## Overview

Thay thế điểm yếu "FalkorDBLite không chạy trên win32" (`storage/config.py:478`, `tools/graph/cli.py:222`, `requirements.txt:2-3`) bằng **LadybugDB** — embedded graph DB (tiền thân KùzuDB), phát hành wheel native cho `win_amd64`/`win_arm64`, macOS (x86-64 + ARM64) và manylinux, Python 3.10–3.14, MIT.

Kết quả kỳ vọng:
- **Windows:** `cortex init` + local embedded graph hoạt động out-of-the-box, không cần Docker/server FalkorDB.
- **macOS/Linux:** falkordblite vẫn là default (không phá user hiện hữu); `GRAPH_PROVIDER=ladybug` để dogfood.
- **Không đụng dữ liệu cũ:** provider mới chỉ tạo store mới (provider=ladybug, mode=file). Không có ETL `.rdb` trong plan này — full cutover macOS/Linux là plan riêng, gated sau phase 06.

Verdict hi-predict: **GO cho track này** (full cutover vẫn CAUTION). Bằng chứng kiến trúc: enum `GraphProvider.KUZU` placeholder sẵn có (`base.py:15`), `Neo4jDriver` chứng minh multi-provider pattern.

## Non-goals (out of scope)

- ETL `.rdb` → Ladybug, `cortex migrate` (plan full-cutover sau).
- Bundle `.cortexdb` v2 chứa directory ladybug.
- Bỏ falkordblite trên macOS/Linux.
- Vector store: Qdrant giữ nguyên (FTS/vector native của ladybug chỉ dùng cho graph FTS, không thay Qdrant).

## Phase map

| Phase | Scope | Key files |
|---|---|---|
| 01 | LadybugDriver core (query path, lane, lease) | `code-tiny/tools/graph/driver/ladybug_driver.py` (mới) |
| 02 | Provider plumbing (enum, contract, targets, factories, config, deps) | `base.py`, `provider_contract.py`, `core/factory.py`, `storage/factory.py`, `storage/targets.py`, `storage/config.py`, `pyproject.toml` |
| 03 | Named-graph → sub-directory mapping + sibling read-only + inventory | `storage/layout.py`, `storage/migration.py`, `doc-tiny/graph_store.py`, `scripts/mcp-lifecycle.py` |
| 04 | Schema bootstrap + auto-DDL + index/FTS + introspection gom về driver | `driver/ladybug_driver.py`, `schema/preflight.py`, `scripts/setup_constraints.py`, `mcp/{java,cplus,android}/*_mcp.py`, `tools/sync/dead_code_report.py` |
| 05 | Win32 default flip + platform resolution + docs | `storage/config.py`, `tools/graph/cli.py`, `cortex_harness/dev.py`, `requirements.txt`, ReadMe |
| 06 | macOS dogfood + parity gate + benchmark smoke | tests, CI (`lifecycle-macos.yml`), docs |

Thứ tự nghiêm ngặt 01 → 02 → 03 → 04 → 05/06 (05 và 06 có thể chạy song song sau 04).

## Key architectural decisions

1. **`GraphProvider.LADYBUG = "ladybug"` mới, không reuse KUZU.** `KUZU` giữ làm deprecated alias chuyển hướng sang LADYBUG. Lý do: tên khớp package PyPI (`ladybug`), tránh ám chỉ Kuzu fork cũ.
2. **Ladybug = local-only provider.** Không có remote mode (embedded-only). `FALKORDB_URI`/remote-graph vẫn thuộc falkordb provider. `EffectiveStorageTarget` của ladybug luôn `mode="file"`.
3. **Graph name → DB directory.** `<root>/<owner>.lbug/<graph-name>/` — mỗi named-graph FalkorDB tương ứng một directory Ladybug. Mapping tập trung ở driver (lazy open + cache), layout.py chỉ cung cấp canonical path.
4. **Schema bootstrap + auto-DDL gated.** `CREATE NODE TABLE IF NOT EXISTS` từ `CODE_GRAPH_SCHEMA` khi mở store write-mode; property lạ → `ALTER TABLE ADD PROPERTY` qua cờ `CORTEX_GRAPH_AUTO_DDL` (default on, log loud). Chống worst-case "mất dữ liệu âm thầm" của static schema.
5. **Dialect gom về driver.** Các chỗ gọi trực tiếp `CALL db.relationshipTypes()`/`db.labels()` (`mcp/java/java_mcp.py:751`, `mcp/cplus/cplus_mcp.py:1091,1124`, `mcp/android/android_mcp.py:902`, `tools/sync/dead_code_report.py:231`) phải chuyển sang `driver.list_relationship_types()` / API tương ứng — tránh lặp lại nợ kỹ thuật FalkorDB.
6. **Retry/timeout giữ nguyên ngữ nghĩa FalkorDB driver:** read retry tự do, mutation retry chỉ transient, timeout mutation = ambiguous (embedded không có socket-ambiguous như redis nhưng vẫn giữ contract để tái sử dụng caller code).
7. **Dual-path ingest:** bulk `COPY FROM` DataFrame/Parquet cho initial full ingest (bảng rỗng — dedupe PK trước khi COPY) + MERGE path cho incremental (COPY chỉ skip-duplicate, không update property — đã verify docs ladybug). Chi tiết phase-04. Bonus: `EXPORT/IMPORT DATABASE` của Ladybug là ứng cử viên bundle v2 cho plan full-cutover sau này.

## Risks & gates

| Risk | Gate |
|---|---|
| Ladybug Cypher dialect khác Kuzu cũ (MERGE, wildcard rel table, FTS API name, datetime) | Phase 04 mở đầu bằng spike verifikasi trên release hiện hành; mọi kết论 ghi vào phase-04 |
| Static schema chặn write của analyzer | Phase 04 parity test: toàn bộ analyzer chạy end-to-end với ladybug, so node/rel counts |
| Fork maturity | Pin version trong pyproject; rollback = `GRAPH_PROVIDER=falkordb` (config sẵn có) |
| macOS floor 15.0+ theo PyPI metadata | Phase 01 ghi nhận version thực tế qua spike; document floor trong ReadMe |

## Verification strategy

- Unit: fake `Database`/`Connection` objects (pattern `tests/test_falkordb_driver_local.py`).
- Integration opt-in: marker `@pytest.mark.ladybug` — skip khi chưa cài package.
- Parity: phase 06 chạy bộ test hiện hữu với `GRAPH_PROVIDER=ladybug` + so sánh output MCP queries falkordblite vs ladybug trên 1 sample repo.
- Win32: monkeypatch `sys.platform` cho resolution matrix; CI windows runner nếu có thể thêm.

## Review outcome (full mode, 2026-09-13)

Reviewer verdict sau fix cycle 1: **approve-grade** — 0 critical. Hai major đã fix + regression tests:
1. Secondary named-graph stores giờ được schema bootstrap (cache key theo path store được mở, không còn primary path).
2. Query read-intent ngoài read-token set (vd `CALL show_tables()`) trên graph chưa tồn tại → raise "database does not exist" thay vì silently tạo store; write intent mở rộng cover COPY/INSTALL/LOAD/CHECKPOINT/EXPORT/IMPORT.
Minors đã fix: sibling read-only connection không còn bị reuse cho write; sibling stores nhận query timeout; `_connections` keyed by store file name (case-collision safe); validate identifier cho index/FTS DDL; auto-DDL failure chain về binder gốc; shared driver key include ladybug path; `validate_backend_config` nhận alias ladybug; cli `env_graph_provider` win32 default ladybug; Windows reserved device names bị reject trong store naming.
Minors ghi nhận chưa fix (không block): bulk_load chạy sync ngoài BoundedLane (chỉ benchmark dùng, documented trong docstring); ladybug_graph project_id fallback trong cli prepare_graph_args là dead code (argparse default luôn set).

## Active-plan coordination

Không có plan active nào sở hữu `cortex_harness/storage/*` hay `tools/graph/driver/*`. Các plan mention FalkorDB chỉ ở mức ngữ cảnh parser/query — không xung đột ownership.
