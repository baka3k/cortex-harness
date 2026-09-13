# Phase 10 — Scope B: Storage layer port (behavioral parity + stress)

**Ngày:** 2026-09-14 · **Scope:** `cortex_harness/storage/` (17 modules, ~5.4k LOC Python) → `rust/crates/cortex-storage/`
**Kết luận:** **PASS** — 4/4 stress scenario khớp Python trên toàn bộ 16 phép so sánh hành vi; `cargo test -p cortex-storage` 25/25 green; `cargo clippy -p cortex-storage --all-targets -- -D warnings` sạch.

## Gates

- [x] Stress suite 4 kịch bản pass, so hành vi với Python trên cùng kịch bản (16/16 OK).
- [x] `cargo test -p cortex-storage` — lib 6 + parity 14 + stress 5 = 25 tests green.
- [x] `cargo clippy -p cortex-storage --all-targets -- -D warnings` — 0 warning.
- [ ] Gate Scope A (`dev` CLI 21 commands, `dev doctor`, `dev sync`): **ngoài phạm vi báo cáo này** (Scope B storage layer).

## Kiến trúc port

Asyncio (Python) → blocking threads (Rust). Admission semaphore → `Mutex + Condvar` với permit; caller thread tự thực thi operation (Python dùng per-lane `ThreadPoolExecutor` — vì lane semaphore đã chặn đồng thời ≤ concurrency, executor không bao giờ queue thêm nên caller-thread execution tương đương quan sát được). Tất cả manifest/fingerprint dùng canonical JSON tự viết tương đương `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=True)` (escape `\uXXXX` + surrogate pair) — fingerprint khớp Python **byte-for-byte** (golden test nhúng giá trị sinh từ `.venv/bin/python`).

## Stress suite — kết quả (Python vs Rust, chạy bởi `scripts/rust_parity/storage_stress.py`)

| # | Scenario | Python | Rust | Match |
|---|---|---|---|---|
| 1 | **Lease race** — 8 process đua lease cùng instance | 1 winner / 7 losers, loser fail ngay (conflict error, không wait) | 1 winner / 7 losers, `immediate-conflict-error` | OK |
| 2 | **Generation swap, pinned reader** — publish gen2 khi reader đang pin gen1 | pinned reader vẫn thấy `generation-1`; reader mới thấy `generation-2`; `retire(gen1)` = False khi pinned / True sau unpin; active = `generation-2` | identical | OK |
| 3 | **BoundedLane saturation + deadline** | `OVERLOADED` (retryable, retry_after_ms=100); `DEADLINE_EXCEEDED`; counters accepted=5, completed=3, rejected=1, timed_out=2 | identical (cùng code, cùng counters) | OK |
| 4 | **Kill -9 giữa lease** | kernel nhả flock; stale metadata đọc được; lease recover được + acquire lại OK | identical | OK |

Chi tiết kịch bản 3: holder chiếm slot → 1 item queued → item thứ 3 bị OVERLOADED (đúng `retry_after_ms=100`) → nhả slot → item thứ 4 với deadline quá khít bị DEADLINE_EXCEEDED → **deadline đã quá hạn fail cả khi lane rảnh** (đúng semantics Python, xem "Phát hiện" bên dưới) → deadline tương lai + permit rảnh thì thành công.

### Phát hiện quan trọng trong lúc đối chiếu (đã sửa Rust cho khớp Python)

1. **Elapsed deadline luôn fail kể cả khi lane rảnh.** Python `BoundedLane.run` clamp `timeout = max(0.0, deadline - monotonic())` rồi `asyncio.wait_for(sem.acquire(), 0)` — task coroutine chưa bao giờ done tại thời điểm tạo nên `wait_for(timeout=0)` luôn raise `TimeoutError`, bất kể semaphore có permit. Bản Rust đầu tiên check permit trước rồi mới check deadline (sai). Đã sửa: deadline quá hạn → `DEADLINE_EXCEEDED` ngay cả khi permit rảnh; deadline tương lai + permit rảnh → thành công ngay. Test nhánh này ở cả hai phía.
2. **Double-decrement counter trong lane** — bản Rust đầu tiên decrement `queued_items` hai lần trên timeout path (một lần trong timeout branch, một lần trong release path). Python chỉ decrement một lần trong `finally`. Stress test bắt được (`is_idle` không bao giờ true) → đã sửa, counters chuẩn theo Python (snapshot trong error details chụp **trước** khi `finally` decrement, đúng như Python build `details=self.snapshot` trước khi raise).
3. **Race setup**: acquire+release tức thì của 8 process gần như không bao giờ chồng nhau (không bao giờ thấy conflict). Cả hai phía dùng GO-file barrier + winner **giữ lease 3s** trong suốt race window — loser conflict vẫn immediate (portalocker timeout=0 / flock LOCK_NB), đúng contract đang test.

## Python-vs-Rust behavior table (toàn bộ so sánh của script — 16/16 OK)

