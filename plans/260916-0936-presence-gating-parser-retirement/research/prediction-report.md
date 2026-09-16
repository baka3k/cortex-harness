/graph_state.rs# Prediction Report — Smart Language Detection cho `dev sync code`

- **Date:** 2026-09-16
- **Depth:** deep (5 persona độc lập, MCP + code-verified)
- **Verdict:** ⚠️ **CAUTION** — tiến hành, nhưng **không** theo hình dạng "bộ detect gần đúng (approximate) mới"; chuyển sang thiết kế đã được reframing (mục Recommendations).
- **Proposal (gốc):** Thay cơ chế `--parsers auto` (chạy toàn bộ analyzer) bằng cơ chế detect gần đúng các ngôn ngữ có trong scan root để chỉ chạy analyze cho ngôn ngữ đó. Triệu chứng động cơ: quét source Android mà graph vẫn dính COBOL. Người dùng yêu cầu cân nhắc kỹ các sai số.

---

## Executive Summary

Năm persona phân tích độc lập trên bằng chứng code đã verify. Kết quả bất ngờ nhất đến từ Devil's Advocate và được tôi xác thực lại trực tiếp: **selection theo presence (count>0 mỗi parser) đã tồn tại sẵn và chính xác 100%** — orchestrator walk toàn cây, route từng file qua `_select_parser_for_path`, và skip parser không có file nào ở **mọi mode**, kể cả full scan (`orchestrator.rs:1186` đặt `changed_paths = all_source_paths` khi full scan; gate `:1487` không có điều kiện full_scan). Một "bộ detect gần đúng mới" vì vậy chỉ có thể **trừ bớt** parser so với cơ chế chính xác hiện có — tức lỗi sai duy nhất nó có thể mắc thêm là **false negative: mất dữ liệu graph một cách im lặng** (exit 0, graph thiếu cả subsystem), tệ hơn hẳn tiếng ồn COBOL mà nó định sửa.

Trong khi đó, triệu chứng COBOL-in-graph của bạn **không nằm ở selection** mà ở hai chỗ khác: (1) chỉ cần **1 file rác trùng extension** (`.cpy`/`.copy` — copybook COBOL, hay gặp trong fixture/vendored/docs) là cobol analyzer được spawn và ghi node rác vào graph + collection Qdrant riêng; (2) **không có cơ chế retirement theo parser** — một khi COBOL đã ghi vào graph, việc bỏ cobol khỏi run set không bao giờ dọn nó (cleanup hiện tại chỉ theo từng file; topology cleanup chỉ phủ `topology_owned`).

Kết luận consensus: **làm đúng ý đồ của bạn nhưng bằng thành phần có sẵn, chính xác, không heuristic chấm điểm** — và kèm hai mảnh bắt buộc: rule copybook cho case file rác, và per-parser retirement cho case rác tồn đọng.

---

## Agreements (≥4/5 persona đồng thuận)

