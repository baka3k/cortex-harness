# Research digest — unify both embedding pipelines on Qwen/Qwen3-Embedding-0.6B

Created: 2026-10-09. Scope: replace `jinaai/jina-embeddings-v3` (code side, graph_mcp / code-tiny) and `BAAI/bge-m3` (doc side, mind_mcp / doc-tiny) with one model on both pipelines.

Priority docs read first: `code-tiny/CLAUDE.md`, `doc-tiny/CLAUDE.md`, `cortex_harness/CLAUDE.md` (all three are empty claude-mem stubs — no conventions there), `docs/UNIFIED_INGEST_QUERY_CONTRACT.md`, `docs/HARNESS_WORKFLOW.md`. Prior art: `docs/plans/260829-2322-vector-search-query-optimization/` (status: implemented).

---

## Findings

### 1. All sites that define a default embedding model name

Full-repo grep for `jina-embeddings|jinaai|bge-m3|BAAI` (excluding `.git`, `node_modules`, venvs, `__pycache__`, `docs/plans`). Grouped:

**(a) Production defaults — code side (jinaai/jina-embeddings-v3)**

Launcher / config (cortex_harness/dev.py):
- `cortex_harness/dev.py:1731` — `_run_analyzer` (direct per-analyzer sync): `"--embed-model", env.get("EMBEDDING_MODEL", "jinaai/jina-embeddings-v3")`.
- `cortex_harness/dev.py:3032` — `dev init` code section: `code_embed_model = _p("EMBEDDING_MODEL", ["code","env","EMBEDDING_MODEL"], "jinaai/jina-embeddings-v3")`.
- `cortex_harness/dev.py:3528` — inside `sync_code` (def at :3432): `"--embed-model", str(env.get("EMBEDDING_MODEL") or "jinaai/jina-embeddings-v3")`.
- `cortex_harness/dev.py:3634` — inside `sync_code_all` (def at :3580): same jina fallback.
- (doc-side twin at `dev.py:1270` and `dev.py:3053` — see below.)

MCP backends (module-level `DEFAULT_MODEL`, all read `CODE_EMBEDDING_MODEL_PATH` → `CODE_EMBEDDING_MODEL` → `JINA_MODEL_PATH` → jina literal):
- `code-tiny/mcp/fastmcp_server.py:116-121`
- `code-tiny/mcp/cplus/cplus_mcp.py:125-129`
- `code-tiny/mcp/android/android_mcp.py:104-108`
- `code-tiny/mcp/java/java_mcp.py:103-107`

Analyzers with a jina fallback in their entrypoint (`model_name=args.embed_model or "jinaai/jina-embeddings-v3"`), the 12 "legacy" analyzers that own a local `CodeEmbedder`:
- `code-tiny/tools/python/python_analyzer.py:1919-1921`
- `code-tiny/tools/js/js_analyzer.py:1791-1793`
- `code-tiny/tools/java/java_analyzer.py:2378-2380`
- `code-tiny/tools/cplus/cplus_analyzer.py:6109-6111`
- `code-tiny/tools/kotlin/kotlin_analyzer.py:2152-2154`
- `code-tiny/tools/php/php_analyzer.py:1616-1618`
- `code-tiny/tools/ts/ts_analyzer.py:1883-1885`
- `code-tiny/tools/sql/sql_analyzer.py:2025-2027`
- `code-tiny/tools/delphi/delphi_analyzer.py:2497-2499`
- `code-tiny/tools/csharp/csharp_analyzer.py:1796-1798`
- `code-tiny/tools/plsql/plsql_analyzer.py:2309-2311`
- `code-tiny/tools/vb/vb_analyzer_base.py:2323-2325`
- `code-tiny/tools/android/android_java_analyzer.py:772-774`
- `code-tiny/tools/android/android_kotlin_analyzer.py:4391-4393`

Analyzers that delegate to the shared sync (empty-string default → jina fallback inside `sync_vector_documents` call):
- `code-tiny/tools/go/go_analyzer.py:1270` (`model_name=args.embed_model or "jinaai/jina-embeddings-v3"` then `sync_vector_documents(...)` at :1266)
- `code-tiny/tools/rust/rust_analyzer.py:1318`, `code-tiny/tools/swift/swift_analyzer.py:1207`, `code-tiny/tools/perl/perl_analyzer.py:288`, `code-tiny/tools/flutter/flutter_analyzer.py:149`, `code-tiny/tools/jp1/jp1_analyzer.py:87`, `code-tiny/tools/shell/shell_analyzer.py:315`, `code-tiny/tools/cobol/cobol_analyzer.py:220` (same pattern; their `--embed-model` defaults read `os.environ.get("CODE_EMBEDDING_MODEL","")` at go:1300, rust:1348, swift:1237, perl:363, flutter:186, cobol:125 — note cobol reads `EMBEDDING_MODEL` instead, a one-off).

