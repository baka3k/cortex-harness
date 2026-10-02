# Research Digest — vendor API + adapter (session 2026-10-02)

Nguồn: researcher pass (Explore agent) trên repo, phục vụ plan
`261002-1410-vb6-worker-progress-sll`. Tất cả path relative repo root.

## 1. Vendor API (proleap-vb6-parser, pinned 53e7b5c, MIT)

- `VbParserRunner` (interface, public): `analyzeCode`, `analyzeFile(File[,
  params])`, `analyzeFiles(List<File>[, params])` — không có entry per-file
  accumulate vào `Program` có sẵn.
- `VbParserRunnerImpl` (public class):
  - `protected void parseFile(File, Program, VbParserParams)` (:248-267) —
    điểm accumulate duy nhất; `analyzeFiles` (:143-155) = new ProgramImpl →
    registerModelElements → for-each `parseFile` → `analyze(program)`.
  - `protected void parseCode(String vbCode, String moduleName, boolean
    isClazz, boolean isStandard, Program, params)` (:198-239) — chứa lexer
    (:201), CommonTokenStream (:210), parser (:213), `startRule()` (:222),
    moduleName tail (:224-232), visitor (:234-238).
  - `protected` → **subclass ở package khác (io.cortex.vb6.worker) override
    được**; `Vb6Worker` là `final` class đứng ngoài không gọi trực tiếp được.
  - PredictionMode: không chỗ nào set → default LL (ALL(*)).
- ANTLR 4.7.2 (vendor pom :24-37); shaded jar có sẵn
  `atn/PredictionMode`, `BailErrorStrategy`, `misc/ParseCancellationException`.
- `ignoreSyntaxErrors=true` (worker set) → KHÔNG remove listeners →
  **ConsoleErrorListener in lỗi cú pháp ra stderr** hôm nay đã có sẵn (R1).
- slf4j NOP logger trong shaded jar (không binding) → vendor LOG.info no-op.

## 2. VbParserParams

Chỉ 2 field: `charset` (default UTF-8), `ignoreSyntaxErrors` (default false)
+ getter/setter. Không có prediction-mode param → two-stage phải qua subclass.

## 3. Vb6Worker (worker)

- `System.err` chỉ dùng cho usage (:151); stdout = 1 JSON (:198).
- Batch call `analyzeFiles` :288; retry :301; params set :282-283;
  runner `new VbParserRunnerImpl()` :285.
- `worker_meta` :385-398 — thêm field an toàn (contract test không assert
  exact shape).
- `parse_meta.worker_elapsed_ms` hardcoded 0 :649 — hook điền timing thật.

## 4. Adapter (vb6_antlr_adapter.py)

- `subprocess.run(..., stderr=PIPE, timeout=effective_timeout_sec)` :292-299;
  stderr chỉ hiện khi rc≠0 (:301-307); rc==0 discard.
- verbose: analyzer `--verbose` (vb_analyzer_base :2417) →
  `parse_vb6_files_with_antlr(verbose=...)` (:579-586).
- stdout contract = 1 JSON (`json.loads(proc.stdout)`) — Popen relay stderr
  không đổi protocol; cần drain thread cho stdout (tránh deadlock).

## 5. Tests

- `tests/test_vb6_antlr_worker_contract.py` import adapter, không đọc
  stderr; meta spot-check `implements_map` thôi → thêm stderr/meta an toàn.
- Cover gián tiếp: `test_vb6_anchor_graph.py` (qua
  `_parse_vb6_with_antlr_batch`), `benchmark_vb6_parse_quality.py`,
  `test_vb6_engine_dispatch.py` (assert stdout strings — cẩn thận khi thêm
  print mới vào analyzer stdout).

## 6. Plan conventions

- Dir `docs/plans/YYMMDD-HHMM-<slug>/`: `plan.md` (YAML frontmatter: title,
  status, created, mode, scope, blockedBy, blocks, relatedPlans, sources),
  body: Overview / Coverage matrix / Scope Challenge / Architecture
  Decisions / Milestones / Phases / Risks / Resync.
- `phase-NN-*.md`: `# Phase NN — name`, `> Gate ra: M*`, `## Tasks` theo
  subsection, kết `## Định nghĩa xong` checklist.
- Kèm `red-team.md`, `research-digest.md`, `verification-report.md`.
