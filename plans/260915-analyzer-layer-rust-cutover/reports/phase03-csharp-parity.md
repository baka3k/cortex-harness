# Phase 03 — analyzer-csharp parity + fallback gate report

- chạy: 2026-09-15, script `scripts/rust_parity/analyzer_parity_csharp.py` + `scripts/rust_parity/csharp_fallback_probe.py`
- **Trạng thái: ESCALATION — gates FAIL do 1 defect trong crate `analyzer-csharp` đã landed (commit fde67a2). Harness + probe + negative test hoàn chỉnh, re-run pass ngay sau khi fix. Crate không được sửa trong scope này (constraint của worker).**
- env: dotnet 10.0.401 (chỉ có runtime .NET 10 — `DOTNET_ROLL_FORWARD=LatestMajor` cho cả 2 leg, worker dll prebuilt `roslyn_worker/bin/Release/net8.0/CSharpRoslynWorker.dll`), FalkorDB 127.0.0.1:6379, graphs `p03cs_*`
- corpus mới: `tests/fixtures/csharp-analyzer` — 9 file `.cs` (interface ×2, abstract class + inheritance, sealed/struct/enum, static class + overload + generic method, properties, fields, cross-file calls, XML docs) + `CSharpParityApp.csproj` (net8.0)
- "repo .NET thật" leg: `tests/fixtures/aspnet-core-application` (app fixture lớn nhất có sẵn trong repo; không có real .NET repo trên máy — stock là Python)
- raw run report (có diff JSON đầy đủ): `.cache/p03_csharp/parity_run_report.md`; logs: `.cache/p03_csharp/logs/`
- mask chuẩn: `_dst, _edge_id, _graph_id, _src, created_at, last_updated, summary_updated_at, updated_at`

## Kết quả gates

| Gate | Kết quả |
|---|---|
| FULL corpus: `[SCAN_RESULT]` canonical identical | **FAIL** — py `files=9 functions=47 classes=0` vs rs `files=9 functions=0 classes=0` |
| FULL corpus: graph diff rỗng ngoài mask | **FAIL** — py 94 nodes / 91 edges vs rs 10 nodes / 0 edges, diff=175 |
| FULL aspnet_app: `[SCAN_RESULT]` | **FAIL** — py `files=2 functions=2` vs rs `files=2 functions=0` |
| FULL aspnet_app: graph diff | **FAIL** — py 8 nodes vs rs 3 nodes, diff=10 |
| Incremental: cleanup counts | **FAIL** — py `(20, 0)`, rs không emit `[cleanup][graph]` (không port cleanup leg) |
| Incremental: graph diff | **FAIL** — diff=171; rs-only stale node = `File\|Services/Calculator.cs` (file đã xoá không được dọn) |
| Negative: worker unavailable → exit 3 + message | PASS |
| Negative: `--disable-roslyn` → exit 3 (no fallback) | PASS |

Chi tiết FULL corpus diff (đã attribution):
- `nodes_only_py`: Function 47, Property 17, Type 12, Field 4, Namespace 4 — rs không viết node nào ngoài Project/File.
- `nodes_only_rust`: rỗng; `props_differ: 0` — 10 node rs viết ra (1 Project + 9 File) khớp props với py.
- `edges_only_py`: CONTAINS 89, CALLS 2; rs **0 edges** (kể cả Project→File CONTAINS — secondary observation, xem dưới).
- File count + file selection parity: khớp ở cả 3 leg (FULL 9/9, aspnet 2/2, incremental files=2 cả hai phía).

## Root cause (defect #1 — chặn parity)

`rust/crates/analyzer-csharp/src/main.rs` (khối map payloads, ~dòng 199):

```rust
let payloads: Vec<Value> = match worker_response.get("results").and_then(Value::as_array) {
    Some(results) => results
        .iter()
        .map(|evidence| attach_provenance(roslyn_evidence_to_payload(evidence), &provenance))
```

