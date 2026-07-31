# End-to-End Data Flow: tree-sitter → cache → Rust → Qdrant + FalkorDB

## Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PYTHON (orchestration)                              │
│                                                                             │
│  cplus_analyzer.py main()                                                   │
│     │                                                                       │
│     ├── 1. SCAN files (os.walk)                                             │
│     ├── 2. CHECK cache (JSON mtime check)                                   │
│     │      ├── HIT  → load JSON payload                                     │
│     │      └── MISS → ──────────────────────► RUST (step 3)                │
│     │                                                                       │
│     ├── 3. 🔧 RUST: extract_cplus_batch(paths, threads=8)                  │
│     │      ├── Parse tree-sitter (C native, same grammar)                   │
│     │      ├── Walk AST (Rust iterative DFS)                                │
│     │      ├── Resolve calls (Rust HashMap indexes)                        │
│     │      ├── Enrich semantic (Rust regex signals)                         │
│     │      └── Return Vec<PyDict> ← same ParseResult schema                │
│     │                                                                       │
│     ├── 4. WRITE cache JSON (Python — atomic write per file)               │
│     │                                                                       │
│     ├── 5. WRITE FalkorDB/Neo4j (Python async — LanguageCodeWriter)       │
│     │      ├── Batch buf_files[], buf_functions[], buf_calls[]             │
│     │      └── Cypher UNWIND queries via driver                             │
│     │                                                                       │
│     └── 6. WRITE Qdrant (Python — embedder + REST)                         │
│            ├── Load model (torch — 1 lần per process)                      │
│            ├── Batch embed texts → vectors                                  │
│            └── REST upsert /collections/{name}/points?wait=true            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Bước 1-2: SCAN + CACHE CHECK (Python — unchanged)

```python
# cplus_analyzer.py — existing code, không đổi

all_scanned_paths = _scan_c_family_files(root)  # line 2490
# Trả về list file paths .cpp/.h/.c/.hpp/.cc/.cxx/.hh/.hxx

# Cache check per file (trong iter_payloads, line 2999):
signature = f"{file_sig}|lang:{parser_language}|schema:{_PARSE_CACHE_VERSION}"
cached_payload = load_parse_cache(parse_cache_root, rel_path, signature)

if cached_payload:
    # CACHE HIT → dùng luôn, KHÔNG gọi Rust
    yield cached_payload
else:
    # CACHE MISS → cần parse mới
    # Nếu Rust available → gọi Rust batch
    # Nếu không → gọi Python parse_c_family_file()
```

**Cache file location:** `.cache/cplus_analyzer/<project_hash>/parse/<sha1(rel_path)>.json`

**Cache invalidation:** mtime_ns + size + parser_language + schema version. Nếu file thay đổi → cache miss → parse lại.

```
Cache HIT (file không đổi):  0.4ms/file  → Python load JSON
Cache MISS (file mới/đổi):  15-374ms/file → Rust extract (hoặc Python fallback)
```

## Bước 3: RUST EXTRACT (thay thế Python parse)

### 3a. Tree-sitter parse (C native — KHÔNG đổi)

```
Rust crate: tree-sitter-cpp / tree-sitter-c
    │
    ├── Language capsule loaded 1 lần (thread_local parser)
    │
    ├── File bytes đọc bằng std::fs::read(path)
    │   └── Hoặc mmap cho file lớn (>1MB) — zero copy vào parser
    │
    ├── parser.parse(&source, None)
    │   └── tree-sitter C engine parse → Tree (node tree trong C heap)
    │       (Giống hệt Python: cùng grammar, cùng C engine)
    │
    └── tree.root_node()
        └── Node có .start_byte(), .end_byte(), .kind(), .children()
```

**Tại sao Rust không nhanh hơn ở bước này:** tree-sitter là C native trong cả Python và Rust. C engine parse tốc độ như nhau.

**Tại sao vẫn dùng Rust:** bước parse chỉ chiếm 4.4s/92.8s (4.7%). Phần còn lại (88.4s) là AST walk + extraction — đó là chỗ Rust nhanh hơn 20x.

### 3b. AST Walk + Symbol Extraction (Rust — thay thế Python _walk_tree)

