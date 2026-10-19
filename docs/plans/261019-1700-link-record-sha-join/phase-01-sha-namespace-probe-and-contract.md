# Phase 01 — sha-namespace probe + contract + failing fixtures (TDD red)

## Goal

Pin down, with live evidence and committed fixtures, exactly how each
side's graph identity maps onto its baseline keys — then write the join
contract into `docs/LINK_CONTRACT.md` and encode it as failing tests, so
Phase 02 implements against a red suite instead of assumptions.

## Tasks

1. **Probe script** `link-tiny/tests/probe_sha_namespaces.py`
   (runnable standalone, never imported by the suite):
   - doc side: sample `Paragraph.source_id` values from a live
     `{project}_doc` graph and the `file_hashes` keys from the project's
     sync-state; print the flatten mapping hit-rate for the
     `__`-join rule;
   - code side: sample symbol/File nodes per parser label and record the
     `repo` property values observed (project root vs scan folder),
     cross-referenced against the `code.env.source.projects[].folder`
     list from the project config;
   - emit a short markdown table to stdout (paste into the contract).
2. **Fixtures** (pure-python, no live FalkorDB — follow the existing
   fake-graph-handle pattern in `link-tiny/tests/test_link_engine.py`):
   - doc: `ParagraphRef("specs__module_a(v1)__batch_job_01_data_flow.xlsx", 11)`
     + baseline key `specs/module_a(v1)/batch_job_01_data_flow.xlsx`;
   - doc single-file mode: source_id = stem, baseline = rel path;
   - code: symbol `file_path="src/COMMON/x/ADODB.cs"` with
     `repo="/proj/<code-folder>/s1"` + inventory key `src/COMMON/x/ADODB.cs`
     under folder `/proj/<code-folder>/s1`; a second folder `/proj/<code-folder>/s3`
     containing a suffix-shadowing path (must NOT match);
   - adversarial: doc filename containing a literal `__`; symbol with
     `repo=None`; two folders both containing `src/dup.py` (ambiguity).
3. **Contract** — new "SHA join" section in `docs/LINK_CONTRACT.md`:
   the flatten rule (mirror of dev.py `source_id` construction, both
   separators), the `(repo, path)` primary join, the unique-fallback and
   the ambiguity→`null` rule, and the telemetry fields. Note explicitly
   that suffix matching is forbidden (live evidence: 152 shadows / 1
   collision on the live project s1+s3).
4. **Red tests** — write the Phase 02 assertions now
   (`test_sha_join.py`): they exercise `_doc_sha_for` /
   `load_code_inventory_hashes` / `build_record` through the contract
   shapes and fail against today's engine.

## Files

- New: `link-tiny/tests/probe_sha_namespaces.py`,
  `link-tiny/tests/test_sha_join.py` (red)
- Touched: `docs/LINK_CONTRACT.md` (new section only)
- Not touched: engine code (Phase 02)

## Test scope

Mapped to acceptance "spot-check records byte-for-byte" + "ambiguity
never produces a wrong SHA". Expected size **S**: probe (manual run,
output committed to the contract PR description), ~8–10 fixture tests,
1 doc section.