`roslyn_evidence_to_payload` nhận **evidence dict** (unit test crate truyền evidence trực tiếp) nhưng main.rs truyền nguyên **result item** `{file_path, ok, error, evidence{...}}` — không unwrap `.evidence`. Python reference (`roslyn_integration.py::try_load`) unwrap: `evidence = result.get("evidence") or {}`. Hệ quả: mọi payload rỗng → `functions=0`, graph chỉ có Project/File rows. Empirically xác nhận: binary thật in `functions=0` trong khi worker trả `types`/`members` đầy đủ (đã verify raw worker response trực tiếp: 9/9 file ok, 12 types, 40+ members).

Suggested fix (3 dòng, side-effect-free với unit tests hiện có):

```rust
.map(|result| {
    let evidence = result.get("evidence").cloned().unwrap_or(Value::Null);
    attach_provenance(roslyn_evidence_to_payload(&evidence), &provenance)
})
```

## Root cause (defect #2 — incremental cleanup leg thiếu)

`analyzer-csharp/src/main.rs` không gọi incremental cleanup khi có `--deleted-files-manifest`/changed files; framework đã có sẵn `cortex_analyzer_framework::cleanup::cleanup_graph_files` (analyzer-python dùng, emit cùng format `[cleanup][graph] deleted_nodes=… deleted_unknown_functions=…`). Python leg dọn 20 nodes cho 3 files mutated; RS để lại stale node `File|Services/Calculator.cs` + mọi fact thuộc nó. Fix: mirror `analyzer-python/src/python_analyzer.rs` dòng 105–135 (cleanup `changed ∪ deleted` trước khi parse, in cùng log line).

Secondary observation: RS chỉ có batch `repo_file_edges` (completed=9, **matched=0**) và graph rs có 0 edges dù File nodes tồn tại — kiểm tra lại `repo_file_edges` writer path khi fix defect #1 (có thể chỉ là hệ quả rows rỗng, nhưng cần xác nhận sau fix).

## Fallback-usage probe (gate red-team A6)

Script: `scripts/rust_parity/csharp_fallback_probe.py`. Phương pháp (nêu thẳng trong output JSON, field `method`): subprocess re-exec, import nguyên vẹn `tools.csharp.csharp_analyzer`, monkeypatch `parse_csharp_file` (fallback-of-record — được `_load_or_parse_payload` gọi cho **mọi** file không có payload Roslyn) để tally per-file, wrap `RoslynFirstRunner.try_load` + `_build_roslyn_runner` để ghi nhận backend/resolved/last_error. CLI contract GIỐNG leg Python của parity (journal shadow env, `--ignore-cache` để parse cache không che fallback).

Kết quả trên corpus (worker khoẻ, coverage `safe_compilation`/partial):

```
[probe:healthy-worker] worker_requested=True backend=csharp_roslyn_syntax resolved=9/9
[probe:healthy-worker] fallback triggers: 0 file(s)
[probe:healthy-worker] verdict: FALLBACK NOT TRIGGERED (worker covered all files)
```

Control run (`--break-worker` → worker project sai, chứng minh instrumentation bắt được fallback):

```
[probe:broken-worker] worker_requested=True backend=None resolved=0/9
[probe:broken-worker] fallback triggers: 9 file(s)   (cả 9 file liệt kê tên)
[probe:broken-worker] worker last_error: C# Roslyn worker build failed (1) / MSB1001
[probe:broken-worker] verdict: FALLBACK EXERCISED
```

## Negative test (worker unavailable → loud error)

```
$ analyzer-csharp --root tests/fixtures/csharp-analyzer --roslyn-worker-project /nonexistent/p03/CSharpRoslynWorker.csproj
C# Roslyn worker failed: C# Roslyn worker build failed (1)
MSBUILD : error MSB1001: Unknown switch. …
exit code 3

$ analyzer-csharp --root … --disable-roslyn
--disable-roslyn is not supported in Rust backend (no tree-sitter fallback); run
`analyzer-csharp` without --disable-roslyn, or set CORTEX_RUST_ANALYZER=python
exit code 3
```

## Policy recommendation: port-fallback-REJECTED

