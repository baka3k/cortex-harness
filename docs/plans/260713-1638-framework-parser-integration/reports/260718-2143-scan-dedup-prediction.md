---
type: hi-predict report
date: 2026-07-18
depth: deep
verdict: CAUTION
---

# HI Predict: Tối ưu scan Java/Spring và loại bỏ duplicate graph

## Executive Summary

Không nên chọn **Java hoặc Spring** như hai parser loại trừ nhau. Java/Kotlin phải tiếp tục sở hữu graph canonical (`File`, `Class`, `Function`, `CALLS`); Spring và các framework khác là overlay many-to-many. Thay đổi nên làm là parse source canonical một lần, chia sẻ IR đã version hóa cho overlay, và collapse semantic facets ở query layer.

Verdict là **CAUTION**: hướng kiến trúc đúng, nhưng identity canonical hiện chưa project/root-scoped và có nguy cơ cross-project overwrite. Cần sửa scope/identity, path normalization và cleanup boundary trước khi rollout shared IR/DAG.

## Current-State Findings

1. Repo đã phân biệt primary parser và framework overlay trong `incremental_sync.py`; cùng file Java đi vào cả primary và nhiều overlay là hành vi có chủ ý.
2. Spring auto path chỉ ghi `semantic_facts` và relationships; `language_facts` không được ghi thành canonical nodes. Các node `Controller`, `Service`, `ApiEndpoint`, ... là semantic facets nối về Java symbol qua `SEMANTIC_OF`, không phải một Java graph thứ hai.
3. Duplicate công việc scan là có thật:
   - Java tự walk/parse/index source.
   - Orchestrator chạy detector framework.
   - Spring analyzer lại discover module, walk source, adapter-read source và regex-scan source.
4. Trên một Spring module, static call-path cho thấy tối thiểu khoảng sáu lượt walk source/root trước khi cộng các overlay khác: Java inventory; Spring detector tại orchestration (root + source); Spring detector trong analyzer (root + source); Spring source walk.
5. Validation cũ trên fixture nhỏ đo tổng 27.131 giây: Java 6.706 giây, Spring 0.896 giây, Servlet/JSP 6.531 giây, MyBatis 6.411 giây. Đây không phải benchmark production nhưng cho thấy process/detector/overlay overhead đáng kể.
6. Java incremental vẫn có các phần O(F): walk toàn bộ Java files, đọc để dựng import graph, rồi load/parse payload toàn project để dựng resolution index. Chỉ chia sẻ AST cho Spring sẽ chưa đủ biến incremental thành O(delta).
7. Identity là rủi ro correctness/security: primary writers `MERGE` theo `id`, sau đó mới gán `project_id`; Java file ID chủ yếu là relative path và class ID là qualified symbol. Hai project/root có cùng path/symbol có thể overwrite nhau.
8. Test target chạy được 8/9; test framework fixture fail vì fixture `tests/fixtures/framework-java-app` đã bị xóa ở commit `9769ccd` nhưng test vẫn còn. Validation hiện tại vì vậy chưa bảo vệ đầy đủ anchor/cross-platform behavior.

## Consensus Agreements

- Giữ base-language ownership + non-exclusive framework overlays; không thay Java bằng Spring.
- Tạo một inventory chuẩn hóa cho mỗi scan và dùng lại ở mọi detector.
- Shared IR phải là artifact bất biến, content-addressed, schema-versioned; không truyền raw AST object giữa subprocess.
- Overlay chỉ được đọc canonical IR và emit framework facts/edges; không sở hữu hoặc cleanup canonical nodes.
- Identity phải gồm project/repository/root scope trước khi migration graph lớn.
- Semantic facets nên được collapse khi query/UI, nhưng vẫn giữ raw facet view cho phân tích chuyên sâu.
- Đo baseline trước; rollout theo phase và có gate về performance, recall, orphan anchors và data isolation.

## Conflicts and Resolutions