```
Tree root_node
    │
    ├── walker::walk_tree(root, source_bytes, rel_path)
    │   │
    │   │  Iterative DFS (port trực tiếp từ Python _walk_tree line 1114)
    │   │  Dùng VecDeque<WalkFrame> thay vì collections.deque
    │   │
    │   ├── Match node.kind():
    │   │   ├── "function_definition"
    │   │   │   └── symbols::function::extract(node, source, frame)
    │   │   │       ├── declarator → func_name (zero-copy &str slice)
    │   │   │       ├── parameter_list → arity
    │   │   │       ├── scope_stack → qualified_name
    │   │   │       ├── comment nodes → comment text
    │   │   │       ├── start_byte:end_byte → code snippet (&str)
    │   │   │       └── → FunctionDef struct (Rust, compact)
    │   │   │
    │   │   ├── "class_specifier" / "struct_specifier"
    │   │   │   └── symbols::type_def::extract(node, source, frame)
    │   │   │       ├── name field → type name
    │   │   │       ├── base_class_clause → EXTENDS relation
    │   │   │       └── → TypeDef struct + RelationEdge
    │   │   │
    │   │   ├── "namespace_definition"
    │   │   │   └── symbols::namespace::extract → NamespaceDef
    │   │   │
    │   │   ├── "field_declaration"
    │   │   │   └── symbols::field::extract → FieldDef
    │   │   │
    │   │   ├── "call_expression" / "method_call_expression"
    │   │   │   └── calls::extract_call(node, source)
    │   │   │       ├── function field → callee_name
    │   │   │       ├── arguments → call_arity
    │   │   │       ├── _collect_call_control_context → branch_kind
    │   │   │       └── → CallEdge (callee_id = None, resolved sau)
    │   │   │
    │   │   ├── "using_directive" → using_namespaces
    │   │   ├── "type_alias" → AliasDef
    │   │   ├── "template_declaration" → TemplateDef
    │   │   ├── "preproc_def" / "preproc_function_def" → macros
    │   │   ├── "preproc_include" → includes
    │   │   └── _ → push children to work queue
    │   │
    │   └── Return WalkContext {
    │           functions: Vec<FunctionDef>,
    │           calls: Vec<CallEdge>,
    │           types: Vec<TypeDef>,
    │           namespaces: Vec<NamespaceDef>,
    │           relations: Vec<RelationEdge>,
    │           fields: Vec<FieldDef>,
    │           aliases: Vec<AliasDef>,
    │           templates: Vec<TemplateDef>,
    │           using_namespaces: Vec<String>,
    │           using_imports: HashMap<String, String>,
    │           includes: Vec<String>,
    │           macros: HashMap<String, String>,
    │           file_def: FileDef,
    │           parse_meta: ParseMeta,
    │       }
    │
    └── ExtractedPayload (Rust structs — KHÔNG phải Python dict)
```

**Tại sao nhanh hơn 20x:**
- `_node_text()`: Rust slice `&source[start..end]` (zero alloc) vs Python `source_bytes[start:end].decode("utf-8")` (alloc + decode mỗi call)
- `_normalize_ws()`: Rust `regex::Regex::replace_all` compiled 1 lần vs Python `re.sub()` per call
- Struct allocation: Rust `FunctionDef` = ~120 bytes contiguous vs Python `dataclass` = ~400 bytes + dict overhead
- No GIL: 8 threads walk song song, share `&[u8]` source

### 3c. Call Resolution (Rust — thay thế Python Pass 1 + _resolve_calls)

```
Sau khi extract tất cả files → Vec<ExtractedPayload>
    │
    ├── Phase B: Build CallIndex (1 lần, single-thread)
    │   │
    │   │  Iterate tất cả functions từ tất cả payloads:
    │   │
    │   ├── function_index_by_name: HashMap<String, Vec<FuncEntry>>
    │   │   "foo" → [{symbol_id, scope, arity, file}, ...]
    │   │
    │   ├── function_index_by_scope_name_arity:
    │   │   (Some("MyClass"), "method", 2) → [FuncEntry, ...]
    │   │
    │   ├── function_index_by_qualified:
    │   │   "MyClass::method" → FuncEntry
    │   │
    │   ├── class_methods: HashMap<String, Vec<FuncEntry>>
    │   │   "MyClass" → [method1, method2, ...]
    │   │
    │   ├── using_namespaces_by_file
    │   ├── alias_targets_by_name
    │   └── base_relations: Vec<(source_id, target_id)>
    │
    └── Phase C: Resolve calls (parallel, rayon)
        │
        ├── for payload in payloads.par_iter_mut():
        │   for call in &mut payload.calls:
        │       if call.callee_id.is_none():
        │           call.callee_id = index.resolve_callee(
        │               callee_name, caller_scope, caller_file,
        │               arity, using_namespaces, aliases
        │           )
        │
        │  Resolution priority (port từ _resolve_calls line 2086):
        │  1. (scope, name, arity) → exact scope match
        │  2. (scope, name) → scope match, any arity
        │  3. using_namespace + "::" + name → qualified match
        │  4. alias resolution
        │  5. (name, arity) → global name match
        │  6. name → first candidate
        │  7. None → unmatched (callee_id = None)
        │
        └── Result: tất cả calls có callee_id (hoặc None)
```