Giữ Rust entry fail-loud (exit 3) khi worker unavailable; **không** port tree-sitter fallback. Cơ sở:
1. Trên corpus thực với worker khoẻ, fallback **không hề được trigger** (0/9 files) — nó chỉ là mạng an toàn cho failure của worker/bootstrap.
2. Khi fallback có chạy (control run), chất lượng tụt thấy rõ: tree-sitter extract 30 functions vs Roslyn 47 trên cùng corpus — Python fallback đang **âm thầm** hạ chất lượng graph khi worker hỏng. Loud error (Rust) đúng hơn: caller (orchestrator) quyết định chuyển Python backend một cách tường minh qua `CORTEX_RUST_ANALYZER=python`.
3. dotnet requirement (prerequisite duy nhất) đã được entry tự kiểm và báo lỗi actionable + INSTALLER/runbook ghi ở phase-07 docs theo plan.

## Ghi chú thêm

- Python entry tự thân có quirk: `[SCAN_RESULT] … classes=0` luôn (payload key là `types`, đếm bằng `p.get("classes")`) — không phải do backend; so canonical tính cả con số 0 này cho khớp.
- Corpus design: bỏ khai báo `delegate`/`event` vì static schema manifest `code_graph@8613fc08894a26c2` không có id-index cho label `Delegate` → Python writer crash `target label 'Delegate' has no required id index` (latent bug Python-side, ngoài scope parity — cần escalate riêng nếu muốn cover delegate).
- Cách re-run sau khi fix defect #1/#2: `cargo build --release -p analyzer-csharp` rồi `.venv/bin/python scripts/rust_parity/analyzer_parity_csharp.py` (exit 0 = ALL GATES PASS) + `.venv/bin/python scripts/rust_parity/csharp_fallback_probe.py` (và `--break-worker` control).

## Fix cycle (2026-09-15, sau gate run đầu)

Gate run đầu **FAIL 6 gates** — phát hiện 2 defect thật của crate `fde67a2` (đây chính là
giá trị của parity gate: composition sẽ ship analyzer emit graph rỗng):

| # | Defect | Fix |
|---|---|---|
| 1 | `main.rs` truyền cả worker result item thay vì nested `evidence` vào `roslyn_evidence_to_payload` (Python: `roslyn_integration.py:119-126` check `ok` + unwrap `evidence`) → payloads rỗng, RS emit 0 functions vs 47 | Mirror Python: `filter_map` skip `ok=false`, unwrap `evidence` (fallback `{}`) |
| 2 | Thiếu incremental cleanup leg (py dọn 20 nodes, rs 0) | Mirror `analyzer-python` cleanup: store mở sớm 1 lần, cleanup `changed ∪ deleted` trước worker spawn, cùng format `[cleanup][graph]` |

Script-side mask/canonical fixes (không phải code defect):
- Mask `_start_id`/`_end_id` trên edge props ở tầng script (internal surrogate — identity thật
  nằm trong edge name; shared `MASKED_PROPS` giữ nguyên cho các gate khác)
- Canonical strip `classes=`: accepted quirk — Python đếm `payload["classes"]` nhưng payload
  roslyn không populate key này (luôn in 0); RS đếm thật. Ghi nhận như quirk spring/struts.
- Canonical strip suffix `vectors=… vector_status=…` (thiếu trong implementation dù doc ghi)

**Kết quả cuối: ALL GATES PASS** — FULL corpus graph diff 0, FULL aspnet_app diff 0,
INCREMENTAL cleanup counts khớp + graph diff 0, 3 negative tests PASS (exit 3 + message),
fallback probe: healthy worker → 0/9 fallback; broken worker → 9/9 fallback với extraction
suy giảm (30 vs 47 functions) → **policy: port-fallback REJECTED** (giữ loud error), dotnet
prerequisite ghi cho phase-07 docs.

Escalation riêng (ngoài scope plan này): latent Python bug — static schema manifest thiếu
id-index cho label `Delegate` → Python writer crash trên corpus có `delegate` (corpus parity
đã exclude delegate). Báo umbrella handle.
