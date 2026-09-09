
---

## English Translation

I want to finalize the CLI suite so that users only need to interact with the terminal. Let's start by completing the **`cli init`** command.

### 1. Configuration File Generation

Executing `dev init` will generate JSON configuration files located within the target project directory.

* **Path:** `.cortext-harness/config/dev.json` or `.cortext-harness/config/prod.json`.
* **Environment Support:** The system supports multiple configuration files for different environments.
* **Active State:** The system must identify which configuration is currently in use via an `active` field within the config file.

### 2. Project Structure Generation

The CLI will scaffold the following directory structure:

```text
.
├── docs/                        # Project Documentation
│   ├── design-docs/             # System Architecture & Design
│   │   ├── index.md
│   │   ├── core-beliefs.md
│   │   └── ...
│   ├── exec-plans/              # Execution & Tracking
│   │   ├── active/              # Ongoing sprints/tasks
│   │   ├── completed/           # Archive of finished plans
│   │   └── tech-debt-tracker.md # Legacy issues & modernization gaps
│   ├── generated/               # Auto-generated Assets
│   │   └── db-schema.md         # Schema diagrams (Neo4j/SQL)
│   ├── product-specs/           # Functional Requirements
│   │   ├── index.md
│   │   ├── new-user-onboarding.md
│   │   └── ...
│   ├── references/              # LLM Context & Standards
│   │   ├── design-system-reference-llms.txt
│   │   ├── nixpacks-llms.txt
│   │   ├── uv-llms.txt
│   │   └── ...
│   ├── DESIGN.md                # High-level Design Overview
│   ├── FRONTEND.md              # Frontend Guidelines
│   ├── PLANS.md                 # Project Roadmap
│   ├── PRODUCT_SENSE.md         # Product Logic & Philosophy
│   ├── QUALITY_SCORE.md         # Engineering Standards
│   ├── RELIABILITY.md           # Stability & Error Handling
│   └── SECURITY.md              # Security Protocols
│
├── src/                         # Source Code (Parallel to docs)
│   ├── core/                    # Business Logic
│   │   ├── migration/           # Legacy-to-Modernization engine
│   │   └── services/            # Main application services
│   ├── infra/                   # Infrastructure & Data
│   │   ├── persistence/         # Database drivers (Neo4j, Qdrant..etc)
│   │   └── providers/           # External AI/LLM API wrappers
│   ├── interface/               # Entry Points
│   │   ├── api/                 # REST/gRPC endpoints
│   │   └── cli/                 # Terminal tools (e.g., "Knows" project)
│   └── shared/                  # Common Utilities & Types
├── AGENTS.md
├── ARCHITECTURE.md
├── .cursorrules                 # AI Instruction Set
└── README.md                    # Project Onboarding

```

### 3. Folder-to-Config Mapping

The generated project structure must be reflected in the configuration file. For example:

**Documentation Mapping:**

```json
 "source": {
      "git": "",
      "folder": [
        "docs"
      ]
    }

```

**Source Code Mapping:**

```json
"source": {
      "git": "",
      "folder": [
        "src"
      ]
    }

```

### 4. Ignore Folders (scan-time excludes)

`dev init` asks for a comma-separated list of folders to skip while scanning,
stored as a top-level config section that applies to **both** code sync and
doc sync:

```json
{
  "ignore": {
    "folders": ["legacy", "generated-*", "sandbox", "third_party"]
  }
}
```

Semantics:

* Each entry is a **folder name** (exact, e.g. `legacy`) or an **fnmatch glob**
  (`generated-*`, `*.tmp-out`), matched against directory names at **any
  depth** below the configured scan roots. Relative paths (`a/b`) are not
  supported in v1.
* User entries **add to** the built-in default excludes
  (`_SCAN_EXCLUDE` / `COMMON_SCAN_EXCLUDE`); defaults can never be un-ignored.
* Excluded folders are hidden from the `dev sync code` / `dev sync doc` folder
  pickers, omitted from snapshot hashes, incremental change detection and
  doc ingestion. A scan root selected explicitly while also matching the
  ignore list still runs, with a warning.
* Manage the list without re-running `dev init`:

  ```bash
  dev ignore add legacy generated-*   # merge into ignore.folders
  dev ignore remove legacy            # exact-match removal
  dev ignore list                     # print configured entries
  ```

  All three act on the active environment config and fail with
  "Run 'dev init' first" when no config exists. Changes apply from the next
  sync run.

* `CORTEX_EXTRA_IGNORE_DIRS` (comma-separated) is the **internal channel**
  the orchestrator uses to propagate `ignore.folders` into spawned
  subprocesses (incremental_sync, language analyzers, doc-tiny ingest).
  Configure ignores via `dev init` / `dev ignore` — do not set the env var by
  hand.