| Topic | Architect | Security | Performance | UX | Devil's Advocate | Resolution |
| --- | --- | --- | --- | --- | --- | --- |
| Xóa node framework để hết duplicate | Giữ facets, collapse query | Không physical-merge theo name/path | Collapse giảm payload nhưng có DB cost | Mặc định hiển thị một canonical entity | “Duplicate” có thể là semantics hợp lệ | Giữ physical facets; collapse theo canonical scoped ID, không heuristic |
| Shared in-memory ScanContext | Không hợp subprocess hiện tại | Tăng trust/cache boundary | RSS và reliability risk | Không ảnh hưởng trực tiếp người dùng | Có thể là refactor quá lớn | Bắt đầu bằng persisted IR manifest; cân nhắc in-process sau benchmark |
| Full capability DAG ngay | Declarative DAG là đích đến | Phải allowlist/cycle-check/resource-limit | Quick wins trước DAG rewrite | Scan plan cần dễ hiểu | Planner có thể thành god component | Tách registry/planner/executor; triển khai inventory + metrics trước, DAG sau |
| Ưu tiên performance hay identity | Identity trước | Identity là critical | Instrument trước, identity migration cần đo | Tránh kết quả lẫn project | Đừng biến optimization thành migration vô hạn | P0 metrics song song với thiết kế scope; không rollout shared graph writes trước scoped identity |
| Gắn `Controller` label trực tiếp lên `Class` | Không khuyến nghị ban đầu | Cleanup/provenance phức tạp | Có thể giảm node count | Dễ hiểu hơn ở UI | Có thể đơn giản hơn facet nodes | Không đổi physical model ở phase đầu; cung cấp canonical projection với `facets[]` |

## Risk Summary

| Risk | Severity | Persona | Mitigation |
| --- | --- | --- | --- |
| Cross-project overwrite/exposure do `MERGE` chỉ theo `id` | Critical | Security, Architect | `scan_scope_id` + globally unique `node_uid`; scoped edge match; composite constraints; migration sang namespace mới rồi audit |
| Cleanup overlay xóa quá rộng hoặc mất graph cũ khi write fail | High | Security, Architect | Scope theo capability/generation/source; delete budget; stage/promote hoặc transaction; overlay không xóa canonical |
| Symlink/path traversal và resource exhaustion từ repo không tin cậy | High | Security | Common scanner dùng `lstat`, containment, file/byte/depth/time budgets; không execute build/annotation processors |
| Cache poisoning/collision/race | High | Security | SHA-256 key gồm scope/content/parser/version/options; schema validation; random temp + lock; cache tách trust domain |
| Orphan `SEMANTIC_OF` do path separator/ID mismatch | High | Architect | POSIX path normalization tại source; shared ID factory; anchor validation metric/test trên Windows/Linux |
| O(F x frameworks) detector/source walks | High | Performance | Single ProjectInventory, module detector fingerprints, pass exact manifests vào overlay |
| Java incremental vẫn O(F) | High | Performance | Persist import/dependency/symbol indexes và invalidate theo changed dependency closure |
| Query collapse làm giảm unique recall khi LIMIT trước collapse | Medium | Performance, UX | Aggregate/collapse trước LIMIT hoặc oversample; measure unique-symbol recall@k |
| Shared IR schema làm coupling framework với Java parser | Medium | Devil's Advocate | Minimal normalized JVM IR, schema versioning, compatibility adapter, conformance tests |
| Stale fixture làm validation report không tái lập được | Medium | Architect | Khôi phục/đổi fixture hoặc xóa test stale; thêm E2E anchor and deletion-only tests |

## Persona Details

### Architect

Concerns:

- Current subprocess boundary không hỗ trợ shared memory an toàn.
- Canonical identity chưa project/root-scoped.
- Java giữ native relative path trong một số ID, Spring chuẩn hóa slash; anchor có thể orphan trên Windows.
- `prerequisite_parsers` + numeric `order` chưa biểu diễn dependency/capability contract.
- Cleanup-before-write không atomic.

Recommendations:

- Sửa path/identity trước; giữ `symbol_id` compatibility nhưng writer dùng `node_uid` scoped.
- Tạo `ProjectInventory` và declarative capability DAG (`role`, `requires`, `consumes`, `produces`, `cleanup_strategy`).
- Java/Kotlin export versioned symbol IR; overlays nhận `--ir-manifest`.
- Giữ semantic facts và collapse ở query layer.

Confidence: high.

### Security

Threats:

- Cross-project node overwrite/data mixing là critical.
- Cleanup blast radius, symlink reads, unbounded parsing, cache poisoning và dynamic Cypher interpolation là high risks.
- Optional project scope trong query có thể biến collapse view thành cross-project view.

Mitigations:

- Mandatory scope ở storage/query/cleanup; composite uniqueness hoặc scoped global key.
- Immutable allowlisted capability registry và relationship types.
- Root-contained scanner với budgets; cache content-addressed và isolated.
- Migrate sang graph/namespace mới, audit collision trước cutover.