Also:
- `code-tiny/tools/cplus/cplus_analyzer.py:2758` and `code-tiny/tools/kotlin/kotlin_analyzer.py:1023` and `code-tiny/tools/android/android_kotlin_analyzer.py:1188` — runtime check `"jina-embeddings-v3" in model_source.lower()` (model-specific behavior branch).
- `code-tiny/tools/common/embed_runtime.py:250` — comment naming jina-v3 in the CPU-retry rationale; `:73` / `:288` — `trust_remote_code` heuristic `"jina" in model_name.lower()`.

**(a) Production defaults — doc side (BAAI/bge-m3)**

- `cortex_harness/dev.py:1270` — `_sync_doc_folder` (def :1220, `dev sync doc`): `"--embedding-model", env.get("EMBEDDING_MODEL", "BAAI/bge-m3")`.
- `cortex_harness/dev.py:3053` — `dev init` doc section: `doc_embed_model = _p("EMBEDDING_MODEL", ["doc","env","EMBEDDING_MODEL"], "BAAI/bge-m3")`.
- `doc-tiny/mcp_graph_rag.py:80` — `DEFAULT_TEXT_EMBEDDING_MODEL = "BAAI/bge-m3"`; used at `:162` via `resolve_embedding_model(None, DEFAULT_TEXT_EMBEDDING_MODEL)`.
- `doc-tiny/graphrag_query_langextract.py:175-176` — `resolve_embedding_model(args.embedding_model, "BAAI/bge-m3")`.
- `doc-tiny/graphrag_ingest_langextract.py:1519-1520` — `resolve_embedding_model(args.embedding_model, "BAAI/bge-m3")`.
- Livingdoc scripts (inside code-tiny but they use the doc-side model by default):
  - `code-tiny/livingdoc/living-doc-vectorize.py:257` — `model_name = args.embed_model or "BAAI/bge-m3"`
  - `code-tiny/livingdoc/living-doc-link.py:198` — same
  - `code-tiny/livingdoc/living-doc-vectorize-infra.py:60` — `default=get_env("CODE_EMBEDDING_MODEL", "BAAI/bge-m3")`
  - `code-tiny/livingdoc/living-doc-pipeline.py:67` — same
  - `code-tiny/livingdoc/living-doc-vectorize.py:46` / `living-doc-link.py:47` — `default=get_env("CODE_EMBEDDING_MODEL")` (no literal fallback)

**(b) Env var reads** — see the full matrix in section 7; every `os.environ.get(...)` site is listed there.

**(c) Docs / readmes**
- `code-tiny/README.md:168, 213, 244, 267` — jina examples.
- `code-tiny/Design.md:617` — `CODE_EMBEDDING_MODEL=jinaai/jina-embeddings-v3`.
- `code-tiny/mcp/Readme.md:358` — `export CODE_EMBEDDING_MODEL=jinaai/jina-embeddings-v3`.
- `code-tiny/tools/vb/README.md:156` — same export.
- `code-tiny/livingdoc/README.md:141, 190` — `CODE_EMBEDDING_MODEL` default `BAAI/bge-m3`; `:291, 311, 376` — `--embed-model "BAAI/bge-m3"` examples.
- `code-tiny/livingdoc/strategy.md:93, 153, 177, 215` — BGE-M3 design narrative.
- `code-tiny/skills/code-graph-ingest/references/examples.md:17, 31, 45, 59, 73, 87, 101, 115, 129` — jina examples.
- `doc-tiny/Readme.md:48` (`EMBEDDING_MODEL_PATH=...bge-m3-local`), `:61` (`TEXT_EMBEDDING_MODEL=BAAI/bge-m3 # 1024`), `:62` (`EMBEDDING_MODEL_PATH=...`).
- `docs/HARNESS_WORKFLOW.md:96` — `dev init` prompt table row "EMBEDDING_MODEL | Keep default or change to your local model".
- Historical (do not need edits, but explain defaults): `docs/logs/2026-07-16-primary-vector-ingestion.md:11`, `docs/logs/2026-07-17-code-tiny-readme-refresh.md:5,9`, `docs/plans/260716-1615-.../reports/validation-report.md:22`, `docs/plans/260924-1642-yake-dynamic-rules-doc-sync/verification-report.md:23`, `docs/plans/260829-2322-.../plan.md:25,42` and `phase-02-shared-embedding-runtime.md:74,81`.

**(d) Tests pinning the literal strings**
- `tests/test_primary_analyzer_vector_contract.py:173, 177, 193, 208` — pins `"jinaai/jina-embeddings-v3"` (see section 5).
- `tests/test_explore_project_scope.py:59, 64` — passes the jina literal to `_make_embedder` and asserts the backend fallback receives the same string (string-agnostic in behavior, literal in fixture).
- `tests/test_embedding_runtime.py:27` — `resolve_embedding_cache("jinaai/jina-embeddings-v3")`.
- `tests/test_dev_init_graph_provider.py:31, 40, 353` and `tests/test_dev_ignore.py:61, 67` and `tests/test_primary_analyzer_vector_contract.py:149` — use a `"fixture-model"` literal, not a real model name (safe).

