# Phase 02 — Shared embedding runtime (embedder cache + query-vector cache)

Goal: một process chỉ load model đúng 1 lần per `(model, device)`; query
lặp lại không chạy transformer inference; 4 backend hết nhân bản code.
Gate: G1 (repeat-query embed p50 < 5 ms), phần embed của G3.

**Lưu ý đặt tên (red team #1):** module
`code-tiny/tools/common/embedding_runtime.py` **đã tồn tại** với nội dung
khác (`resolve_embedding_cache` + HF network-audit session, được 12
analyzer import — vd `python_analyzer.py:25`, consumed ở
`python_analyzer.py:943`). Module này **không đụng**. Module mới của phase
này là file riêng: `code-tiny/tools/common/embed_runtime.py`.

## Changes

### 1. Module mới — `code-tiny/tools/common/embed_runtime.py`

Mọi consumer đã import `tools.common.*` bằng tên ổn định (backend insert
`code-tiny` vào `sys.path` trước khi chạy — `fastmcp_server.py:23-24`,
`cplus_mcp.py:23-28`, `android_mcp.py:23-28`; unified_mcp load backend qua
importlib alias `:63-65` nhưng `tools.common` vẫn là 1 module object duy
nhất). Nội dung:

- `resolve_device(device_name: str | None) -> torch.device` — port logic
  `_resolve_embed_device` hiện tại (env `EMBED_DEVICE`, fallback cuda→cpu /
  mps→cpu kèm warning). Chỗ để Phase 03 cắm auto-detect; interface chốt từ
  phase này.
- `get_embedder(model_name, device_name=None) -> (tokenizer, model, device)`
  — process-wide cache `dict[(model_name, str(device))]` + `threading.Lock`,
  port logic `_get_embedder` (`trust_remote_code` cho jina, `model.eval()`,
  CUDA load-fail → CPU). Thay thế 4 `_embedder_cache` riêng.
- `embed_query(text, model_name, device_name=None) -> list[float]` —
  port `_embed_query`/`_embed_query_with_model` (`model.encode` nếu có
  `.encode`, ngược lại tokenizer+mean-pool `max_length=512`), kèm CPU-retry
  **thống nhất theo hành vi fastmcp** và **mở rộng bộ nhận lỗi**
  (red team #7): `_is_cuda_runtime_error` (`fastmcp_server.py:615-623`)
  generalize thành `_is_accelerator_runtime_error` nhận thêm chuỗi lỗi
  MPS/Metal (`mps`, `Metal`, `MPSBackend`, "invalid device function" đã có
  sẵn) — nếu chỉ nhận "cuda" thì lỗi MPS sẽ ném thẳng ra caller và (sau
  Phase 03) làm crash boot khi preload. Retry gated
  `EMBED_FALLBACK_TO_CPU` mặc định "1"; evict cache entry rồi retry CPU.
- `_QUERY_VECTOR_CACHE` — `OrderedDict` LRU bound, key
  `(model_name, text)`, maxsize mặc định 512, env
  `MCP_QUERY_EMBED_CACHE` ("0" tắt, số khác = maxsize). Cache hit trả
  `list` copy (tránh caller mutate entry). Không TTL — embedding
  deterministic theo model; model đổi thì key model đổi.
  **Giả định ghi rõ** (red team #13): key không chứa device — vector giữa
  các device lệch nhau tí xíu; chấp nhận được vì device cố định trong một
  process (resolve 1 lần); nếu sau này hỗ trợ đổi device nóng thì thêm
  device vào key.
- `get_sentence_transformer(model_name, device=None)` — process-wide cache
  cho `SentenceTransformer` (phase 06 dùng).
- `reset_caches()` — test helper (clear embedder + vector + ST cache).

### 2. Backend delegate — `fastmcp_server.py`, `cplus_mcp.py`, `android_mcp.py`, `java_mcp.py`

Giữ nguyên **tên** hàm module-level (test patch theo tên —
`test_qdrant_project_scope.py:181`), thay body bằng delegate:

```python
def _embed_query(text, model_name=None):
    return embed_runtime.embed_query(text, model_name or DEFAULT_MODEL)
```

Tương tự `_get_embedder`, `_resolve_embed_device`, `_embed_query_with_model`,
`_mean_pool`, `_encode_texts` (chỉ còn forward; ref line drift đã hiệu chỉnh:
cplus retry `:962`, cplus `_get_embedder` def `:895`, android `:712`, java
`:561`). Xoá `_embedder_cache` module-level.
`_preload_embedder_on_startup` gọi `embed_runtime.get_embedder` — preload
của backend thứ 2 thành no-op vì cùng key.

Lưu ý duy giữ 1 khác biệt đã có: `DEFAULT_MODEL` resolve env theo thứ tự
`CODE_EMBEDDING_MODEL_PATH → CODE_EMBEDDING_MODEL → JINA_MODEL_PATH →
jinaai/jina-embeddings-v3` — giữ nguyên từng module (doc-side dùng model
khác, không đụng).

### 3. Không đụng

`intelligent_retrieval` (nhận embedder inject),
`tools/common/embedding_runtime.py` (module audit HF — rời lating),
`explore_service` (xử lý device ở phase 03), `doc-tiny/*` (model bge-m3,
out of scope), ingest path (phase 06).

## Tests

- `tests/test_embed_runtime.py` (file mới — không đụng
  `test_embedding_runtime.py` hiện có của module audit): cache hit/miss
  (stub model đếm call), LRU eviction, `MCP_QUERY_EMBED_CACHE=0` tắt,
  cache-hit trả copy, CPU-retry path với cả lỗi cuda lẫn lỗi MPS (stub
  raise RuntimeError message "Metal"/"cuda" lần lượt), `reset_caches`.
- Test dedupe qua alias: import `fastmcp_server` thật + load bản importlib
  alias kiểu unified_mcp trong cùng process, stub model đếm call — assert
  query thứ 2 là hit (khóa cho Scope decision #6).
- Test module cũ còn nguyên: import
  `tools.common.embedding_runtime` assert `resolve_embedding_cache` vẫn
  export (guard chống ai đó "gộp nhầm" 2 module).
- Chạy lại `tests/test_qdrant_project_scope.py` hiện tại — seam
  `patch.object(module, "_embed_query")` vẫn phải hiệu lực (delegate giữ
  tên nên patch thay tên trong module backend, tool gọi tên module-level →
  seam giữ được; assert điều này trong test).

## Acceptance

- [ ] G1 đạt trên benchmark `scoped-repeat` (đo qua unified dispatch):
      embed stage p50 < 5 ms (từ baseline hàng trăm ms) — cần model thật,
      chạy local macOS.
- [ ] `scoped-cold` không regression (cache không làm cold chậm hơn).
- [ ] Unified fan-out (android+cplus backend) embed 1 query 2 lần → model
      call count = 1 (test).
- [ ] Fallback `explore_service` → `from cplus.cplus_mcp import
      _embed_query` (`explore_service.py:190`) đi vào shared cache (lazy
      attribute resolution qua delegate) — assert trong test dedupe.
- [ ] Toàn bộ suite pass.

## Rollback

Revert commit: module mới rời latin, backend delegate quay về body cũ.
Module `embedding_runtime.py` cũ không bao giờ bị đụng nên không rủi ro
regression analyzer.