Severity: critical, mitigatable. Confidence: high.

### Performance

Bottlenecks:

- Mọi framework detector tự scan project; analyzer lại scan lần nữa.
- Spring đọc source trong adapter và source scanner dù chỉ `semantic_facts` được ghi graph.
- Java incremental rebuild inventory/import/resolution indexes theo toàn project.
- Subprocess startup và serialization là overhead đáng kể trên repo/fixture nhỏ.

Expected impact:

- Single inventory + detector fingerprints có thể giảm metadata I/O 2–8x trên monorepo lớn; đây là hypothesis cần benchmark.
- End-to-end target hợp lý để gate rollout: giảm ít nhất 30% warm incremental wall time, không giảm recall. Mức 20–50% là dải hypothesis, không phải cam kết.

Alternatives:

- Ưu tiên persisted IR/cache qua process boundary thay vì chuyển ngay sang một process lớn.
- Tối ưu Java dependency/symbol index trước hoặc song song; nếu không shared overlay IR chỉ xử lý một phần bottleneck.

### UX

Issues:

- CLI hiển thị `java, spring` như hai “tools” ngang hàng làm người dùng hiểu đây là scan duplicate.
- Kết quả search trả canonical và facet riêng, dù cùng source concept.
- Trạng thái skip/fallback/cache reuse chưa diễn đạt rõ scan plan.

Edge cases:

- Pure Java không Spring; multi-framework module; manual `--parsers spring`; deletion-only update; missing canonical prerequisite; overlay failure after canonical success.

Recommendations:

- Hiển thị trước khi chạy: `Primary: java`; `Overlays: spring (reuses java IR)`; `Files inventoried/read/parsed`; cache hit rate.
- Mặc định query trả một canonical entity với `facets[]`; thêm raw mode.
- Báo rõ partial success và orphan anchors; output không phụ thuộc màu để dùng tốt trong CI/terminal.

### Devil's Advocate

Assumptions challenged:

- Hai node cùng trỏ đến một class không nhất thiết là duplicate; `Class` và `Controller` là hai semantics khác nhau.
- Shared IR không tự động giải quyết phần O(F) trong Java incremental.
- Capability DAG/in-process context có thể tạo một god orchestrator và coupling schema lớn hơn lợi ích thực tế.

Simpler alternatives:

- Đầu tiên thêm metrics, single inventory, detector cache, fix deletion-only scan và deprecate composite wrapper khỏi discovery.
- Giữ physical graph hiện tại, chỉ collapse read model; benchmark trước khi migration node model.

Worst case:

- Một migration “dedupe” merge facets vào canonical nodes, mất provenance/cleanup semantics, đồng thời shared cache bị stale và scoped-ID migration làm orphan edges. Pipeline nhanh hơn nhưng graph sai và không rollback được.

## Recommended Target Architecture

```text
Git diff / full snapshot
        |
        v
ProjectInventory (one normalized walk, hashes, module evidence)
        |
        v
Capability Planner (DAG, allowlisted, cycle checked)
        |
        +--> JVM canonical parser --> versioned Symbol IR --> canonical graph + vectors
                                      |          |
                                      |          +--> Spring overlay
                                      |          +--> Servlet/JSP overlay
                                      |          +--> MyBatis overlay
                                      |
                                      +--> persisted dependency/symbol indexes
        |
        v
Generation/scoped writers --> raw facet graph --> canonical query projection (`facets[]`)
```

IR cache key:

```text
(scan_scope_id, normalized_path, content_sha256,
 parser_name, parser_version, ir_schema_version, config_hash)
```

Minimum IR fields: normalized source ID, file hash, package/module, class/method signatures, annotations with raw/resolved args, modifiers, source spans, imports/references, parser diagnostics and provenance. Do not serialize raw tree-sitter objects.

## Numbered Recommendations

