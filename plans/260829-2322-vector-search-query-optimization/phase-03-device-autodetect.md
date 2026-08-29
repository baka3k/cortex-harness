# Phase 03 — Device auto-detect (MPS / CUDA / CPU)

Goal: mặc định không set `EMBED_DEVICE` → chọn accelerator tốt nhất có sẵn:
macOS có MPS → MPS; Windows/Linux có CUDA → CUDA; unavailable/fail → CPU.
Explicit env vẫn thắng với fallback hành vi hiện tại. Gate: G2 (cold embed
MPS ≥ 2× baseline CPU trên macOS).

**Sự thật cơ sở đã kiểm chứng lại (red team #4, #9, #14):**
- `python_analyzer` **đã** implement auto-detect đầy đủ, `--device` default
  `"auto"`, tự resolve nội bộ (`python_analyzer.py:1917`, `:1977-1984`).
  Các analyzer khác (go `:1265`, cplus `:6320`, java, js…) truyền thẳng
  giá trị CLI vào `SentenceTransformer(device=…)` — `device="auto"` raw sẽ
  `RuntimeError: Expected one of cpu, cuda…` và làm **crash sync** của mọi
  analyzer trừ python.
- `dev.py::_normalize_embed_device("auto")` hiện pass-through nguyên văn
  (`dev.py:571-572`, đã có test assert ở
  `tests/test_dev_sync_windows_remote.py:142`).
- `explore_service._make_embedder` gọi `SentenceTransformer(model_name)`
  **không** có device arg và không đọc env nào (`explore_service.py:177-178`).

Hệ quả thiết kế: **"auto" không bao giờ được phép chạm downstream** —
dev.py resolve "auto" thành device cụ thể trước khi export env hoặc dựng
CLI. Runtime `embed_runtime.resolve_device` vẫn hiểu "auto" (cho MCP server
process, nơi không có dev.py trung gian).

## Changes

### 1. `embed_runtime.resolve_device` — auto-detect (từ Phase 02)

Khi `device_name` None/blank/"auto":

```
darwin  → torch.backends.mps.is_available() → "mps", else cpu
khác    → torch.cuda.is_available()         → "cuda", else cpu
```

Explicit giá trị (`"cpu"`, `"cuda:0"`, `"mps"`…) giữ nguyên logic validate
+ fallback CPU kèm warning. Không tự nâng `"cpu"` explicit lên accelerator
(opt-out tường minh). Log 1 dòng
`"embed device resolved: <device> (source=auto|env)"`.
Import torch trong module đặt trong try/except ImportError → nếu torch
thiếu, `resolve_device` trả `cpu` và `get_embedder` raise lỗi có ngữ cảnh
(không crash import module — MCP server vẫn boot được ở chế độ không-embed).

### 2. `cortex_harness/dev.py` — resolve "auto" TRƯỚC khi rời launcher

- `_normalize_embed_device` (:559-582): nhận `"auto"` → **resolve ngay
  thành device cụ thể** bằng cùng luật (darwin: mps nếu available; ngược
  lại cuda nếu available; else cpu) — cập nhật test
  `test_dev_sync_windows_remote.py:142` theo contract mới. Giá trị cụ thể
  (`mps`/`cuda`) vẫn qua vòng availability-check hiện tại.
- dev-init config default `device`: `"cpu"` → `"auto"` (code `:2803`).
- **Sửa plumbing gap:** dev sync truyền device **chưa normalize** ra CLI —
  `--device env.get("device", "cpu")` (`dev.py:1519`) và
  `--embedding-device env.get("device", "cpu")` (`:1085`). Đổi thành giá
  trị đã qua `_normalize_embed_device` (luôn là device cụ thể, không bao
  giờ "auto" raw) — analyzer go/cplus/java… nhận `mps`/`cuda`/`cpu`, không
  bao giờ nhận `"auto"`.
- Export env `EMBED_DEVICE` (`:632-634`, `:660-661`) cũng dùng giá trị đã
  normalize.

### 3. Preload guard cho MCP server boot

Với auto-detect, `_preload_embedder_on_startup` (default bật,
`fastmcp_server.py:733-744`, gọi tại `:3048`) có thể load model lên MPS
ngay lúc boot. Bọc preload trong try/except: fail (kể cả MPS load error
không khớp classifier ở phase 02) → log warning, **server vẫn boot**,
embed chỉ fail khi được gọi (lúc đó retry-CPU của `embed_query` gánh).
Không để exception preload làm chết server.

### 4. `explore_service._make_embedder` — pin device tường minh

`SentenceTransformer(model_name)` (`:177-178`) không truyền device — ST tự
chọn. Đổi thành `SentenceTransformer(model_name,
device=embed_runtime.resolve_device())` để nhất quán policy auto-detect
(1 tham số, không refactor).

### 5. Fallback lever (tùy chọn, chỉ khi G2 không đạt)

Nếu MPS batch-1 forward của jina-v3 không đạt ≥2× (risk đã ghi), phương án
rẽ tiếp theo — `model.half()`/float16 hoặc ONNX int8 — **ngoài scope**
plan này, chỉ ghi nhận trong `reports/phase-03.md` làm quyết định sau.

## Tests

- Unit (`tests/test_embed_runtime.py`): monkeypatch
  `torch.backends.mps.is_available` / `torch.cuda.is_available` — ma trận
  {darwin, win} × {mps có, cuda có, không có gì} × {env unset, "auto",
  "cpu" explicit, "mps" explicit trên win}; torch-ImportError → cpu.
- `tests/test_dev_sync_windows_remote.py` (và test dev-init tương ứng):
  `_normalize_embed_device("auto")` trả device cụ thể; dev sync command
  construction không bao giờ chứa chuỗi `"auto"` trong `--device`/
  `--embedding-device` (assert phủ định).
- Preload guard: stub model load raise → server boot tiếp (preload nuốt
  error, log warning).

## Acceptance

- [ ] Trên máy macOS này: benchmark `scoped-cold` (unified dispatch) log
      `embed device resolved: mps` và G2 đạt (p50 embed MPS ≤ 0.5× CPU
      baseline) — số vào `reports/phase-03.md`.
- [ ] So sánh score top-5 giữa CPU và MPS trên corpus synthetic trong
      tolerance (cosine diff ≤ 1e-3 trên vector, hoặc top-5 overlap 5/5) —
      guard chống MPS numeric khác biệt của jina-v3.
- [ ] Sync analyzer go (hoặc cplus) trên config device="auto" chạy được
      end-to-end (chứng minh không còn crash "auto" raw).
- [ ] Windows/CUDA path được bảo vệ bởi unit test (không cần máy thật).
- [ ] Suite pass; `EMBED_DEVICE=cpu` vẫn quay đúng CPU (lưu ý: đổi env cần
      restart process — ghi vào doc/help, không quảng cáo là runtime switch).

## Rollback

Revert commit; env `EMBED_DEVICE=cpu` + restart là escape hatch tức thời
không cần revert.