1. **Per-parser retirement là mảnh bắt buộc** (Architect, Security, Devil's Advocate; UX gián tiếp qua status/reporting): gating mà không retirement thì COBOL cũ vẫn nằm trong graph + Qdrant collection — triệu chứng động cơ **không được sửa**. Security nâng cấp thành vấn đề exposure: secret đã xoá khỏi repo vẫn còn nằm trong vector cũ và vẫn searchable.
2. **Detection phải là composition của các primitive có sẵn** (Architect, Performance, Devil's Advocate; Security về việc reuse guards): reuse `walk.rs` (canonical walk đã chạy mỗi lần sync) + `routing.rs` (`_select_parser_for_path` — đã gồm content-sniff JP1) + `frameworks.rs` (evidence markers). Cấm tạo taxonomy extension thứ 4.
3. **Bất đối xứng rủi ro FN >> FP** (5/5): FP = tốn vài phút + node rác nhìn thấy được; FN = mất dữ liệu graph không nhìn thấy được. Mọi thiết kế phải bias về default-include; cấm threshold loại bỏ ngôn ngữ nhỏ (1 file `.sql` migration, 1 file `.pyx`).
4. **Escape hatch + observability** (Architect, UX, Performance, DA): `--parsers` explicit giữ nguyên byte-for-byte; thêm alias `all`; mỗi lần skip phải kèm evidence (`routed=0`); detection phải thấy được trước khi chạy dài và trong `--dry-run`.
5. **Incremental đã tự tối ưu; full scan mới là mục tiêu** (Performance, DA): 4 gate skip-parser-rỗng đã tồn tại ở primary (:1487), framework evidence (:1744), embedding (:2097), message lane (:3072). Chi phí còn lại của full scan nằm ở children re-walk cây tuần tự (O(children × N)) và message-scan full mode (tối đa 17 lần walk) — các lever lớn hơn detection.

## Conflicts & Resolutions

| Topic | Architect | Security | Performance | UX | Devil's Advocate | Resolution (rationale) |
|---|---|---|---|---|---|---|
| Có cần "bộ detect gần đúng mới" không? | Cần, đặt cạnh `tsdetect.rs`, composition từ primitive có sẵn | Cần nhưng coi selection là integrity-relevant, phải audit | Chi phí detect phải piggyback trên walk có sẵn; thắng lợi chính bị nói quá | Cần, nhưng phải visible + override được | **Không cần** — gating count>0 chính xác đã tồn tại (:1186, :1487) | **DA thắng** (được verify lại trực tiếp trên code): mọi heuristic mới chỉ có thể trừ parser → FN im lặng là lỗi duy nhất nó thêm vào. Ý đồ người dùng vẫn được phục vụ bằng exact-set + 2 mảnh companion. |
| Threshold số lượng file để.include | Bias recall, threshold ≥1 | — | Threshold ≥1 | Low-confidence → chạy luôn + warn | Mọi threshold: ≥1 = hệt hiện tại; >1 = tụt ngôn ngữ nhỏ | **Không dùng threshold loại bỏ.** Inclusion = routed-count > 0 (như hiện tại); confidence chỉ được dùng để *gán nhãn* hiển thị, không để exclude. |
| Detection chạy ở parent (dev CLI) hay child (cortex-sync)? | Child — tránh nhân bản routing logic cross-crate | — | Child, tái dùng walk | Parent — để `--dry-run` nhìn thấy | — | **Child (cortex-sync) sở hữu detection**, expose kết quả vào summary JSON + thêm mode detect-only để `dev sync code --dry-run` gọi. Thỏa mãn cả hai mà không duplicate routing. |
| Detection fail thì sao? | — | Fail-open về all-parsers khi dính cap | — | Fail-open, cấm tuyệt đối "ok với 0 analyzer" | — | **Đồng thuận, không conflict**: fail-open to all + log to. Availability > precision. |
| Retirement có nguy hiểm không? | Bắt buộc, làm luôn | Bắt buộc (staleness = exposure) | — | Status `not-detected` phải tách `skipped` để postmortem | Nguy hiểm: detector flicker + retirement = vòng xoáy xoá dữ liệu hàng loạt | **Cả hai đúng → retirement có guard**: chỉ chạy ở chuyển tiếp full-scan/`--reconcile` (không chạy trên incremental flicker), ghi evidence vào summary, yêu cầu 2 lần liên tiếp non-detected hoặc reconcile tường minh. |

---

## Risk Summary

| # | Risk | Severity | Persona | Mitigation |
|---|---|---|---|---|
| R1 | Heuristic detection sai → FN: graph mất cả subsystem, exit 0, không signal | **High** | DA, UX | Cấm heuristic chấm điểm; dùng exact routed-count từ routing hiện có; default-include; post-check cảnh báo khi % file không route vào parser nào > 5% |
| R2 | Không có retirement → node/collection cũ tồn đọng mãi (triệu chứng COBOL không được sửa; secret đã xoá vẫn nằm trong vector, searchable) | **High** | Security, Architect, DA | Per-parser retirement: purge graph node theo parser-ownership (mở rộng `cleanup.rs` vượt per-file) + delete Qdrant collection `…_{parser}_functions`; guard flicker bằng full-scan/reconcile-only |
| R3 | Message lane bypass `parser_filter` (đã verify: `enabled_parsers` = `PARSER_ITERATION_ORDER ∩ message_enabled_parsers`, không giao filter) | Medium | Architect, Performance | Một canonical detected set gates cả 3 pass (primary/embedding/message); sửa lane intersect filter |
| R4 | Detection opacity: user không tranh luận được skip; CI drift | Medium | UX | In detection + evidence trước run & trong dry-run; status `not-detected` (kèm reason + counts) tách `skipped`; summary JSON machine-readable; `--strict-detection` cho CI |
| R5 | Descriptor do attacker chế (build.gradle/pom giả) lái selection → resource exhaustion; `read_limited_lower(…, usize::MAX)` trong frameworks.rs đọc không bound | Medium | Security | Chỉ substring/regex match descriptor (cấm parse YAML/eval gradle); cap size/count/bytes; reuse guard `detector.rs` (symlink skip, path-escape, MAX_DESCRIPTOR_FILES); fail-open khi dính cap; fix bound đọc file |
| R6 | Ngôn ngữ sniff-only (JP1 qua `.txt`) invisible với detection thô → FN | Medium | DA | Detection = output của routing (đã gồm sniff), không được là lớp thô hơn routing |
| R7 | Cache detection bị poison / drift | Low-Med | Security, UX | Nếu cache: key bằng tree fingerprint (content), lưu ngoài cây quét, validate khi load; mặc định in-memory per-run như cache hiện có của `AndroidPathClassifier` |
| R8 | Đổi ngữ nghĩa `auto` phá script/CI hiện hữu | Medium | UX | `auto` vẫn accepted + notice one-time; thêm keyword `all` khôi phục hành vi cũ; explicit list giữ nguyên; sửa `sync.rs:1038` hardcode `auto` cho multi-folder path |

---

## Per-Persona Detail

### Architect (confidence: high)
- **Concerns:** triệu chứng là full-scan-shaped; detection một mình không sửa residue (không retirement); 3 pass đang gating thiếu nhất quán (message lane bỏ `parser_filter` — latent bug); sai layer risk nếu nhét I/O vào `selected_parsers()` (pure function); nguy cơ taxonomy extension thứ 4; JP1 sniff tốn O(all .txt bytes) nếu detect walk riêng; FN là sticky không tự-heal vì `parser_filter` được check trước manifests (:1473); walked-set phụ thuộc mode (hybrid incremental chỉ có git-diff paths) → cần persisted state; `sync.rs:1038` hardcode `auto`; `parser_auto_mode` đang mang ngữ nghĩa "permissive skip" cần phân loại lại; branch `feat/change-db` (Ladybug) đụng vùng graph-writer — cần coordinate.
- **Recommendations:** module `detect_parsers` trong cortex-sync (sibling `tsdetect.rs`) = keys(`group_paths_by_parser`(walked)) ∪ framework evidence + jp1 sniff, chỉ active khi `parser_auto_mode`; union detected set với changed/deleted groups để FN tự-heal; persist detected set + evidence theo root hash; per-parser drop-out cleanup; gate cả 3 pass từ một set; alias `all`; giữ explicit-list byte-identical; bias recall; cache `is_jp1_file` per path.

### Security (severity tổng: medium)
- **Threats:** descriptor-content injection lái selection (medium — cùng class với marker sniffing có sẵn); unbounded content read (`usize::MAX`) → OOM DoS (medium); **stale retrieval data là rủi ro chính (high)** — collection `{project}__{sha1}__{parser}_functions` + graph node không bao giờ bị dọn khi parser bị skip/flap; secret đã xoá vẫn embedded + searchable; cache poisoning nếu thêm disk cache (low→medium); TOCTOU detection→spawn (low, guard sẵn); walk hazards nếu viết walk mới bỏ guard; gradle evaluation = RCE nếu ai đó "cải tiến" (critical-if-done, low as designed); không có auth/data-exposure thay đổi trong context local.
- **Mitigations:** substring-only descriptor matching; cap + reuse guards + fix `read_limited_lower`; record detected set + evidence vào summary (auditable); retirement collection + graph purge khi parser bị loại (hoặc detection-epoch GC); cache content-keyed ngoài cây quét; bound walk + fail-open; children re-verify file existence (giữ hành vi hiện có).

### Performance (estimates có gắn nhãn)
- **Bottlenecks:** full scan spawn tuần tự 1 child/parser, mỗi child re-walk toàn cây → O(children × N); message-scan full mode ≤17 walk tuần tự; parent đã walk cây mỗi run (+1 verify walk) — detection không được thêm walk nữa; JP1 sniff + compile `Regex::new` per-line per-.txt là chi phí per-file đang tồn tại.
- **Metrics impact (est.):** Android-only full sync hôm nay ~6–12 children (đã extension-gated) → 4–6, **−5–30% wall time**; polyglot monorepo ~20–30 → ~10 spawns, **−30–60% parse phase**; incremental **~0%** (gate đã skip hết, detection là pure overhead); detection piggyback ext-histogram: +1–3% một walk, KBs RAM; detection walk riêng: +giây SSD local, +phút trên NFS; FN flip → full resync là operation đắt nhất.
- **Alternatives:** piggyback histogram trên walk có sẵn (zero extra IO); cache detected set theo inventory snapshot, union với previously-synced parsers; lever lớn hơn: pass manifests/inventory cho full-mode children (bỏ O(children×N)); single-walk message scan bucket theo extension.

### UX
- **Issues:** `auto` đổi ngữ nghĩa im lặng → cần notice one-time + keyword `all` (hiện `all` chưa tồn tại — lỗi "Unsupported parser(s)" mà không liệt kê tên hợp lệ); detection phải thấy trước run dài & trong `--dry-run` (hiện dry-run chỉ echo command, không thấy detection nằm ở child); banner `[sync-code all] analyzers=24` sẽ nói dối khi detection bật; mọi skip cần evidence; escape hatch hiện tại đòi thuộc ~24 tên parser → thêm `auto,+cobol` add-back; status `not-detected` tách `skipped` trong summary; detection fail fail-open, cấm "ok với 0 analyzer"; post-check unmatched files kèm top extensions.
- **Edge cases:** low-confidence → chạy luôn + warn; cây không nhận diện được → warn + fallback all; multi-folder mỗi folder một set → summary per-folder; cây lai (Android vendoring COBOL sample) → feedback exclusion theo path hoặc config pinning; CI determinism (pure function của tree, stable order) + `--strict-detection`; progress cho detection trên cây lớn.
- **A11y:** không tín hiệu bằng màu; giữ words cạnh icons (pattern `✓ ↷ – ✗` sẵn có); output cuối static + greppable; detected/skipped sets nằm trong JSON máy đọc được.

### Devil's Advocate
- **Assumptions challenged:** (1) "full scan chạy tất cả parser vô điều kiện" — **SAI**, gate count>0 chính xác đã chạy mọi mode; (2) "waste là spawn/walk analyzer của ngôn ngữ vắng mặt" — mostly false post-:1487; pain thật là **junk node từ 1 file rác trùng extension**, thứ presence-detection không sửa được; (3) "sai số detect chấp nhận được" — FN là mất dữ liệu im lặng với exit 0, strictly worse; detection mới chỉ có thể TRỪ parser nên lỗi mới duy nhất nó thêm vào là data loss; (4) "gating sửa triệu chứng động cơ" — KHÔNG: stray `.cpy` vẫn count=1, rác cũ không retirement vẫn nằm đó; (5) "cần cơ chế mới" — không, counts đã free từ walk+routing hiện có.
- **Simpler alternatives:** observability-only (log skipped-with-count — hiện skipped biến mất trước khi vào summary); **routing rule copybook**: `.cpy/.copy` → cobol chỉ khi root có ≥1 `.cbl/.cob` program (copybook không program là inert; real COBOL project luôn có program → zero FN class); `--parsers` explicit dùng ngay hôm nay (zero code); nếu gating thay đổi → retirement là companion bắt buộc kèm confirmation.
- **Worst case:** detection heuristic ship vào legacy monorepo (COBOL/SQL layout lạ, JP1 trong .txt) → detector fail, full scan skip quietly, sync báo success, retrieval thiếu cả subsystem; trong khi Android COBOL junk vẫn nguyên (stray .cpy + no retirement); team thêm cleanup cho deselected parser → detector flicker xoá live data hàng loạt.

---

## Verdict: CAUTION

Áp dụng conflict rules của skill: Devil's Advocate đã challenged assumption gốc **bằng bằng chứng code đã được xác thực lại độc lập** (không phải assumption chưa validate — mà là assumption sai) → không thể GO. Nhưng không có Critical unmitigatable: mọi risk đều có mitigation cụ thể, và thiết kế thay thế rõ ràng, nhỏ hơn đề xuất gốc → không STOP.

**Điều KHÔNG được làm:** bộ "smart approximate detection" heuristic (chấm điểm signal, threshold ngôn ngữ, marker scoring mới).

**Điều ĐƯỢC làm (reframed scope — đạt đúng ý bạn, sai số ≈ 0 ở selection):**

## Numbered Recommendations

1. **Không xây detector mới — formalize cái đã có.** Presence-gating (routed-count > 0 qua `_select_parser_for_path` trên canonical walk) đã chạy đúng ở mọi mode (`orchestrator.rs:1186`, `:1487`, `:1744`, `:2097`, `:3072`). Việc còn lại là *expose* nó: emit `[impact] parser=cobol routed=0 → skipped-with-evidence` (hiện parser skip biến mất trước khi vào summary — `continue` ở :1489 đứng trước push :1529) và status riêng `not-detected` vs `skipped`. *Rationale:* đạt mục tiêu "không chạy analyzer thừa" mà thêm đúng 0 lớp heuristic, 0 rủi ro FN.
2. **Sửa gốc rễ triệu chứng COBOL bằng routing rule copybook:** `.cpy/.copy` chỉ route về cobol khi root có ≥1 `.cbl/.cob` program file (copybook không có program là inert; mọi dự án COBOL thật có program file → class false-negative ≈ 0). Bảo vệ thứ hai trong analyzer: suppress write graph/Qdrant khi parse được 0 program file. *Rationale:* đây là lớp "detect" đúng nghĩa mà triệu chứng của bạn cần — chính xác, deterministic, zero heuristic scoring.
3. **Per-parser retirement (bắt buộc, không kèm thì mục tiêu của bạn không đạt):** khi một parser từng sync root này rơi khỏi effective set (hoặc routed-count về 0 ở full scan), purge graph node theo parser-ownership (mở rộng `cleanup.rs` vượt per-file) + delete Qdrant collection `…_{parser}_functions`. Guard chống flicker: chỉ kích hoạt ở full-scan/`--reconcile`, ghi evidence vào summary. *Rationale:* không có mảnh này, COBOL đã ghi vào graph từ các sync trước sống mãi — và secret đã xoá vẫn searchable (risk R2, severity high).
4. **Một canonical parser set gates cả 3 pass** (primary/embedding/message lane); sửa message lane intersect `parser_filter` (latent divergence đã verify). *Rationale:* tránh ba cơ chế gating tự phát triển lệch nhau.
5. **Observability + escape hatch:** alias `--parsers all` (khôi phục hành vi cũ), `auto,+<lang>` force-include, lỗi "Unsupported parser(s)" phải liệt kê tên hợp lệ, detection/skip in trước run + trong dry-run (detect-only mode ở child), post-check unmatched files, `--strict-detection` cho CI. *Rationale:* detection sai một lần mà user không nhìn thấy = mất niềm tin vào graph.
6. **Nếu vẫn muốn module "detect" tường minh** (cho banner/UX naming): pure composition `detect_parsers(root) = group_paths_by_parser(walk_all_source_files(root)) ∪ framework_evidence`, sibling của `tsdetect.rs`, chỉ active khi `parser_auto_mode`; substring-only descriptor reads với caps (fix luôn `read_limited_lower(usize::MAX)` ở frameworks.rs); không cache disk nếu không cần.
7. **Đồng bộ lệch nhánh:** branch `feat/change-db` đang WIP ở `cortex-graph-writer/topology.rs` + `message_scan/graph.rs` (Ladybug dialect) — retirement (mục 3) đụng cùng vùng cleanup/topology; lên kế hoạch rebase trước khi implement.

## Next Steps (theo CAUTION → address mitigations rồi mới hi-plan)

1. Chốt scope reframed (mục 1–6) — đặt tên feature riêng, ví dụ `presence-gating + parser retirement`, **đừng** đặt tên "approximate detection" trong plan để tránh kéo theo thiết kế heuristic.
2. Golden fixtures cho regression test (bám pattern probe/golden capture đã dùng ở phase-01 sync): (a) Android tree + stray `.cpy` → assert không spawn cobol, không node COBOL, không collection; (b) legacy COBOL tree thật → assert cobol chạy đủ; (c) monorepo đa ngôn ngữ → assert full set; (d) JP1-only `.txt` → assert jp1 chạy (sniff không bị detection thô giết); (e) sync 2 lần với parser set thu hẹp → assert retirement chạy đúng, không flicker-deletion.
3. Sau khi mitigations được thiết kế vào plan → chạy `hi-plan` cho implementation.

---

## Method Note

- 5 persona chạy độc lập (subagent tách biệt, không đọc chéo), mỗi persona tự verify code trước khi kết luận.
- 2 claim load-bearing của persona được tôi verify lại trực tiếp trên code trước khi tổng hợp: gate `:1487` unconditional (đúng) và message lane bypass `parser_filter` (đúng một phần — có gate theo routed-files nhưng không theo filter).
- mind_mcp không có project này trong registry (fallback theo skill: ghi nhận confidence thấp hơn cho findings chỉ dựa MCP); graph_mcp xác nhận chéo `_selected_parsers`/`_select_parser_for_path` từ graph của chính harness (tuy nhiên graph phản ánh bản Python cũ `code-tiny/tools/sync/` — semantics đã port sang Rust, dùng để cross-check chứ không phải source of truth).
