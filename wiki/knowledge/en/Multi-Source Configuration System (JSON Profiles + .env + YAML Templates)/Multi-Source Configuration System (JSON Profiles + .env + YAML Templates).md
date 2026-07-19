---
kind: configuration_system
name: Multi-Source Configuration System (JSON Profiles + .env + YAML Templates)
category: configuration_system
scope:
    - '**'
source_files:
    - cortex_harness/dev.py
    - code-tiny/tools/common/harness_config.py
    - .cortext-harness/config/dev.json
    - code-tiny/.env.example
    - doc-tiny/.env-sample
    - harness/templates/config.yaml
    - installers/common/config_manager.py
---

Cortex Harness uses a layered configuration system that combines per-project JSON profiles, environment variables via `.env` files, and YAML harness templates. The system is orchestrated by the top-level `dev` CLI (`cortex_harness/dev.py`) which loads, merges, and propagates configuration to code-tiny and doc-tiny sub-services.

## Configuration Sources and Loading Order

1. **Per-project JSON profiles** — Located under `<project>/.cortext-harness/config/<env>.json`. Each file represents an environment (e.g., `dev.json`, `prod.json`) with an `active: true` flag selecting the current one. The dev CLI scans this directory, picks the active config (or falls back to the first), and reads it as the authoritative source for project settings, graph provider selection, embedding models, and scan roots.

2. **Environment variables** — Loaded via `python-dotenv` from `.env` files in each service root (`code-tiny/.env`, `doc-tiny/.env`). These are used directly by MCP servers and analyzers. The `harness_config.py` helper also populates `os.environ` from the JSON profile's `code.env` block when running analyzers, with existing env vars taking precedence.

3. **Harness YAML template** — `harness/templates/config.yaml` defines agent session parameters (budgets, MCP tool URLs, policy allowlists) consumed by the harness runner scripts.

4. **Installer context-menu config** — `installers/common/config_manager.py` manages `context-menu.json` under `.cortext-harness/` for platform-specific shell integrations (Windows registry, macOS Services, Ubuntu Nautilus).

## Key Files and Packages

- `cortex_harness/dev.py` — Central orchestrator; implements `_load_active_config`, `_config_dir`, `_save_config`, `_graph_provider`, `_code_env_for_process`, `_doc_env_for_process`, and all CLI commands that read/write `.cortext-harness/config/*.json`.
- `code-tiny/tools/common/harness_config.py` — `load_harness_config()` bridges JSON profile env into process `os.environ` for analyzer subprocesses.
- `code-tiny/.env.example` / `doc-tiny/.env-sample` — Template `.env` files documenting required variables (`NEO4J_*`, `QDRANT_*`, `EMBEDDING_MODEL`, etc.).
- `harness/templates/config.yaml` — Agent harness runtime config (mode, budgets, verify commands, policy tool allowlists).
- `installers/common/config_manager.py` — `ContextMenuConfig` class managing cross-platform context menu installation paths and command definitions.
- `.cortext-harness/config/dev.json` — Example active project profile showing `project`, `code.env`, `doc.env`, and `source.projects` layout.

## Architecture and Conventions

- **Profile-per-environment**: One JSON file per environment under `.cortext-harness/config/`; only one may be `active: true` at a time. The dev CLI automatically deactivates others when switching.
- **Graph provider abstraction**: Both `GRAPH_PROVIDER` and scoped variants (`CODE_GRAPH_PROVIDER`, `DOC_GRAPH_PROVIDER`) select between `falkordb` and `neo4j`. When FalkorDB is chosen, `FALKORDB_*` flags are passed alongside Neo4j fallback args.
- **Env var precedence**: Process `os.environ` always wins over JSON profile values. The `harness_config.py` loader only sets keys not already present in the environment.
- **Qdrant URL construction**: If `QDRANT_URL` is absent but `QDRANT_HOST`/`QDRANT_PORT` are set, the dev CLI synthesizes `http://{host}:{port}`. Collection names fall back to `project.code` or the literal string `"project"`.
- **State persistence**: Incremental sync state lives under `.cortext-harness/sync-state/<hash>.json` keyed by folder path, storing git commit, last sync timestamp, and per-file SHA-256 hashes.
- **Sensitive file filtering**: A hard-coded `SENSITIVE_PATTERNS` list (`.env`, `*.key`, `*secret*`, etc.) plus `_SCAN_EXCLUDE` directories (`.venv`, `node_modules`, build artifacts) prevent secrets and caches from being scanned or committed.
- **Agent harness config**: `harness/templates/config.yaml` separates harness runtime (`mode`, `scope_lock`), MCP server endpoints (`graph_mcp_url`, `mind_mcp_url`), budget caps (`max_rounds`, `max_tool_calls`, `max_tokens`, `max_duration_seconds`), verification commands, and per-feature-type tool allowlists.

## Rules Developers Should Follow

- Store per-environment overrides in `.cortext-harness/config/<env>.json` and mark exactly one as `active: true`.
- Keep secrets out of version control; use `.env` files (copied from `.env.example` / `.env-sample`) and rely on `python-dotenv` loading.
- Prefer setting `CODE_GRAPH_PROVIDER` / `DOC_GRAPH_PROVIDER` rather than the generic `GRAPH_PROVIDER` to avoid ambiguity between code and document pipelines.
- When adding new environment variables expected by analyzers, update both the JSON profile schema and `harness_config.py` so they propagate into subprocess environments.
- Do not commit files matching `SENSITIVE_PATTERNS` or inside `_SCAN_EXCLUDE` directories; the dev CLI will skip them during scanning and auto-generate `.gitignore` entries if needed.
- For agent sessions, edit `harness/templates/config.yaml` to adjust budgets, MCP tool URLs, and policy allowlists rather than hard-coding values in scripts.