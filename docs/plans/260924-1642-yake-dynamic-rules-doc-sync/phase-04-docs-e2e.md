# Phase 04 — Docs + End-to-End verification (A/B baseline)

> plan-id: 260924-1642-yake-dynamic-rules-doc-sync · phase: 04 · red-team rev: M5/m8

## Mục tiêu

Tài liệu hóa bề mặt mới + chứng minh user story đầu-cuối trên fixture thật **có A/B baseline khách quan** (m8) + chốt verification-report.

## Thay đổi docs

### 1. `doc-tiny/Readme.md`

- Mục **Entity extraction**: `--yake-rules/--no-yake-rules`, `--yake-language/--yake-top/--yake-max-ngram/--yake-rules-dir`; `--ruler-json` giờ repeatable và cũng ảnh hưởng provider gliner (union). Ghi rõ: pre-pass sinh `rules/from-yake/<project>/ruler.from-yake.<source>.json` từ text đã load, trước extraction; gliner merge ruler (blank sidecar, không cần model NER) với predictions; degradation khi thiếu `yake`.
- Compact table: dòng flags mới + defaults theo env.
- Mục ngắn **Dynamic rules (YAKE)**: vòng đời (sinh khi sync — skip nếu 0 patterns, prune folder-mode + khi doc bị xóa, gitignored), bảng env `YAKE_*`, ví dụ CLI `python yake_rules.py --file X.pdf -l en --top 150 --format ruler -o out.json`.

### 2. `ReadMe.md` (root) + `docs/HARNESS_WORKFLOW.md`

- Root: 1 dòng capability doc sync (vùng capabilities — "sinh entity-rule động theo tài liệu bằng YAKE trước khi GLiNER extraction").
- HARNESS_WORKFLOW.md: nếu có đoạn mô tả sync doc → 1 dòng yake pre-pass; không có chỗ phù hợp thì bỏ.

## E2E verification (thủ công — form click ĐÚNG: group options trước subcommand, M5)

Chuẩn bị: temp project + `.cortext-harness/config/*.json` có doc source folder chứa **1 file**: copy `CCC-TS-101-Digital-Key-R3-1.2.1-APPROVED.pdf` từ `doc-tiny/testdata/`. Storage local mặc định. GLINER theo env máy (`GLINER_MODEL_PATH`/`GLINER_MODEL_NAME`); **không dùng fallback provider spacy** (merge yake không wired cho spacy — M4 note); nếu model không khả dụng → ghi giới hạn, chứng minh merge bằng unit test + dry-run.

Steps:

1. Baseline A — `dev sync doc --project-dir <tmp> --no-yake all` (hoặc interactive không chọn `all`: `dev sync doc --no-yake --project-dir <tmp>` rồi chọn folder):
   - [ ] Echo `yake : off`; KHÔNG có file trong `doc-tiny/rules/from-yake/<project>/`.
   - [ ] Ghi lại số node + tập entity names/labels của graph (query qua `dev mcp` hoặc script store trực tiếp).
2. Run B (feature) — xóa graph/collection hoặc đổi project_id rồi `dev sync doc --project-dir <tmp> all`:
   - [ ] Echo `yake : on (lang=en, top=150, dir=...)`.
   - [ ] `ruler.from-yake.<safe-source>.json` tồn tại, patterns ≈ 100–150.
   - [ ] Log ingestor: dòng `yake rules: ... patterns` in TRƯỚC extraction của chính file đó.
   - [ ] Graph có entity match rule động với label `DOCUMENT`/`TRANSPORT` (ví dụ "Key Technical Specification") **mà tập baseline A không có** — đây là bằng chứng A/B (m8).
3. `dev sync doc --project-dir <tmp> --no-yake --dry-run all`:
   - [ ] Cmd chứa `--no-yake-rules`; không rule file mới.
4. Xóa PDF khỏi folder → `dev sync doc --project-dir <tmp>` (incremental deletion-only):
   - [ ] Sync chạy; log/verification cho thấy `yake_rules.py --prune-sources ...` được gọi; rule file biến mất (D8).
5. `git status` → toàn bộ `doc-tiny/rules/from-yake/` bị ignore.

## Verification report

`docs/plans/260924-1642-yake-dynamic-rules-doc-sync/verification-report.md`: kết quả từng step (pass/fail + log excerpt), A/B diff entity evidence, kết quả `pytest doc-tiny/tests/ tests/` full, danh sách file đổi, xác nhận default-on qua E2E.

## Definition of Done

- Cả 4 phase DoD ✅; report hoàn chỉnh; plan `status: complete`.
- Không artifact sinh động nào bị track trong git.