No `Qwen` embedding references exist anywhere yet (`ReadMe.md:61,117` / `dev.py:5312` mention "Qwen Code" the CLI agent — unrelated).

### 2. CodeEmbedder duplication vs shared runtime; every model-loading module and encode() site

**Duplicated (12 per-analyzer `CodeEmbedder` classes, transformers AutoModel + mean pooling, ingest-side only):**
`code-tiny/tools/python/python_analyzer.py:947`, `js/js_analyzer.py:906`, `java/java_analyzer.py:1309`, `cplus/cplus_analyzer.py:2738`, `kotlin/kotlin_analyzer.py:1008`, `php/php_analyzer.py:859`, `ts/ts_analyzer.py:490`, `sql/sql_analyzer.py:1090`, `delphi/delphi_analyzer.py:1334`, `csharp/csharp_analyzer.py:826`, `plsql/plsql_analyzer.py:1365`, `android/android_kotlin_analyzer.py:1163`. These are near byte-identical copies: `AutoTokenizer.from_pretrained` + `AutoModel.from_pretrained` with `trust_remote_code` decided by `_should_trust_remote_code` (jina-specific, e.g. python_analyzer.py:928-932), `local_files_only` from `resolve_embedding_cache`, optional `CODE_EMBEDDING_MODEL_PATH` override via `_resolve_embedding_model_source` (python_analyzer.py:935-944, duplicated per analyzer), mean-pool at `max_length=512` (python_analyzer.py:1007-1017), vector size via `_infer_vector_size` (python_analyzer.py:1076-1093: `get_sentence_embedding_dimension` → config attrs → probe embed of `"_"`). Note python's copy adds `extra_tokenizer_kwargs={"fix_mistral_regex": True}` when trust_remote_code (python_analyzer.py:952) — jina-v3-specific workaround. `vb/vb_analyzer_base.py` shares the pattern (default :2323, `embedder.vector_size` use :2474, :2544) without its own `class CodeEmbedder` hit.

**Shared runtime (`code-tiny/tools/common/embed_runtime.py`) — used by query side AND the "new primary" ingest path:**
- `get_embedder(model, device)` :126-159 — transformers `AutoTokenizer`/`AutoModel`, process-wide cache keyed `(model, device)`.
- `get_sentence_transformer(model, device, trust_remote_code)` :270-295 — `SentenceTransformer`, cache keyed `(model, device)`; `trust_remote_code=None` → `"jina" in model_name.lower()` (:288).
- `embed_query(text, model_name, device)` :235-267 — query path with LRU keyed `(model, text)`; internally `encode_texts` :169-180 (uses `model.encode` **if the object has it**, else tokenizer + `mean_pool` :162-166 at `max_length=512`, :187-198). **Mean-pool fallback is wrong for Qwen3-Embedding** (last-token pooling) — see Risks.
- `primary_vector_sync.sync_vector_documents` (`code-tiny/tools/common/primary_vector_sync.py:302-317`): `embedder_factory = embed_runtime.get_sentence_transformer` with `trust_remote_code="jina" in model_name.lower()` (:309), then `model.encode(..., batch_size=..., convert_to_numpy=True, normalize_embeddings=True)` (:311-317) — **ingest normalizes, query does not** (pre-existing asymmetry documented in the prior-art plan, plan.md:146-148).

Delegating MCP backends (thin wrappers kept for test patching): `code-tiny/mcp/fastmcp_server.py:621-653` (`_embed_query` → `embed_runtime.embed_query`), query call at `:1219`. Same delegate shape in `cplus_mcp.py`, `android_mcp.py`, `java_mcp.py` (per plan.md:141-143).

`explore_service` (`code-tiny/mcp/services/explore_service.py:169-190`): `_make_embedder` prefers `embed_runtime.get_sentence_transformer` + `_model.encode([text])[0].tolist()` (:181-185); model name from `_DEFAULT_MODEL = os.environ.get("EMBED_MODEL", "")` (:138), fallback to `cplus.cplus_mcp._embed_query` when sentence-transformers missing.

Secondary analyzers do NOT load models themselves — they delegate: `go_analyzer.py:1266` (and rust/swift/perl/flutter/jp1/shell/cobol) → `tools.common.primary_vector_sync.sync_vector_documents`.

`message_scan.py` uses **hash vectors, not a model** (`_hash_vector` at :393-400, `DEFAULT_MESSAGE_VECTOR_SIZE = 1024` at :25) — model-swap does not require touching `{base}_mess` collections.

Doc side loaders/encoders — see section 3.

### 3. How doc-tiny loads bge-m3 and calls encode

All doc-tiny model loading goes through **sentence-transformers `SentenceTransformer`** (no FlagEmbedding anywhere in the repo; grep for `FlagEmbedding` = 0 hits). Resolution helper: `doc-tiny/embedding_utils.py:16-25` `resolve_embedding_model(model, default)` — picks `model` arg, else `EMBEDDING_MODEL_PATH` env, else `EMBEDDING_MODEL` env, else `default`; sets `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` when the path exists locally or offline env is set. Device: `resolve_embedding_device` :28-34 — arg → `EMBEDDING_DEVICE` env → `"cpu"` (no MPS/CUDA auto-detect, unlike code side).