```
Behavior                                             Python           Rust  match
lease-race winners (8 racers)                             1              1  OK
lease-race losers (immediate conflict)                    7              7  OK
loser behavior identical                     immediate-conflict-error immediate-conflict-error  OK
pinned reader keeps old generation             generation-1   generation-1  OK
fresh reader sees new generation               generation-2   generation-2  OK
retire refused while pinned                           False          False  OK
retire allowed after unpin                             True           True  OK
OVERLOADED error code                            OVERLOADED     OVERLOADED  OK
OVERLOADED retry_after_ms                               100            100  OK
DEADLINE_EXCEEDED error code                 DEADLINE_EXCEEDED DEADLINE_EXCEEDED  OK
lane accepted counter                                     5              5  OK
lane completed counter                                    3              3  OK
lane rejected counter                                     1              1  OK
lane timed_out counter                                    2              2  OK
kill -9 lease recovered                                True           True  OK
stale metadata observable                              True           True  OK
```

Ngoài stress suite, parity unit tests (`tests/parity.rs`) khóa thêm bằng golden values sinh từ Python: `EffectiveStorageTarget.canonical_json`/`fingerprint` (local graph + remote vector), `EffectiveStorageTopology.fingerprint` + `for_generation`, `canonical_remote_endpoint` (userinfo lowercase host, default port, unix socket), `PhysicalTargetKey` (casefold + symlink resolve `/tmp`→`/private/tmp`), `resolve_storage` (`~/.cortext-harness/v1/instances/...`, ladybug path `ladybug/<owner>/<owner>.lbug/hyper_graph`, provenance `explicit-absolute-override` / `explicit-relative-anchored-to-home`), thông điệp lỗi identity (giữ nguyên literal `{value!r}` — xem bên dưới), lease conflict message, ingestion job durable store + recovery transition (`WRITING`→`AMBIGUOUS` với `owner_restarted_during_store_operation`), local qdrant boundary (create/upsert/search cosine/scroll/retrieve/count/set+overwrite payload/delete).

## Files created

```
rust/crates/cortex-storage/
  Cargo.toml                        # libc, serde/serde_json, sha2, uuid, regex, ureq(json), tempfile(dev)
  src/lib.rs                        # module map + re-exports theo __init__.py
  src/ffi.rs                        # flock(2) LOCK_EX|LOCK_NB, statvfs, gethostname (unsafe duy nhất ở đây)
  src/util.rs                       # canonical JSON (ensure_ascii), utc_now (ms/s ISO-Z),
                                    # resolve_path (posixpath._joinrealpath: symlink/loop guard),
                                    # write_atomic + dir fsync, pretty dumps (indent=2 sort_keys)
  src/errors.rs                     # BackendConnectionError + StoreError {Value/Runtime/Io/Gateway/Connection}
  src/contracts.rs                  # enums (GenerationState/IngestionJobState+terminal/OwnerLifecycle/
                                    #  GatewayErrorCode), PerformanceProfile, PhysicalTargetKey,
                                    #  GenerationManifest, FreshnessMetadata, IngestionJob, StoreHealth,
                                    #  StoreGatewayError (to_dict parity)
  src/lease.rs                      # StorageLease (flock), conflict error message parity,
                                    #  assert_owner_stopped, recover_expired_leases
  src/admission.rs                  # LaneLimits + BoundedLane (12 snapshot fields như Python)
  src/generation.rs                 # GenerationManager: allocate/publish (atomic pointer + fsync),
                                    #  pin_active (GenerationPin guard, publication→reference lock order),
                                    #  retire (tombstone + rmtree, defer khi pinned), mark_incompatible,
                                    #  compatibility metadata fence, ensure_disk_capacity (statvfs 20%)
  src/gateway.rs                    # StoreGateway: start/close/begin_drain, query/write/publish/retire,
                                    #  refresh_readiness, ingestion job state machine (transition map,
                                    #  idempotency, overload budget, cancel, wait_for_ingestion),
                                    #  ingestion-jobs.json durable + recovery transitions, health/metrics,
                                    #  get_or_create_resource (thread-local lane context)
  src/config.rs                     # ENV_* consts, validate_backend_config, validate_storage_identity,
                                    #  resolve_performance_profile, ResolvedStorage, resolve_storage
                                    #  (CLI>config>env>default, legacy keys reject), storage_overlay
  src/targets.rs                    # EffectiveStorageTarget/Topology + fingerprint,
                                    #  canonical_remote_endpoint (urlsplit parity: bracketed IPv6,
                                    #  strict port, userinfo), local/remote graph/vector target builders,
                                    #  effective_graph_target_from_env
  src/factory.rs                    # StorageFactory (force-local flip, effective targets/topology,
                                    #  get_qdrant_store local/remote, graph_driver_selection)
  src/layout.rs                     # ladybug_store_file_name (fail-closed sanitization), instance layout,
                                    #  manifest_payload/load_manifest/ensure_layout (drift validation)
  src/migration.rs                  # migrate_legacy_layout (dual lease, digest verify, marker, no-op verify)
  src/qdrant.rs                     # LocalQdrantStore boundary trên embedded JSON-file engine
                                    #  (exact brute-force cosine/dot/euclid), per-path client cache + lease
  src/qdrant_remote.rs              # RemoteQdrantStore (ureq blocking REST), (url,api_key) client cache,
                                    #  check_connection/ensure_reachable → BackendConnectionError
  src/remote_probe.rs               # ProbeResult/ProvisionResult, probe_qdrant/probe_falkordb,
                                    #  minimal RESP2 client (AUTH/PING/GRAPH.QUERY), provision helpers,
                                    #  render_provision_line
  src/runtime.rs                    # gateway registry, storage_runtime_status, begin/close drain
  tests/stress.rs                   # 4 stress scenarios + child entry + RUST summary JSON
  tests/parity.rs                   # golden parity tests (Python-generated values)
scripts/rust_parity/storage_stress.py  # chạy 4 kịch bản phía Python + Rust suite, bảng so sánh 16 điểm,
                                       # exit 1 nếu mismatch; fallback standalone nếu workspace bị contention
```