### 3d. Semantic Enrichment (Rust — thay thế common/semantic_inference.py)

```
Sau khi resolve calls → enrich functions với semantic signals
    │
    ├── Build UsageIndex (parallel)
    │   ├── Iterate tất cả calls
    │   ├── Classify call context: assignment / condition / await / standalone
    │   │   (Port _extract_call_context từ call_graph_builder.py)
    │   └── Index: callee_id → Vec<CallSiteContext>
    │
    ├── For each function (parallel):
    │   ├── naming_signal: match func.name với pre-compiled regex patterns
    │   │   "^(get|fetch|find)" → retrieval (0.40 weight)
    │   │   "^(set|update|save)" → mutation (0.40)
    │   │   "^(is|has|can)" → predicate (0.40)
    │   │   ...
    │   │
    │   ├── usage_signal: look up function trong UsageIndex
    │   │   assignment → retrieval (0.30)
    │   │   condition → predicate (0.30)
    │   │   ...
    │   │
    │   ├── type_signal: parse return type annotation (0.20)
    │   ├── body_signal: regex scan code body (0.10)
    │   │
    │   ├── intent = highest_priority_signal
    │   ├── confidence = weighted_sum(signals)
    │   │
    │   └── Mutate func:
    │       func.intent = "retrieval"
    │       func.confidence = 0.87
    │       func.signals = {naming: 0.9, usage: 0.85, ...}
    │       func.note = "Summary / Intent / Comment / Code"
    │
    └── Result: functions enriched với semantic metadata
```

### 3e. Build PyDict payload (Rust → Python)

```
Sau khi enrich → ExtractedPayload structs hoàn chỉnh
    │
    ├── Re-acquire GIL (PyO3)
    │
    ├── payload::build_pydict(py, payload)
    │   │
    │   │  Convert Rust structs → Python dict (matching ParseResult schema)
    │   │
    │   ├── PyDict::new(py)
    │   ├── dict.set_item("functions", PyList của PyDicts)
    │   │   Mỗi PyDict = {
    │   │       "symbol_id": "...",
    │   │       "qualified_name": "...",
    │   │       "name": "...",
    │   │       "kind": "method",
    │   │       "scope_name": Some("MyClass"),
    │   │       "file_path": "src/foo.cpp",
    │   │       "start_line": 42,
    │   │       "end_line": 58,
    │   │       "arity": 2,
    │   │       "code": "int foo() { ... }",     ← &str → Python string
    │   │       "comment": "/** ... */",
    │   │       "summary": "Retrieves user data",
    │   │       "note": "Summary / Intent / Comment / Code",
    │   │       "exported": false,
    │   │       "intent": "retrieval",            ← Phase 4 enriched
    │   │       "doc_confidence": 0.87,
    │   │       "signals": {...},
    │   │       "side_effect": false,
    │   │   }
    │   │
    │   ├── dict.set_item("calls", PyList của PyDicts)
    │   ├── dict.set_item("types", ...)
    │   ├── dict.set_item("namespaces", ...)
    │   ├── dict.set_item("relations", ...)
    │   ├── dict.set_item("fields", ...)
    │   ├── dict.set_item("aliases", ...)
    │   ├── dict.set_item("templates", ...)
    │   ├── dict.set_item("file_def", PyDict)
    │   ├── dict.set_item("using_namespaces", PyList)
    │   ├── dict.set_item("using_imports", PyDict)
    │   ├── dict.set_item("includes", PyList)
    │   ├── dict.set_item("macros", PyDict)
    │   └── dict.set_item("parse_meta", PyDict)
    │
    └── Return PyObject (Python nhận native dict)
```

## Bước 4: WRITE CACHE (Python — unchanged)

```python
# Sau khi Rust trả về payload dict, Python ghi cache như cũ:

if parse_cache and signature is not None:
    write_parse_cache(parse_cache_root, rel_path, signature, payload)
    # → JSON serialize + atomic write (.tmp → os.replace)
    # → Lần sau cache HIT, không cần gọi Rust nữa
```