Exact quotes:
- `doc-tiny/graphrag_ingest_langextract.py:1519-1523` (ingest):
  ```python
  model_name, local_files_only = resolve_embedding_model(
      args.embedding_model, "BAAI/bge-m3"
  )
  device = resolve_embedding_device(args.embedding_device)
  embedder = SentenceTransformer(model_name, local_files_only=local_files_only, device=device)
  ```
  Encode: `:638` `vector = embedder.encode([paragraph])[0]` (one paragraph per call inside `ingest_to_qdrant`; `--no-batch` is passed by dev.py at dev.py:1277).
- `doc-tiny/graphrag_query_langextract.py:179-180` (CLI query):
  ```python
  embedder = SentenceTransformer(model_name, local_files_only=local_files_only, device=device)
  query_vector = embedder.encode([args.query])[0].tolist()
  ```
- `doc-tiny/mcp_graph_rag.py:159-168` (MCP server, lazy singleton):
  ```python
  def get_embedder() -> SentenceTransformer:
      global _embedder
      if _embedder is None:
          model_name, local_files_only = resolve_embedding_model(None, DEFAULT_TEXT_EMBEDDING_MODEL)
          device = resolve_embedding_device(None)
          _embedder = SentenceTransformer(
              model_name, local_files_only=local_files_only, device=device
          )
      return _embedder
  ```
  Encode (query side, two tools): `:918` and `:1019` — `q_vec = embedder.encode([query])[0].tolist()`.
- Livingdoc (in code-tiny, bge-m3 defaults): `code-tiny/livingdoc/living-doc-vectorize.py:259-267` `SentenceTransformer(model_name, device=args.embed_device, trust_remote_code=args.embed_trust_remote_code)` then `:270 vector_size = embedder.get_sentence_embedding_dimension()`; `living-doc-link.py:203-206` same constructor. `living-doc-vectorize-infra.py:60` and `living-doc-pipeline.py:67` only define the defaults.

### 4. Qdrant collection creation sites, size sources, naming, recreate semantics

**Code side:**
- `code-tiny/tools/common/local_qdrant.py:218-240` `ensure_collection(store, collection, vector_size)`: if the collection exists and `vector_size not in sizes.values()` → **raises ValueError** (`:226-232`); else creates with `VectorParams(size=int(vector_size), distance=COSINE)` (`:236-239`) plus optional HNSW/quantization env kwargs (`_tuning_kwargs` :170-215). **No drop/recreate anywhere on size mismatch.**
- Primary sync: `primary_vector_sync.py:320-331` — `vector_size = len(vectors[0])` (from the actual embed output), then `_ensure_collection` → `ensure_collection`. So code-side size always derives from the embedder; nothing hardcodes 1024 for creation.
- Hardcoded `1024` appears only as: `message_scan.py:25 DEFAULT_MESSAGE_VECTOR_SIZE = 1024` (hash vectors, separate `_mess` collections, created at message_scan.py:443-448 via `writer.ensure_collection()`), and as **summary-metadata fallback** `qdrant_vector_size=embedder.vector_size if embedder else 1024` in analyzer entrypoints (python_analyzer.py:2138, js:2003, cplus:6490, java:2573, php:1820, kotlin:2357, vb_analyzer_base.py:2544) — informational only, not collection creation.
- Collection naming: registry default code collection `== project_id` (contract, `docs/UNIFIED_INGEST_QUERY_CONTRACT.md:33`); MCP backend fallback defaults `QDRANT_COLLECTION` env → `"kotlin_functions"` (fastmcp_server.py:124, cplus_mcp.py:133), `"java_functions"` (java_mcp.py:111), `"android_kotlin_functions"` (android_mcp.py:112). Message-scan sibling: `default_message_collection_name` (`code-tiny/tools/common/message_scan.py:839-848`) maps `<base>_functions` → `<base>_mess` (also `_function`, else `<base>_mess`).
- Re-ingest behavior: upserts into the live collection + `_delete_stale` (primary_vector_sync.py:354-367); **fails hard on dimension mismatch** (ensure_collection raise). `recreate_collection` exists on the storage adapters (`cortex_harness/storage/qdrant.py:141-148`, `qdrant_remote.py:156-163`, factory :85-93) but no ingest path calls it.

**Doc side:**
- `doc-tiny/graphrag_ingest_langextract.py:611-620`:
  ```python
  def create_collection(client, name, vector_size):
      try:
          client.get_collection_info(name)
          return                      # exists → reuse, NO size check
      except Exception:
          pass
      client.create_collection(
          name,
          vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
      )
  ```
  Size source: `:1528` `create_collection(qdrant, args.collection, vector_size=embedder.get_sentence_embedding_dimension())`. **If a legacy `{project}_doc` collection already exists, a new model's vectors are upserted into the old 1024-dim collection with no guard** (local-mode upsert would raise on dim mismatch only if the new dim differs; same-dim different-model vectors are silently mixed).