**Lưu ý repo state:** commit `19d6136` (06:05, "phase 08 web overlays", của agent khác) quét cả working tree nên vô tình commit luôn bản `storage_stress.py` lúc đó của tôi; diff ` M` hiện tại chỉ là 21 dòng fallback thêm sau. Tôi **không** `git commit` gì; `rust/crates/cortex-storage/` vẫn untracked. `rust/Cargo.lock` tự cập nhật khi build crate mới trong workspace (side effect tất yếu, các crate khác của agent khác cũng vậy).

## Exclusions / độ lệch đã ghi nhận (cố ý, có chủ đích)

| Vùng | Lý do | Thay thế |
|---|---|---|
| `qdrant.py` delegate sang `qdrant_client` local engine (HNSW, mmap, quantization) | engine nhị phân không link được vào Rust | giữ nguyên API surface (`LocalQdrantStore`, client cache theo path + `StorageLease`, error shapes) trên engine JSON-file brute-force exact search; scroll sắp theo id |
| `factory.get_falkordb_driver/get_ladybug_driver` trả driver instance | driver nằm ở `tools.graph.driver` (Python) | `graph_driver_selection()` trả enum `GraphDriverSelection` (uri/path/owner/instance giống hệt) + lỗi ladybug-remote fail-closed cùng message; `TargetSpec` thay `ProjectTargets` |
| `migration._reopen_inventory` (qdrant_client/redislite/ladybug reopen) | cùng lý do engine nhị phân | inventory = `()` cho `copied`/`verified-noop` (dry-run identical); digest + marker + dual-lease giữ nguyên |
| Gateway asyncio (`CancelledError`, `asyncio.shield`) | port blocking-thread | không có cancellation giữa chừng; `_run_sync` executor → caller thread chạy trực tiếp dưới permit; lane thread-local giữ contract `get_or_create_resource` |
| `runtime.close_active_gateways` async | đồng bộ | blocking close tuần tự, trả `(total, first_error)` khi fail |
| `remote_probe` FalkorDB dùng `FalkorDBDriver` | driver Python | RESP2 tối thiểu: AUTH/PING/GRAPH.QUERY `"RETURN 1 AS ok"` — cùng semantics; `rediss` báo unreachable có cause; `setup_remote_falkordb_schema` vẫn subprocess script Python nhưng không enforce timeout (std::process không có timeout, script tự bounded) |
| `storage_overlay` trả `BTreeMap` thay `os.environ` dict | Rust không có env đích | keys/values + logic pop/override giữ nguyên; đọc `CORTEX_STORAGE_BACKEND_FORCE_LOCAL` từ env thật |

## Suspected shared bugs (Python layer — đã STOP, không sửa Python)

1. **`validate_storage_identity` mất f-string prefix** (`config.py:202-209`): nhánh `"…; got {value!r}"` không phải f-string nên thông điệp lỗi in ra literal `{value!r}` thay vì giá trị. Cosmetic. Đã **replicate byte-parity** ở Rust + test khóa hành vi này.
2. **Elapsed deadline fail-closed bất kể permit** (`admission.py:99-113`): `max(0.0, deadline - monotonic())` + `wait_for(…, 0)` luôn `TimeoutError` kể cả khi lane rảnh — có thể gây DEADLINE_EXCEEDED ảo cho request đến sau deadline dù hệ thống nhàn rỗi. Hành vi được replicate chính xác và ghi nhận ở đây để sửa Python sau nếu muốn (đổi thành try-acquire nonblocking trước khi wait_for).
3. **Commit quét rộng** (`19d6136`): một commit của agent khác `git add` cả working tree, vô tình bring-in script của tôi giữa chừng. Không phải code bug nhưng đề xuất các agent khác commit theo path cụ thể (`git add <paths>`) thay vì `-A`.

## Cách chạy lại

```sh
# Rust suite + unit/parity tests
cd rust && cargo test -p cortex-storage
cargo clippy -p cortex-storage --all-targets -- -D warnings

# Python-vs-Rust parity (harness venv, chạy cả hai phía + bảng so sánh)
.venv/bin/python scripts/rust_parity/storage_stress.py
```
