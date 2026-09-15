# Phase 04 — Implementation report: `analyzer-topology` (project_topology, analyzer half)

- Thực hiện: 2026-09-15 (isolated implementation worker, phase 04 của `260915-analyzer-layer-rust-cutover`)
- Trạng thái: **DONE** (parity dual-run PASS; full-stock parity = PENDING — orchestrator leg riêng, xem bên dưới)
- Python reference: `code-tiny/tools/project_topology/` (topology_analyzer.py 143 LOC CLI + pipeline.py + resolver.py + registry.py + detector.py + models.py + parsers/ ~1.460 LOC)
- Writer half đã port sẵn (`cortex-graph-writer/src/topology.rs`) được **tái dùng nguyên vẹn** — binary KHÔNG reimplement bất kỳ write query nào.

## Những gì được xây

### 1. Crate `rust/crates/analyzer-topology/` ([[bin]] `analyzer-topology`, ~6.000 LOC, `unsafe_code = deny`)

| Module | Port từ | Ghi chú |
|---|---|---|
| `src/main.rs` | `topology_analyzer.py::main` | CLI contract byte-stable với `registry::build_analyzer_cmd` cho entry `project_topology`: `--root --project-id --project-name --commit-sha-before/after --graph-provider --falkordb-graph --incremental --changed-files-manifest --deleted-files-manifest --ignore-cache --disable-message-scan --verbose` + đầy đủ `--neo4j-*`, `--ladybug-*`, `--falkordb-*`, `--cache-dir`, `--dry-run`, `--summary-output` (khớp `parse_args` + `add_graph_provider_args`). Graph writes: create 6 indexes → `writer.cleanup_project` → `writer.write(result.to_json())`. `CORTEX_DISABLE_GRAPH` → `graph_writes: {}`; `--summary-output` ghi `payload + "\n"`. Exit 0/2 như Python |
| `src/models.rs` | `models.py` | `PyValue` (dict giữ insertion order vì `str(properties)` của resolver phụ thuộc repr Python), `normalize_module_path` (posixpath.normpath từng chữ + `.lstrip("./")` quirk), `stable_module_id`/`stable_fact_id` (sha256[:24]), `safe_summary` (redact secret-like + truncate), các fact structs + `to_dict()` đúng `asdict()+_json_value` |
| `src/registry.rs` | `registry.py` | Bảng 24 `DescriptorSpec` đúng thứ tự (spec đầu tiên khớp thắng), `fnmatch` (translate semantics: `*` ăn cả `/`), `descriptor_candidates` (sorted unique) |
| `src/detector.rs` | `detector.py` + `legacy_encoding.py` | `iter_descriptor_paths` (SKIP_DIRS + dot-prefix + `matches_extra_ignore` từ framework, sorted walk, skip symlink), `parse_descriptor_file` (symlink/escape/size/io error branches, error messages OSError kiểu Python `"[Errno 2] No such file or directory: '<abs>'"`), legacy decode cho `*.ini` (BOM + NUL heuristic + utf-8/cp932/cp1252 qua `encoding_rs`) |
| `src/parsers/*` (10 parsers) | `parsers/*.py` | gradle (settings+build: include/projectDir/includeBuild, plugin→module_kind, project/external deps, dynamicFeatures), maven (namespace-safe DOM, property resolve, DOCTYPE/ENTITY reject), android (manifest + resource: permission/feature/queries/instrumentation/components/intent-filters, views walk, secret redact, node/depth limits), ant, cmake (statement scan, generator-expression diagnostics), make (targets/prerequisites/variables, `(?![=])` lookahead → fancy-regex), ini, manifest (JSON + name patterns), protobuf (service blocks, rpc + google.api.http annotation → EndpointFact) |
| `src/etdom.rs` | thay `xml.etree.ElementTree` | Mini DOM trên `quick-xml`: tag/attr namespace-expanded `{uri}local` với scope chain đúng XML (decl của chính element áp cho tag+attr của nó), text unescape, undefined entity → lỗi, `iter()` pre-order. **Parse error messages expat-compatible** (`mismatched tag: line L, column C`) — fixture `malformed/pom.xml` cho cùng message với Python |
| `src/resolver.rs` | `resolver.py` | Module paths từ descriptors + declared_modules, coordinate matching, cycle detection (canonical rotation, min lexicographic), kind priority, framework markers trên `str(properties)` (Python dict **repr** qua `PyValue::repr`), special files, diagnostics ordering |
| `src/pipeline.rs` | `pipeline.py` | `analyze_project`: discovery → parse → sort by path → resolve; LIMIT_EXCEEDED ở 10.000 files; deleted-paths diagnostic (khi truyền, giống signature Python) |
| `src/pyjson.rs` | `json.dumps(..., ensure_ascii=True, sort_keys=True)` | Serializer byte-compatible cho summary line (`", "` / `": "` separators, `\uXXXX`, surrogate pairs) |