- Naming: `QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION_DOC", "documents")` (`doc-tiny/mcp_graph_rag.py:78`); registry rule `doc_qdrant_collection == f"{project_id}_doc"` (`mcp_graph_rag.py:203, 286, 290`; `doc-tiny/project_contract.py:19, 91, 223`); ingest default `--collection` = `QDRANT_COLLECTION_DOC` env (graphrag_ingest_langextract.py:1283-1285); global reset fallback name `"graphrag_entities"` (`0_reset_all.py:112`).
- Reset behavior: `doc-tiny/0_reset_all.py:40-78` — per-project (`--project-id`): count+`delete` by `project_id_normalized` filter, collection kept; without project id: `client.delete_collection(collection)` (:73). Dry-run supported.

### 5. Tests that pin embedding model defaults / vector behavior

- `tests/test_primary_analyzer_vector_contract.py`
  - `:138-152 test_incremental_sync_normalizes_embedding_environment` — `_build_analyzer_env` maps `--embed-model/--embed-device/--embed-batch-size/--max-embed-chars` → env `CODE_EMBEDDING_MODEL/EMBED_DEVICE/EMBED_BATCH_SIZE/MAX_EMBED_CHARS` (asserts `"fixture-model"`, not a real model).
  - `:154-177 test_new_primary_analyzer_commands_include_configured_embedding_model` — for dart/go/perl/rust/swift, `_build_analyzer_cmd` must place the configured `embed_model` value (`"jinaai/jina-embeddings-v3"` fixture) after `--embed-model`. Changing the *default* in incremental_sync does not break this; it breaks only if the flag plumbing changes.
  - `:179-213 test_new_analyzers_accept_android_style_direct_vector_flags` — rust/go/swift/perl/flutter `parse_args` accept `--embed-model` etc.; asserts `args.embed_model == "jinaai/jina-embeddings-v3"` (:208) — the literal is just the passed fixture.
  - `:120-136 test_vector_failure_has_a_distinct_nonzero_exit` — exit code 4 on vector sync failure.
- `tests/test_explore_project_scope.py:57-67 test_embedder_falls_back_to_semantic_backend_for_remote_code_models` — with `sentence_transformers` patched out, `explore_service._make_embedder("jinaai/jina-embeddings-v3")` must return a callable that delegates to `cplus.cplus_mcp._embed_query("orders", "jinaai/jina-embeddings-v3")`. Pins the *fallback delegate signature*, not the model.
- `tests/test_mcp_gateway_errors.py` — no model names. `:69-87 test_embedder_initialization_is_single_flight_on_named_lane` patches `explore_module._make_embedder` and asserts `_get_embedder()` runs exactly once concurrently; `:27`, `:57`, `:89`, `:114` cover lane/drain/error semantics; `:140` gateway structured-error passthrough. Model-agnostic.
- Related guards worth knowing: `tests/test_embed_runtime.py:248 test_existing_embedding_runtime_module_untouched` (embed_runtime.py must not absorb `tools/common/embedding_runtime.py`, the HF network-audit module) and `:253 test_backend_delegate_names_remain_patchable`; `tests/test_qdrant_project_scope.py:181` patches `module._embed_query` by backend module name; `tests/test_qdrant_collection_scope.py` / `test_qdrant_local_smoke.py` exercise collection behavior with stubs.

### 6. Dependency pins relevant to embedding

Root `requirements.txt` (single shared file used by both pipelines; doc-tiny has its own subset):
- `sentence-transformers` — **unpinned** (requirements.txt; also unpinned in `doc-tiny/requirements.txt`).
- `transformers>=4.41,<4.56` (requirements.txt; not present in doc-tiny/requirements.txt).
- `torch` — **unpinned**; `einops` present (unpinned).
- `qdrant-client==1.18.0` (also `pyproject.toml:13`).
- `FlagEmbedding` — **not present anywhere** (doc side does not use it).
- `pyproject.toml` core deps (:10-18) exclude torch/transformers/sentence-transformers entirely (only click/requests/qdrant-client/falkordblite/dotenv/portalocker) — the heavy ML stack comes only from requirements.txt.
- No environment/conda docs with pins found. Doc-tiny/requirements.txt: qdrant-client==1.18.0, sentence-transformers, gliner, spacy, etc., **no torch/transformers lines** (transitive via sentence-transformers).

Qwen3-Embedding-0.6B needs transformers ≥ 4.51 (Qwen3 architecture) — inside the existing `<4.56` window (verify at implementation time; inferred).

### 7. Env var matrix

