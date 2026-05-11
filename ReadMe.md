# CortexHarness

CortexHarness is a cognition-aware context orchestration framework for AI systems.

It combines Graph Database relationships, Vector Database semantic retrieval, and structured harness engineering to build reliable, scalable, and context-consistent AI applications.

Instead of treating prompts as isolated inputs, CortexHarness focuses on constructing a persistent contextual cognition layer for models — enabling better memory synthesis, contextual reasoning, execution stability, and orchestration control.

## Core Capabilities

* Graph + Vector hybrid context retrieval
* Structured system context generation
* Harness engineering support for stable execution flows
* Context contracts and orchestration pipelines
* Semantic memory layering
* Multi-source context synthesis
* AI-agent and Copilot-ready architecture
* Extensible runtime integration

## Philosophy

Modern AI systems should not rely on prompts alone.

CortexHarness treats context as infrastructure:

* memory is structured,
* cognition is composable,
* execution is orchestrated.

The goal is to provide a foundational layer for building reliable AI-native systems at scale.

## Use Cases

* AI Copilot systems
* Multi-agent architectures
* Enterprise AI orchestration
* Long-context memory systems
* Knowledge graph enhanced AI
* Retrieval-augmented generation (RAG)
* Harness engineering platforms
* Cognitive runtime infrastructure

The Dev CLI lives at `cli/dev.py` with launchers `dev.bat` (Windows) and `dev.sh` (Unix/macOS).

---

## 1. Commands

### Setup

| Command | Description |
| --- | --- |
| `dev init` | Interactive wizard — create/update config and scaffold project folders |
| `dev init --env prod` | Configure the `prod` environment (default: `dev`) |
| `dev init --project-dir /path/to/project` | Target a specific project directory |
| `dev status` | Show the active config (Neo4j, Qdrant, folders, environments) |

### Sync — Source Code

| Command | Description |
| --- | --- |
| `dev sync code` | Interactive folder picker; auto-detects languages; incremental if baseline exists |
| `dev sync code all` | Run **all** available analyzers on every configured folder |
| `dev sync code --preview` | Preview changed files before syncing |
| `dev sync code --dry-run` | Print commands without executing them |
| `dev sync code --no-verbose` | Suppress verbose output |
| `dev sync code --project-dir /path` | Target a specific project directory |

> **First run:** always a full sync (no baseline).
> **Subsequent runs:** incremental — changed/deleted files are passed to each analyzer via `--changed-files-manifest` (git diff → mtime fallback).

### Sync — Documentation

| Command | Description |
| --- | --- |
| `dev sync doc` | Interactive folder picker; incremental if baseline exists |
| `dev sync doc all` | Full sync for every configured doc folder |
| `dev sync doc --preview` | List queued files and confirm before syncing |
| `dev sync doc --entity-provider langextract` | Override entity provider (`gliner` / `langextract` / `spacy`) |
| `dev sync doc --dry-run` | Print commands without executing them |
| `dev sync doc --project-dir /path` | Target a specific project directory |

> **First run:** always a full sync (no baseline).
> **Subsequent runs:** incremental — detects changes via git diff → SHA-256 hash comparison → mtime.
> **Supported formats:** `.pdf`, `.md`, `.docx`, `.txt`, `.pptx`, `.xlsx`

### MCP Servers

| Command | Description |
| --- | --- |
| `dev mcp start` | Start code-tiny (port 8788) and doc-tiny (port 8789) in background; shows status if already running |
| `dev mcp start --force-restart` | Kill existing processes then restart |
| `dev mcp add` | Write `.mcp.json` in project root (workspace scope, default) |
| `dev mcp add --scope global` | Patch system-wide agent config files |
| `dev mcp add --agent claude` | Target a specific agent: `claude` / `claude-code` / `vscode` / `cursor` / `all` |

> Config files are backed up before modification (`*.bak.<timestamp>.json`).
> Supported agents and their config paths:
> - **Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json`
> - **Claude Code** — `~/.claude/settings.json`
> - **VS Code** — `~/Library/Application Support/Code/User/mcp.json`
> - **Cursor** — `~/.cursor/mcp.json`
> - **Workspace** — `.mcp.json` in project root

---

## 2. Usage from a Project Directory

Run the CLI from any project folder by referencing the launcher with an absolute path:

**macOS / Linux:**

```bash
# From your project root:
/path/to/cortex-harness/dev.sh init                    # Create config + scaffold folders
/path/to/cortex-harness/dev.sh status                  # Check active config
/path/to/cortex-harness/dev.sh sync code               # Interactive code sync
/path/to/cortex-harness/dev.sh sync code all           # Full code sync (all analyzers)
/path/to/cortex-harness/dev.sh sync doc                # Interactive doc sync
/path/to/cortex-harness/dev.sh sync doc all            # Full doc sync
/path/to/cortex-harness/dev.sh mcp start               # Start MCP servers in background
/path/to/cortex-harness/dev.sh mcp add                 # Register MCP endpoints in .mcp.json
```

**Windows:**

```bash
C:\ai\cortex-harness\dev.bat init
C:\ai\cortex-harness\dev.bat sync code
C:\ai\cortex-harness\dev.bat sync doc all
C:\ai\cortex-harness\dev.bat mcp start
C:\ai\cortex-harness\dev.bat mcp add --scope global
```

---

## 3. Configuration

Each project stores its own config under `.cortext-harness/config/<env>.json` (e.g. `dev.json`, `prod.json`).

Generated by `dev init`, the config holds separate sections for code and doc pipelines:

```json
{
  "active": true,
  "project": { "code": "my-project", "name": "My Project" },
  "code": {
    "env": {
      "NEO4J_URI": "bolt://localhost:7687",
      "NEO4J_DB": "neo4j",
      "NEO4J_USER": "neo4j",
      "NEO4J_PASS": "...",
      "QDRANT_HOST": "localhost",
      "QDRANT_PORT": "6333",
      "EMBEDDING_MODEL": "jinaai/jina-embeddings-v3",
      "BATCH_SIZE": "1",
      "MAX_EMBED_CHARS": "500",
      "device": "cpu"
    },
    "source": { "git": "", "folder": ["src"] }
  },
  "doc": {
    "env": {
      "NEO4J_URI": "bolt://localhost:7687",
      "EMBEDDING_MODEL": "BAAI/bge-m3",
      "...": "..."
    },
    "source": { "git": "", "folder": ["docs", "docs/design-docs", "..."] }
  }
}
```

Multiple environments are supported. Only one can be `"active": true` at a time — switching is handled automatically by `dev init`.

---

## 4. Sync State & Incremental Logic

Sync state is stored per-folder in `.cortext-harness/sync-state/`. Each entry records the last git commit, timestamp, and file hashes used as baseline for the next incremental run.

**Code sync** delegates change detection to each analyzer's built-in `--changed-files-manifest` mechanism.

**Doc sync** detects changes in this priority order:
1. `git diff` since last synced commit
2. SHA-256 hash comparison against stored hashes
3. `mtime` comparison against last sync timestamp

Sensitive files (`.env`, `*.pem`, `*.key`, `*secret*`, etc.) are always excluded before transmission.

---

## 5. Environment Management

The CLI automatically locates the Python interpreter inside `.venv/` for both `code-tiny` and `doc-tiny`, falling back to the system Python if no virtual environment is found.