### 2. Parity script `scripts/rust_parity/analyzer_parity_topology.py`

Dual-run PY vs RS trên 2 graph FalkorDB giống hệt nhau (`p04topo_*_py` / `_rs` @ 127.0.0.1:6379), seed foreign facts (Class/Function `is_public_api`, HttpEndpoint, AndroidManifest, AndroidResource) để link queries (`EXPOSES_API`, `EXISTING_ENDPOINT_LINK`, `ANDROID_FACT_LINK`) có đối tượng. Gate: `[project_topology]` summary byte-identical + graph dump diff rỗng ngoài mask (`dual_write_diff`) + incremental leg (thêm `feature2/build.gradle`, xoá `library/build.gradle`, manifests `--incremental`). Full-stock parity = **PENDING** — topology chạy cuối sync cần graph đầy đủ sau toàn bộ parsers, sẽ được bọc qua `sync_orchestrator_parity.py` opt-in (red-team C6 orchestrator leg) trước merge.

## Verification

```
cargo test -p analyzer-topology                       → 19 passed; 0 failed
cargo clippy -p analyzer-topology --all-targets -- -D warnings  → 0 errors
cargo check -p cortex-sync -p cortex-graph-writer -p cortex-analyzer-framework -p cortex-falkordb → Finished (0 errors)
cargo clippy -p cortex-graph-writer --all-targets -- -D warnings → 0 errors
.venv/bin/python scripts/rust_parity/analyzer_parity_topology.py → FAILURES: none — PASS
```

Chú ý: `cargo check --workspace` **bỏ qua** theo chỉ đạo của orchestrator — `analyzer-dart` và `analyzer-csharp` đang cố tình dở dang (implementer bị dừng giữa chừng); workspace check sẽ được chạy khi 2 crate đó xong.

Kết quả parity đậm hơn yêu cầu gate:

1. **Result JSON byte-identical**: `TopologyAnalysisResult.to_dict()` của Python và binary Rust dump ra **byte-identical** trên cả 2 fixture — `tests/fixtures/project-topology` (17 descriptors, 14 modules, 9 deps, 2 endpoints, 3 frameworks, 4 diagnostics gồm malformed-pom expat message và dependency cycle) VÀ một edge fixture ngoài spec (`.env` missing-file IO diagnostic, `*.ini`, `pyproject.toml`, `package.json` workspaces, broken JSON, symlink, DOCTYPE reject, build/node_modules/.git pruning, framework spring detect qua `str(properties)` repr).
2. **Summary line byte-identical** (`[project_topology] {...}`) ở cả 2 chế độ: `--dry-run` (không có `graph_writes`) và graph-disabled (có `graph_writes: {}`), có/không manifests.
3. **Graph diff = 0 ngoài mask** cho cả full leg (49 nodes / 44 edges, 43 topology-owned) và incremental leg (51/45), summaries byte-identical ở cả 2 leg.

## Gap của writer half (phase-03 crate) — đã fix tại chỗ, không reimplement