| Env var | Where read | Pipeline it controls |
| --- | --- | --- |
| `EMBEDDING_MODEL` | dev.py init defaults per section (`:3032` code=jina, `:3053` doc=bge); persisted into `.cortext-harness/config/*.json` `code.env`/`doc.env` (dev.py:3138, 3148); CLI pass-through at dev.py:1270 (doc ingest `--embedding-model`), `:1731` (analyzer `--embed-model`), `:3528`/`:3634` (incremental_sync `--embed-model`); merged into code-process env at dev.py:735-737 (`CODE_EMBEDDING_MODEL`+`EMBED_MODEL`) and doc-process env at `:765-766` (`DOC_EMBEDDING_MODEL`); code-tiny merge at `code-tiny/tools/common/harness_config.py:180-183`; read directly by `doc-tiny/embedding_utils.py:20` (after `EMBEDDING_MODEL_PATH`) and `cobol_analyzer.py:125` | **Master switch; both pipelines** (scoped per config section) |
| `CODE_EMBEDDING_MODEL` | MCP `DEFAULT_MODEL` chains (fastmcp_server.py:118, cplus_mcp.py:127, android_mcp.py:106, java_mcp.py:105); analyzer argparse defaults (python:1919, js:1791, java:2378, cplus:6109, kotlin:2152, php:1616, ts:1883, sql:2025, delphi:2497, csharp:1796, plsql:2309, vb:2323, android_java:772, android_kotlin:4391); `--embed-model` defaults in go:1300, rust:1348, swift:1237, perl:363, flutter:186, ts_backend_analyzer.py:2131; livingdoc defaults (living-doc-vectorize.py:46, living-doc-link.py:47, vectorize-infra:60, pipeline:67); set by incremental_sync.py:1232 (`env["CODE_EMBEDDING_MODEL"] = args.embed_model`) | **Code side** (analyzers + MCP query + livingdoc) |
| `EMBED_MODEL` | `code-tiny/mcp/services/explore_service.py:138` (`_DEFAULT_MODEL`); incremental_sync.py:3845 fallback (`CODE_EMBEDDING_MODEL or EMBED_MODEL`); set by dev.py:737, harness_config.py:183 | **Code side** (explore/graph fan-out embedder) |
| `CODE_EMBEDDING_MODEL_PATH` | First choice of MCP `DEFAULT_MODEL` (fastmcp:117, cplus:126, android:105, java:104); `_resolve_embedding_model_source` in the 12 CodeEmbedder analyzers (e.g. python_analyzer.py:936-944) | **Code side** local-path override |
| `EMBEDDING_MODEL_PATH` | `doc-tiny/embedding_utils.py:20` (first preference before `EMBEDDING_MODEL`); docs `doc-tiny/Readme.md:48,62` | **Doc side** local-path override |
| `JINA_MODEL_PATH` | `embed_runtime.py:70` (trust_remote_code trigger when it matches the model name); analyzer argparse fallbacks after `CODE_EMBEDDING_MODEL` (python:1920, js:1792, java:2379, cplus:6110, kotlin:2153, php:1617, ts:1884, sql:2026, delphi:2498, csharp:1797, plsql:2310, vb:2324, android_java:773, android_kotlin:4392); last choice of MCP DEFAULT_MODEL chains; `_should_trust_remote_code` copies in analyzers (e.g. python_analyzer.py:929) | **Code side** local-path + jina trust_remote_code trigger |
| `TEXT_EMBEDDING_MODEL` | **NOT read by any code.** Appears only in `doc-tiny/Readme.md:61` and a comment at `doc-tiny/mcp_graph_rag.py:68` | Stale doc trap — the doc server actually reads `EMBEDDING_MODEL`/`EMBEDDING_MODEL_PATH` |
| `DOC_EMBEDDING_MODEL` | Set by dev.py:766; **no reader found in doc-tiny** (doc-tiny reads `EMBEDDING_MODEL`) | Set-but-unconsumed |
| Adjacent | `EMBED_DEVICE` (dev.py:741/769, embed_runtime.resolve_device env source :99), `EMBEDDING_DEVICE` (doc-tiny/embedding_utils.py:31, default `cpu`), `EMBED_FALLBACK_TO_CPU` (embed_runtime.py:51, default on), `MCP_PRELOAD_EMBEDDER` (fastmcp:122), `MCP_QUERY_EMBED_CACHE` (embed_runtime.py:202), `EMBEDDING_LOCAL_ONLY`/`HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` (doc-tiny/embedding_utils.py:6-13) | device/cache/offline behavior |

### 8. Re-ingest / wipe entry points today

