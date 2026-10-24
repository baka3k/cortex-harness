<!-- Synchronized with AGENTS.md — Mandatory rules for Claude Code and all AI agents -->

# Cortex Harness — Mandatory Agent & Harness Rules

## 1. Zero Real-Project Leakage (STRICT SECURITY RULE — ALL AGENTS & HARNESSES)

Every AI Agent (Claude Code, Antigravity/Gemini, Codex, Cursor, Copilot, Qwen, subagents) and every Harness workflow operating in this repository MUST obey the following data-sanitization rules without exception:

1. **NEVER record real project keywords or identifiers anywhere in the repository**:
   - Do **NOT** copy real project names, client/partner/vendor names, usernames, hostnames, or internal paths into tracked files.
   - Do **NOT** copy real domain identifiers sampled from live Qdrant/FalkorDB/LadybugDB stores into unit test fixtures, code comments, docstrings, `docs/plans/`, `docs/logs/`, contracts, or commit messages. This includes:
     - Real module/folder names, screen IDs, form names, or source filenames
     - Real symbol, function, variable, database column, or UI control names
     - Real specification document filenames, spreadsheet titles, or prose snippets (in any language, including Japanese/Vietnamese/English)
2. **Always use synthetic, neutral placeholders**:
   - When writing tests, examples, architecture plans, or incident logs based on a live debugging session, **translate every concrete artifact into a generic synthetic equivalent** before writing to disk:
     - Project IDs: `"fixt"`, `"sample"`, `"project-x"`, `"erp"`
     - Code paths & modules: `"src/orders/OrderSummaryForm.vb"`, `"src/auth/login.py"`, `"src/shared/_shared_widget.vb"`, `"src/MOD_A"`
     - Symbols & controls: `"OrderSummaryForm_Load"`, `"grid_unit_col"`, `"login_handler"`, `"DEPT_CODE"`, `"on_submit_click"`
     - Documents: `"specs/order_form_spec.xlsx"`, `"docs/auth.md"`, `"specs__module_a__batch_job_01.xlsx"`
3. **Never bypass `.sensitive-terms` or the `pre-commit` hook**:
   - `.sensitive-terms` is local-only (gitignored) and enforced by `.git/hooks/pre-commit` (`sensitive-guard`).
   - **NEVER** use `git commit --no-verify` or `# sensitive-guard:allow` to bypass a blocked term.
   - When discovering a new live project identifier pattern during debugging, add its wildcard pattern to local `.sensitive-terms` so it can never be committed accidentally.
   - Full policy and history-purge runbook: [`docs/SENSITIVE_TERMS_POLICY.md`](docs/SENSITIVE_TERMS_POLICY.md).

## 2. Repository Engineering Rules

- **Language**: All code, comments, tests, plans, logs, and commit messages in the repository must be in English. Chat responses follow the user's language.
- **Git Discipline**: Commit only after verification, never use `--no-verify`, and do not push unless explicitly requested by the user.
- **Subsystem Boundaries**:
  - `code-tiny/`, `doc-tiny/`, and `link-tiny/` are sibling pipelines coordinated by `cortex_harness/`.
  - Respect each tree's local contract (`docs/LINK_CONTRACT.md`, `docs/UNIFIED_INGEST_QUERY_CONTRACT.md`, `docs/PROJECT_ID_QUERY_RULES.md`).
