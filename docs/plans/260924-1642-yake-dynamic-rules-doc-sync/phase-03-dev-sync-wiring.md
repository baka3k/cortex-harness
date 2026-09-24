# Phase 03 — Wiring `dev sync doc` + prune + artifact hygiene

> plan-id: 260924-1642-yake-dynamic-rules-doc-sync · phase: 03 · red-team rev: C3/M1/M5/m5/m6

## Mục tiêu

`dev sync doc` / `dev sync doc all` truyền cấu hình yake + rules-dir per-project xuống ingestor; xóa rule khi doc bị xóa (kể cả deletion-only run); rule artifacts không bị commit. Không đụng region sync-code của dev.py (plan 260821-2115).

## Thay đổi

### 1. `cortex_harness/dev.py` — option CLI trên nhóm `sync doc`

- `sync_doc` group (L3616): thêm `@click.option("--yake/--no-yake", default=None, help="Override YAKE dynamic rules (default: env YAKE_ENABLED).")` → `ctx.obj["yake"]`. Lưu ý click: option group phải đặt **trước** subcommand (`dev sync doc --no-yake all` — M5).
- `sync_doc_all` (L3686): đọc `o.get("yake")`.
- Truyền xuống `_sync_doc_folder(..., yake: bool | None)`.

### 2. `_sync_doc_folder` (L1175-1330) — base_cmd flags

- Resolve `yake_on` (CLI override > env `YAKE_ENABLED`, default True; parse "0/false/no/off" → False).
- Rules dir per-project (C3): `yake_rules_dir = DOC_TINY / "rules" / "from-yake" / _safe(project_id)` (project_id đã có trong hàm, L1198; sanitize giống source naming).
- Append vào `base_cmd` (L1205-1221):

```python
*(["--yake-rules"] if yake_on else ["--no-yake-rules"]),
"--yake-language", env.get("YAKE_LANGUAGE", "en"),
"--yake-top",      env.get("YAKE_TOP", "150"),
"--yake-rules-dir", str(yake_rules_dir),
```

(`YAKE_MAX_NGRAM` chỉ append khi env có.)

- Echo cạnh `provider:` (L1225): ` yake   : on (lang=en, top=150, dir=rules/from-yake/<project>)` hoặc `off`.

### 3. Prune khi xóa doc — D8 (sửa M1)

Sau incremental sync thành công có `deleted_rel` (L1303-1323, ngay chỗ pop hashes): gọi nhẹ

```python
if yake_on and deleted_rel and not dry_run:
    run [python, str(DOC_TINY / "yake_rules.py"), "--rules-dir", str(yake_rules_dir),
         "--prune-sources", *[safe_source_id(rel) for rel in deleted_rel]]
```

Deletion-only run (0 changed files) vẫn đi tới đoạn này vì hàm trả về sau — verify placement. `--prune-sources` là CLI có từ Phase 01. Fail → warn, không fail sync.

### 4. `.gitignore` (repo root)

```
doc-tiny/rules/from-yake/
```

(C3 layout mới; prototype artifact top-level đã bị xóa ở Phase 01 — m6 không còn dính dáng.)

## Tests — `tests/test_dev_sync_doc_yake.py` (mới)

Convention `test_dev_sync_reliability.py`: CliRunner + temp project config + `--dry-run` capture cmd + `patch("cortex_harness.dev._venv_python", "/fake/python")` (research F9). Cmd assertions kiểu subset `assertIn` (m5 — các test cũ sẽ không break khi thêm flag mới; bỏ hedge).

1. `test_sync_doc_default_enables_yake` — cmd chứa `--yake-rules --yake-language en --yake-top 150 --yake-rules-dir <...>/rules/from-yake/<project>`.
2. `test_sync_doc_env_disables_yake` — doc.env `YAKE_ENABLED: "0"` → `--no-yake-rules`.
3. `test_sync_doc_cli_override` — `--no-yake` (env bật) → `--no-yake-rules`; `--yake` (env tắt) → `--yake-rules`. Đặt option trước subcommand `all` (M5 form).
4. `test_sync_doc_env_params_forwarded` — `YAKE_LANGUAGE: "vi"`, `YAKE_TOP: "80"` → flags đúng.
5. `test_sync_doc_all_same_flags` — subcommand `all` nhận override từ ctx.obj.
6. `test_deleted_docs_prune_rule_files` — state có baseline, xóa 1 file, sync incremental (fake _run_with_retry OK) → có lời gọi `yake_rules.py --prune-sources <id>` với rules dir per-project; `--dry-run` → không gọi.

Regression: `pytest tests/test_dev_sync_reliability.py tests/test_unified_contract_doc_paths.py` pass nguyên vẹn.

## Definition of Done

- `pytest tests/test_dev_sync_doc_yake.py tests/test_dev_sync_reliability.py` pass.
- `dev sync doc --dry-run` thật: echo `yake : on (...)` + cmd hiển thị flags đúng form click.
- `git status` sạch với artifacts sinh động (bị ignore đúng pattern mới).