- **Code side:** `dev sync code all` (dev.py `sync_code_all` :3580) and `dev sync code` (:3432) → `code-tiny/tools/sync/incremental_sync.py` (its `--embed-model` default `CODE_EMBEDDING_MODEL or EMBED_MODEL` at :3845). "Full" means re-scan + re-embed + upsert + `_delete_stale` (primary_vector_sync.py:262-367); it **never drops collections**. Wipe recipe in docs is manual: `rm -rf .cortext-harness/sync-state/` then full sync (`docs/HARNESS_WORKFLOW.md:477-483`) — clears change tracking, not vectors. **No dev/doctor command drops a code Qdrant collection.** The only destructive tool is `code-tiny/scripts/rebuild_vector_collection.py` (delete/recreate with tuning, **copy vectors, never re-embed** — header :1-23; `delete_collection` calls at :127, :150, :167, :188) — unusable as-is for a model swap (vectors would be copied verbatim). `db_transfer export/import` is the recommended backup (rebuild script :21-22).
- **Doc side:** `dev sync doc all` → `graphrag_ingest_langextract.py` (dev.py:1263-1277). Wipe: `python doc-tiny/0_reset_all.py [--project-id X] [--dry-run/--force]` (`0_reset_all.py:82-128`; global form deletes the whole collection :73, per-project form deletes by filter :66-69). Also `db_transfer` for storage-level export.
- Storage adapters expose `delete_collection`/`recreate_collection` (`cortex_harness/storage/qdrant.py:141-155`, `qdrant_remote.py:156-168`, `factory.py:85-93`) — building blocks exist; nothing wires them into a doctor/cleanup command.
- `dev doctor` (`dev.py:2794-2796` → `_run_lifecycle("doctor")`) — health checks only, no collection management (inferred from `scripts/mcp-lifecycle.py` role; not verified line-by-line).

### 9. Prompt/template text wrapped around texts before encode

**None found.** No `prompt_name`, no `"query:"`/`"passage:"` prefixes, no instruction strings prepended in either pipeline. The only "prompt" hits are LLM generation prompts (`doc-tiny/graphrag_query_langextract.py:135` `build_generation_prompt` — post-retrieval answer synthesis, not embedding) and GLiNER labels. Both sides encode raw text: code ingest truncates/chunks by chars (`MAX_EMBED_CHARS`, CodeEmbedder `_truncate_text`/`_split_chunks` python_analyzer.py:1036-1048; sync-side `documents_from_payloads` `_bounded_text` primary_vector_sync.py:92-97); code query passes the raw query (`fastmcp_server.py:1219`); doc side encodes the raw paragraph/query (`:638`, `:180`, `:918`, `:1019`). Qwen3's recommended `instruct` query prompt is therefore an *addition*, not a replacement — decide whether to introduce it (and keep it query-side only, matching the model card).

### 10. Conventions any model change must respect

From `docs/UNIFIED_INGEST_QUERY_CONTRACT.md`:
- Naming: code Qdrant collection `== project_id`; doc graph+collection `== f"{project_id}_doc"` (:29-38); config-file values always win over the naming rule (:38-39).
- `ProjectRegistry` is the single source of truth — `code-tiny/tools/common/project_registry.py`, mirrored (not shared) as `doc-tiny/project_contract.py` because doc-tiny is flat scripts (:45-48). Registry is re-read every call, no in-process cache (:72-73).
- Launcher contract: `dev.py mcp start` and `scripts/mcp-lifecycle.py start` produce byte-identical env dicts (`FALKORDB_*`, `QDRANT_COLLECTION`, `QDRANT_COLLECTION_DOC`, `NEO4J_DB`, `GRAPH_PROVIDER`, `PROJECT_ID`) (:200-218). Any new model env var must be added to both launchers symmetrically.
- Reset is per-project scoped, other projects intact (:180-194). Backfill doctrine: **drop + re-ingest, never in-place migration** (:176-179, Migration Playbook :262-278).
- `project_id` is the only per-call key; query precedence scoped/unscoped (:99-107); fan-out tool list and precedence must not change (:108-138).
From the prior-art plan (260829-2322, implemented):
- Embedding state lives in `tools/common/embed_runtime.py`; `tools/common/embedding_runtime.py` (HF network-audit, used by 12 analyzers) **must stay separate** — guarded by `tests/test_embed_runtime.py:248`.
- Backend module-level helper names (`_embed_query`, `_get_embedder`, …) are kept as thin delegates because tests patch by module name (plan.md:120-123; `tests/test_qdrant_project_scope.py:181`).
- Query-vs-ingest normalization asymmetry is documented spec (plan.md:144-148) — a model swap is the natural moment to revisit, but it changes scores.
- Benchmark/validation gates: `scripts/validate_retrieval.py` correctness pass and `tests/benchmark_*.py` harness measured via `unified_mcp._dispatch_tool("semantic_search", …)` (plan.md:199-214).
From `pyproject.toml:40-42`: pytest `pythonpath = ["code-tiny", "doc-tiny"]` — both trees are import roots in tests.

---

## Conventions to follow (implementation checklist)

1. Keep the registry/naming contract untouched; do not add model info to collection names.
2. Change the model **per pipeline config section** via the existing `EMBEDDING_MODEL` (`code.env` / `doc.env`) + `CODE_EMBEDDING_MODEL` + `EMBED_MODEL` + `EMBEDDING_MODEL_PATH` chain — update every literal default listed in Finding 1 so the defaults converge on `Qwen/Qwen3-Embedding-0.6B` (or are removed in favor of one shared constant).
3. Preserve the `embed_runtime` vs `embedding_runtime` module split and the patchable delegate names in the 4 MCP backends.
4. Follow the drop + re-ingest doctrine for migration; per-project reset commands already exist on the doc side and need a code-side equivalent (see Recommended).
5. If query prompts (Qwen3 `instruct` prefix) are introduced, apply them **only on the query side**, in `embed_runtime.embed_query` / doc `get_embedder`/query scripts, never at ingest.