```
Cache file structure:
.cache/cplus_analyzer/<project_hash>/parse/
├── a1b2c3d4e5f6g7h8i9j0.json    ← sha1("src/foo.cpp")[:20].json
├── b2c3d4e5f6g7h8i9j0k1.json    ← sha1("src/bar.cpp")[:20].json
└── ...

Mỗi file:
{
    "version": 1,
    "signature": {"mtime_ns": 1690..., "size": 12345},
    "payload": { ... ParseResult dict ... }
}
```

**Incremental sync:** Chỉ file thay đổi (mtime/size khác) → cache miss → Rust parse lại. File không đổi → cache hit → skip Rust.

## Bước 5: WRITE FalkorDB / Neo4j (Python — unchanged)

```
Python LanguageCodeWriter (graph/writer/language_writer.py)
    │
    ├── Stream payloads → batch buffers
    │
    │   for payload in resolved_payloads:        ← từ Rust output
    │       buf_files.append({id, path, code, ...})
    │       buf_functions.append({id, name, scope, ...})
    │       buf_calls.append({caller_id, callee_id, ...})
    │       buf_types.append({id, name, kind, ...})
    │       buf_relations.append({source_id, target_id, rel_type})
    │       ...
    │       if len(buf_files) >= 500:
    │           await code_writer.write_batches(...)
    │           buf_files = []; buf_functions = []; ...
    │
    ├── Cypher UNWIND queries (via driver)
    │   │
    │   ├── Neo4j path:
    │   │   driver = Neo4jDriver(uri, user, password)
    │   │   await driver.execute_query("UNWIND $rows AS row MERGE ...")
    │   │
    │   └── FalkorDB path:
    │       driver = FalkorDBDriver(host, port)
    │       query = _normalize_query(cypher)   # CALL (var) → CALL { WITH var }
    │       await driver.execute_query(query, params)
    │
    └── Batch size: 500 files / 1000 nodes per flush
```

**Tại sao giữ Python:**
- Neo4j Python driver là async (asyncio) — Rust FFI với async Python phức tạp
- FalkorDB driver dùng Redis protocol — Python library có sẵn
- Cypher query generation logic phức tạp (17+ Cypher templates)
- Đang migration Neo4j → FalkorDB — chưa ổn định

## Bước 6: WRITE Qdrant (Python — embedding must stay Python)

```
Python embedding pipeline
    │
    ├── 6a. Load embedding model (1 lần per process)
    │   │
    │   │   embedder = CodeEmbedder(
    │   │       model_name="jinaai/jina-embeddings-v3",
    │   │       device="cpu",  # or "mps" / "cuda"
    │   │       max_embed_chars=4000,
    │   │   )
    │   │   # Internal:
    │   │   self.tokenizer = AutoTokenizer.from_pretrained(model_name)
    │   │   self.model = AutoModel.from_pretrained(model_name)
    │   │   self.model.to(device)
    │   │   self.model.eval()
    │   │
    │   └── TORTEX MUST STAY PYTHON — không thể port sang Rust
    │       (PyTorch Rust bindings tồn tại nhưng chưa production-ready,
    │        HuggingFace transformers chỉ có Python)
    │
    ├── 6b. Build texts from payloads
    │   │
    │   │   for payload in resolved_payloads:
    │   │       for func in payload["functions"]:
    │   │           texts.append(func["note"] or func["code"])
    │   │   # func["note"] đã được Rust enrich (Phase 4)
    │   │   # Format: "Summary / Intent / Comment / Code"
    │   │
    │   └── Ví dụ text:
    │       "Retrieves user data (retrieval) / Get user by ID / /**
    │        Get user from database / int get_user(int id) { ... }"
    │
    ├── 6c. Batch embed
    │   │
    │   │   for batch in chunks(texts, batch_size=8):
    │   │       vectors = embedder.embed(batch)
    │   │       # Internal:
    │   │       #   encoded = tokenizer(batch, truncation=True, max_length=512)
    │   │       #   outputs = model(**encoded)
    │   │       #   embeddings = mean_pool(outputs.last_hidden_state, mask)
    │   │       #   → List[List[float]]  (1024-dim per function)
    │   │
    │   └── Output: 1024-dim vector per function
    │
    ├── 6d. Write JSONL points cache (resume capability)
    │   │
    │   │   for func, vector in zip(batch_funcs, vectors):
    │   │       point = {
    │   │           "id": stable_point_id(func["symbol_id"]),
    │   │           "vector": vector,           ← 1024 floats
    │   │           "payload": {
    │   │               "symbol_id": "...",
    │   │               "name": "...",
    │   │               "code": "...",
    │   │               "note": "...",          ← Rust enriched
    │   │               "intent": "retrieval",   ← Rust enriched
    │   │               "project_id": "...",
    │   │               ...
    │   │           }
    │   │       }
    │   │       handle.write(json.dumps(point) + "\n")
    │   │
    │   └── File: .cache/cplus_analyzer/<hash>/qdrant/<collection>_points.jsonl
    │
    └── 6e. Upsert to Qdrant (REST)
        │
        │   # Đọc JSONL, batch upsert
        │   for batch in read_jsonl(points_path, batch_size=512):
        │       qdrant_writer.upsert(batch)
        │       # REST: PUT /collections/{name}/points?wait=true
        │       # Body: {"points": [{id, vector, payload}, ...]}
        │
        └── Resume: state file tracks "upserted" count
            Nếu crash → restart continues from last upserted point
```