1. **`cortex-graph-writer/src/topology.rs::graph_value`** — list-of-dicts (mọi `evidence` property) bị per-item stringify trước khi dump, trong khi Python `json.dumps` TOÀN BỘ list (keys sort đệ quy). Sửa `graph_value` + `canonical_json` dùng dumper đệ quy + escape `ensure_ascii` như Python; thêm 2 unit test regression. Đây là lý do chính khiến graph diff #19 trước fix.
2. **`cortex-graph-writer/src/store/falkordb_store.rs::create_indexes`** — swallow "already indexed" chỉ match `ClientError::Server`, nhưng FalkorDB trả qua `ClientError::Redis` (`redis: "Attribute": 'id' is already indexed`) ⇒ binary fail trên **mọi re-run vào graph có sẵn index** (trường hợp chuẩn của topology: cleanup+rewrite mỗi sync). Sửa: match theo Display text (Python driver swallow mọi variant). Đã mở rộng phần docs cho mỗi fix.
3. Kèm 4 lint clippy đang có sẵn trong `cortex-graph-writer` (empty-line-after-doc ở `query_contract.rs`, `for_kv_map` × 2 ở `language_writer.rs`) để `clippy -p analyzer-topology -D warnings` sạch vì nó lint cả path deps.

## Deviation nhỏ có chủ đích (ghi nhận)

- Provider `neo4j`: Rust không có driver — trả `graph_writes: {}` (tương đương driver-None của Python); embedded `--falkordb-path` fail-closed như các analyzer Rust khác.
- Journal attach + ProjectRegistry defaults (`apply_project_registry_defaults`) là plane orchestrator/Python-side; binary dùng precedence flag → env → project_id → `hyper_graph` như `AnalyzerArgs::open_store` của framework.
- Message lỗi malformed **JSON** manifest: text của `json.JSONDecodeError` (Python) vs `serde_json` khác nhau — chỉ ảnh hưởng message của diagnostic `malformed_descriptor` cho JSON hỏng (không xuất hiện trong fixtures; XML đã byte-parity qua expat message mapping).
- `ANALYZER_TOPOLOGY_DUMP_RESULT=<path>` (env-only, ngoài CLI contract) để parity script dump result JSON so 1:1 — orchestrator không bao giờ truyền.

## Files

- Mới: `rust/crates/analyzer-topology/**` (crate + 20 modules), `scripts/rust_parity/analyzer_parity_topology.py`, `plans/260915-analyzer-layer-rust-cutover/reports/phase04-topology-parity-dualrun.md` (parity gate output), report này.
- Sửa: `rust/crates/cortex-graph-writer/src/topology.rs` (+tests), `.../store/falkordb_store.rs`, `.../language_writer.rs` + `.../query_contract.rs` (chỉ lint), `rust/Cargo.lock` (member mới; file đã dirty từ trước).
- KHÔNG đụng: `rust/crates/cortex-sync/**`, `Makefile`, `rust/crates/cortex-dev/src/cmds/lifecycle.rs`, `rust/crates/analyzer-sql-family/sql-grammar/build.rs`, `.cortex/*`.

## Registry wiring (để phase registry patch — central-owner)

Primary map entry thêm vào `rust_analyzer_binaries()` trong `rust/crates/cortex-sync/src/registry.rs`:

```rust
("project_topology", "analyzer-topology"),
```

(`project_topology_analyzer()` đã route qua flip matrix từ phase-01; `rust_analyzer_binary()` sẽ resolve `analyzer-topology` trong `rust/target/release` khi `CORTEX_RUST_ANALYZER=rust`.)

## Pending trước merge

1. **Full-stock parity** (PENDING): chạy `sync_orchestrator_parity.py` opt-in rust trên stock để topology chạy cuối sync trên graph đầy đủ (red-team C6 orchestrator leg) — binary đã sẵn sàng cho leg này vì CLI contract byte-stable.
2. Report `phase04-orchestrator-leg.md` thuộc leg riêng trên.