1. **Instrument before changing behavior.** Record per-stage wall time, walks/stats/opens/bytes, parse and IR cache hit rate, scanned-to-changed amplification, peak RSS, graph create/match counts, orphan anchors and query recall/latency.
2. **Introduce `scan_scope_id` and one canonical ID factory.** Include project/repository/root and normalized POSIX path. Add constraints and migrate in a new namespace/graph before cutover.
3. **Build one `ProjectInventory`.** Prefer Git tracked-file inventory plus explicit untracked policy; detectors consume this inventory instead of calling `os.walk` independently.
4. **Cache module detection.** Fingerprint build/config files, detector version and relevant changed source signals. Re-evaluate only affected modules and changed inactive candidates.
5. **Fix incremental correctness quick wins.** Deletion-only runs must not fall through to full unchanged-source scans; use stage/promote for overlay replacement; restore a valid framework fixture.
6. **Persist Java dependency/symbol indexes.** Make warm incremental proportional to changed files plus dependency closure before claiming O(delta).
7. **Export normalized JVM IR.** Extend canonical Java/Kotlin parser output with annotations and spans; overlays consume `--ir-manifest`, with temporary direct-self-parse fallback only for diagnostics.
8. **Replace numeric order with capability DAG.** Keep planner declarative and executor thin; encode Java -> Spring, Java -> Servlet/JSP, Java/Kotlin -> MyBatis dependencies explicitly.
9. **Keep framework facets but collapse results.** Default to canonical entity + `facets[]`; raw view remains available. Collapse before LIMIT and only within mandatory scope.
10. **Deprecate composite wrappers.** Auto orchestration must invoke canonical parser once and overlays once. Existing `spring_java_analyzer.py` is compatibility-only and should delegate to the shared planner or be retired.

## Rollout and Verification Gates

### Phase 0 — Baseline and correctness

- Add metrics and scan-plan output.
- Repair fixture coverage.
- Add cross-project collision, Windows anchor, symlink, deletion-only and overlay-failure tests.

### Phase 1 — Scope and inventory

- Introduce scoped IDs/constraints and migration audit.
- Create single inventory and detector fingerprints.
- Gate: zero cross-project overwrite; zero unexpected orphan anchors.

### Phase 2 — Delta indexes and shared IR

- Persist Java dependency/symbol indexes.
- Export JVM IR; migrate Spring first.
- Gate: each changed source parsed once; no framework emits canonical nodes; at least 30% warm incremental improvement on representative benchmark.

### Phase 3 — DAG and read model

- Enable declarative planner and safe overlay concurrency where dependencies allow.
- Add canonical facet projection and raw mode.
- Gate: no loss in unique-symbol recall@k; query p95 and graph write volume remain within target.

### Phase 4 — Deprecation

- Remove composite wrappers from supported auto paths.
- Remove overlay self-parse fallback after compatibility telemetry reaches zero.

## Evidence Bundle

### Coverage

- `mind_mcp`: unavailable in this session.
- `graph_mcp`: used; parser capabilities and semantic code matches confirmed Java/Spring share the backend while exposing different profiles.
- Serena: skipped because graph search plus direct code inspection was sufficient.
- `rg` / direct inspection: used for orchestration, detectors, parsers, writers, tests, docs and historical validation report.
- Targeted tests: 8 passed, 1 failed because the referenced fixture directory is absent.

### Key Evidence

- Primary/overlay registries and prerequisites: `code-tiny/tools/sync/incremental_sync.py:74`, `:101`.
- Many-to-many overlay routing: `code-tiny/tools/sync/incremental_sync.py:544`.
- Primary then overlay execution: `code-tiny/tools/sync/incremental_sync.py:1347`, `:1490`.
- Spring repeated discovery/read/scan: `code-tiny/tools/spring/pipeline.py:40`, `:77`, `:85`; `source_scanner.py:100`.
- Spring graph writes only semantic facts: `code-tiny/tools/spring/spring_analyzer.py:166`.
- Semantic anchoring: `code-tiny/tools/spring/extractors/core.py:111`, `:164`.
- Unscoped primary merge: `code-tiny/tools/graph/writer/language_writer.py:809`, `:892`.
- Framework test explicitly routes one Java file to multiple overlays: `tests/test_incremental_sync_framework_overlays.py:91`.
- Intended design docs: `docs/HARNESS_WORKFLOW.md:169`; `plans/260713-1638-framework-parser-integration/plan.md:16`.
- Historical timing: `plans/260713-1638-framework-parser-integration/reports/validation-report.md:25`.

## Next Step

Với verdict **CAUTION**, không bắt đầu bằng việc “chỉ chạy Spring thay Java” hoặc merge physical nodes. Bước tiếp theo nên là một implementation plan cho Phase 0–1: instrumentation, scoped identity/path contract, single inventory và regression tests; sau đó benchmark mới chốt phạm vi shared JVM IR.