**Tại sao giữ Python:**
- `torch`, `transformers`, `AutoModel` — Python ML ecosystem lock-in
- Model load mất ~5s, nhưng chỉ 1 lần per process
- Embedding inference: GPU (CUDA/MPS) cần Python
- Qdrant REST: đơn giản, Python `requests` đủ

## Timeline so sánh: Current vs Rust

### Current (Python-only, sequential)

```
t=0s    [SCAN]         scan 2493 files                          0.07s
t=0.07s [PARSE]        iter_payloads (3 lần):
        │
        ├── Pass 1: parse + build indexes                        92.8s
        │   ├── tree-sitter C parse: 4.4s (already fast)
        │   ├── Python _walk_tree: 78s  ← 🔴 BOTTLENECK 1
        │   └── asdict + index build: 10s
        │
        ├── _resolve_calls: match callee_id                      ~5s
        │
        ├── Semantic enrichment (if enabled)                     80.5s ← 🔴 BOTTLENECK 2
        │
        ├── Pass 2: stream to Neo4j/FalkorDB                    ~15s
        │   └── Cypher UNWIND batches (async)
        │
        └── Pass 3: embed + Qdrant
            ├── Model load (torch): ~5s (1 lần)
            ├── Embed inference: ~30-60s (depends on func count)
            └── Qdrant upsert: ~10s

TOTAL: ~190-260s
```

### Target (Rust extraction + Python graph/embed)

```
t=0s    [SCAN]         scan 2493 files                          0.07s
t=0.07s [CACHE CHECK]  check mtime/size per file                 0.2s
        │
        ├── Cache HIT files: load JSON, skip Rust               ~2s
        │
        └── Cache MISS files: → RUST BATCH
            │
t=0.3s  [RUST EXTRACT] extract_cplus_batch(missed_files, threads=8)
            │
            ├── Phase A: parallel parse + walk (8 threads)      ~6s    (vs 92.8s)
            │   ├── tree-sitter C parse: 4.4s (same, but parallel)
            │   └── Rust walk + extract: ~2s (vs 78s Python)
            │       └── Zero-copy &str, compact structs, no GIL
            │
            ├── Phase B: build CallIndex                        ~0.3s
            │
            ├── Phase C: parallel resolve calls                 ~0.5s  (vs 5s)
            │
            ├── Phase D: parallel semantic enrichment            ~3s   (vs 80.5s)
            │
            └── Phase E: build PyDicts (GIL)                    ~1s
                └── Return list[dict] to Python
            │
t=~11s   [CACHE WRITE]  write JSON for new/changed payloads     ~9s
        │
t=~20s   [GRAPH WRITE]  stream to FalkorDB/Neo4j (Python async)  ~15s
        │   └── Same as current: Cypher UNWIND batches
        │       Payloads từ Rust (PyDict) → buf_files, buf_functions, ...
        │
t=~35s   [EMBED+QDRANT] embed + upsert (Python torch)            ~35-65s
            ├── Model load: ~5s (unchanged)
            ├── Batch embed: ~30-60s (unchanged — model inference bound)
            └── Qdrant upsert: ~10s (unchanged — network bound)

TOTAL: ~70-100s  (vs 190-260s current — ~2-3x faster end-to-end)
```

**Nếu chỉ tính parse+extract+resolve+semantic (không graph/embed):**
```
Current:  ~178s   (92.8 + 5 + 80.5)
Rust:     ~11s    (98% reduction)
```

**End-to-end bao gồm graph + embed (I/O bound, không cải thiện):**
```
Current:  ~218s
Rust:     ~100s   (54% reduction)
```

Phần còn lại (~65s) là embedding model inference + network I/O — **không thể tối ưu bằng Rust**. Phải cải thiện bằng: tăng batch_size, shared model daemon, async Qdrant.