## Risks / open questions

1. **[BLOCKER-level, verified] Pooling mismatch on the code-side query path.** `embed_runtime.embed_query` → `get_embedder` loads a plain transformers `AutoModel` and falls back to **mean pooling** (`embed_runtime.py:162-198`, no `model.encode` attribute on `AutoModel`). Qwen3-Embedding requires **last-token pooling**; mean pooling produces garbage vectors. Ingest uses `SentenceTransformer` (correct modules) — so a naive swap yields ingest/query vectors from different pooling schemes. `embed_query` must be rerouted through `get_sentence_transformer` (or implement last-token pooling keyed by model family).
2. **[BLOCKER-level, verified] Silent dimension collision.** jina-v3 (1024), bge-m3 (1024), and Qwen3-Embedding-0.6B (default 1024) are all 1024-dim. Code-side `ensure_collection` raises only on dimension mismatch (`local_qdrant.py:226-232`) and doc-side `create_collection` doesn't check size at all on existing collections (`graphrag_ingest_langextract.py:611-620`). A swapped model will happily upsert incompatible 1024-dim vectors into legacy collections — retrieval quality collapses with **no error**. Requires: explicit re-index of all collections plus an in-payload model marker and/or a collection metadata "model id" check before upsert/query.
3. **[Major] `trust_remote_code` and jina-specific heuristics everywhere.** `"jina" in model_name.lower()` decides `trust_remote_code` in `embed_runtime.py:73` and `:288`, `primary_vector_sync.py:309`, and the 12 CodeEmbedder copies (plus `fix_mistral_regex` tokenizer kwarg, python_analyzer.py:952, and the `"jina-embeddings-v3" in model_source.lower()` branches at cplus:2758, kotlin:1023, android_kotlin:1188). A local-path model (`CODE_EMBEDDING_MODEL_PATH`) bypasses the heuristic entirely. All sites need a model-agnostic or Qwen-aware policy (Qwen3 needs `trust_remote_code=False`).
4. **[Major] Duplication surface is ~30 files.** 12 CodeEmbedder classes + 4 MCP DEFAULT_MODEL chains + 8 delegating analyzers + 4 livingdoc scripts + dev.py (6 sites) + docs (≈20 hits) + tests (3 files with literals). Missing one site leaves a straggler pipeline embedding with jina/bge while the rest use Qwen.
5. **[Medium] MPS/CPU numerical parity and device defaults differ per side.** Code side auto-detects MPS/CUDA (embed_runtime.py:76-123); doc side is `cpu`-only by default (`doc-tiny/embedding_utils.py:28-34`) — Qwen3 0.6B on CPU will be slow for doc queries; consider porting device auto-detect to doc-tiny. Qwen3 on MPS needs the same CPU-fallback guard already present in embed_runtime.
6. **[Medium] Env var dead ends.** `TEXT_EMBEDDING_MODEL` is documented (`doc-tiny/Readme.md:61`) but never read; `DOC_EMBEDDING_MODEL` is set (dev.py:766) but never read. Either wire them or fix the docs during the swap, or operators will set the wrong var and silently keep bge-m3.
7. **[Unknown] Qwen3-Embedding-0.6B under `SentenceTransformer` with `local_files_only`** — needs a locally verified snapshot (model card ships ST config; inferred working, not verified in this repo). Also `max_length=512` truncation in the mean-pool fallback paths and `MAX_EMBED_CHARS=500/800` defaults (dev.py init :3034/:3055) interact with Qwen3's 32k context — truncation is safe but wasteful; not a correctness issue.
8. **[Open] Which collections must be re-indexed:** code `{project_id}`, `{project_id}_mess` (hash vectors — NOT affected), doc `{project_id}_doc`, plus any legacy MCP default collections (`kotlin_functions` etc.) that may exist on disk from pre-registry runs. The `{project}_mess` hash-vector collections survive the swap untouched (`message_scan.py:25,393`).

## Recommended approaches (max 3, one line each)

1. Add a single model-family-aware helper in `tools/common/embed_runtime.py` (name → `trust_remote_code`, pooling mode, query prompt) and reroute `embed_query` through `get_sentence_transformer` so both sides always encode via SentenceTransformer modules; sweep all literal defaults (Finding 1) to the Qwen id.
2. Migration = version-bump gate, not silent swap: stamp each upsert payload with `embedding_model`, check it (plus vector size) at query/ingest time, and provide `dev sync code/doc --reindex` (or a small `dev doctor --drop-collections`) that drops the project's collections via the existing adapter `delete_collection` before a full sync.
3. Update the contract: fold the env matrix (kill/wire `TEXT_EMBEDDING_MODEL`/`DOC_EMBEDDING_MODEL`), document the model marker + re-index playbook in `UNIFIED_INGEST_QUERY_CONTRACT.md`, and update the three pinning tests plus README/livingdoc/skill examples in the same change.
