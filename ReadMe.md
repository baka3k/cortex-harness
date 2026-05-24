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

## Installation

Clone the repo once, install the `dev` command globally — no aliases, no path prefixes needed.

```bash
git clone <repo-url>
cd cortex-harness

# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\activate             # Windows

# Install dependencies and register the dev command
pip install -r requirements.txt
pip install -e .
```

After this, `dev` is available in your PATH on any platform:

```bash
dev init .
dev sync code
dev harness run
```

Because the install is **editable** (`-e`), `git pull` automatically picks up any updates — no reinstall needed.

> **Alternatives without pip install:**
> Use `dev.sh` (macOS/Linux) or `dev.bat` (Windows) from the repo root directly,
> or set an alias: `alias dev='/path/to/cortex-harness/dev.sh'`


---

## 1. Commands

The CLI has **two independent command groups** serving different roles:

| Group | Purpose |
| --- | --- |
| `dev init / sync / mcp` | **Data pipeline** — ingest code & docs into Neo4j + Qdrant, manage MCP servers |
| `dev harness` | **Agent harness** — manage AI agent task sessions, context selection, verify gates |

### Setup

| Command | Description |
| --- | --- |
| `dev init` | Interactive wizard — create/update config and scaffold project folders |
| `dev init --env prod` | Configure the `prod` environment (default: `dev`) |
| `dev init --project-dir /path` | Target a specific project directory |
| `dev status` | Show active config (Neo4j, Qdrant, folders, environments) |

### Sync — Source Code

| Command | Description |
| --- | --- |
| `dev sync code` | Interactive folder picker; auto-detects languages; incremental if baseline exists |
| `dev sync code all` | Run **all** available analyzers on every configured folder |
| `dev sync code add` | Add a new source project (git URL + folders) to the active config |
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
| `dev sync doc add` | Add a new doc project (git URL + folders) to the active config |
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
| `dev mcp start` | Start code-tiny (port 8788) and doc-tiny (port 8789) in background |
| `dev mcp start --force-restart` | Kill existing processes then restart |
| `dev mcp add` | Write `.mcp.json` in project root (workspace scope, default) |
| `dev mcp add --scope global` | Patch system-wide agent config files |
| `dev mcp add --agent claude` | Target a specific agent: `claude` / `claude-code` / `vscode` / `cursor` / `all` |

> Config files are backed up before modification (`*.bak.<timestamp>.json`).
> Supported agents and config paths:
> - **Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json`
> - **Claude Code** — `~/.claude/settings.json`
> - **VS Code** — `~/Library/Application Support/Code/User/mcp.json`
> - **Cursor** — `~/.cursor/mcp.json`
> - **Workspace** — `.mcp.json` in project root

### Harness — Agent Session Orchestration

| Command | Description |
| --- | --- |
| `dev harness init` | Bootstrap `.harness/` structure in a target project |
| `dev harness init --project-dir /path` | Bootstrap in a specific project directory |
| `dev harness status` | Show task backlog summary + MCP endpoint health |
| `dev harness task list` | List all tasks (ID, status, priority, type, title) |
| `dev harness task add` | Add a new task interactively |
| `dev harness task show <id>` | Show full JSON for one task |
| `dev harness run` | Run orchestrator session for next `todo` task |
| `dev harness run --task-id task-002` | Run a specific task by ID |
| `dev harness run --max-rounds 3` | Override max rounds from config |
| `dev harness context <id>` | Run context_selector.py for a task (stdout) |
| `dev harness verify` | Run verify gate (test/lint/type commands from config) |

> Harness config lives in `.harness/config.yaml` inside the target project.
> Session logs are written to `.harness/state/session_log/<session-id>.json`.

---

## 2. Workflows

### Workflow A — Data Pipeline (sync + mcp)

Used to populate the knowledge graph with code structure and document content so that AI agents can query it via MCP.

```
┌─────────────────────────────────────────────────────────────────┐
│                   Data Pipeline Workflow                        │
└─────────────────────────────────────────────────────────────────┘

  [Target project]                [CortexHarness services]

  dev init                        Neo4j (bolt://localhost:7687)
    │  └─ creates                 Qdrant (localhost:6333)
    │     .cortext-harness/
    │     config/dev.json
    │
    ├── dev mcp start ──────────► code-tiny  :8788  (code graph MCP)
    │                             doc-tiny   :8789  (doc RAG MCP)
    │
    ├── dev mcp add ─────────────► .mcp.json  (workspace)
    │                           or ~/.claude/settings.json  (global)
    │
    ├── dev sync code ──────────► [language analyzers]
    │   │  (auto-detect lang)       kotlin / java / ts / js
    │   │  (git diff → incremental) php / python / csharp / …
    │   └─────────────────────►   Neo4j: call graph, symbols
    │                             Qdrant: semantic embeddings
    │
    └── dev sync doc ───────────► [graphrag_ingest_langextract.py]
        │  (git diff → hash)        pdf / md / docx / pptx / xlsx
        └─────────────────────►   Neo4j: entities, relations
                                  Qdrant: paragraph embeddings

                                        │
                                        ▼
                              AI Agent queries via MCP
                              ┌─────────────────────┐
                              │  query_subgraph      │  ← code-tiny
                              │  hybrid_search       │  ← doc-tiny
                              └─────────────────────┘
```

**Typical cadence:**

```bash
# First time
dev init                    # configure project
dev mcp start               # start both servers
dev mcp add                 # register in .mcp.json
dev sync code all           # full code ingest
dev sync doc all            # full doc ingest

# Daily / on change
dev sync code               # incremental (git diff)
dev sync doc                # incremental (git diff → hash)
```

---

### Workflow B — Agent Harness (harness)

Used to run AI agent sessions against the populated knowledge graph with structured task management, context selection, and verify gates.

```
┌─────────────────────────────────────────────────────────────────┐
│                   Agent Harness Workflow                        │
└─────────────────────────────────────────────────────────────────┘

  [Target project]                [CortexHarness services]

  dev harness init                Must be running:
    │  └─ creates                   code-tiny  :8788  (graph_mcp)
    │     .harness/                 doc-tiny   :8789  (mind_mcp)
    │     ├── config.yaml
    │     ├── AGENT.md
    │     ├── scripts/
    │     │   ├── init.sh
    │     │   ├── verify.sh
    │     │   ├── context_selector.py
    │     │   └── orchestrator.py
    │     └── state/
    │         └── feature_list.json
    │
    ├── dev harness task add        ← interactive: title, type,
    │   └─ feature_list.json           priority, entry node
    │
    ├── dev harness task list       ← view backlog
    │
    └── dev harness run ──────────► orchestrator.py
            │
            ├── 1. init.sh ────────► MCP health check
            │                        (graph_mcp + mind_mcp)
            │
            ├── 2. context_selector.py
            │       │
            │       ├── query graph_mcp (query_subgraph)
            │       │       └── code call graph context
            │       │
            │       └── query mind_mcp (hybrid_search)
            │               └── doc vector context
            │                   + fallback queries if empty
            │
            ├── 3. [agent work loop]  max_rounds from config
            │       └── reads context file, edits code
            │
            └── 4. verify.sh ──────► CRITICAL_TEST_CMD
                    │                CRITICAL_LINT_CMD
                    │                CRITICAL_TYPE_CMD
                    │
                    ├── PASS → task status = "done"
                    └── FAIL → task status = "blocked"
                                (retry next round, or stop)

  dev harness status              ← view counts + MCP health
  dev harness task show task-002  ← inspect session result
```

**Typical cadence:**

```bash
# One-time setup
dev harness init            # scaffold .harness/ in project

# Per feature / bug fix
dev harness task add        # define task with entry node
dev harness run             # run orchestrator (picks next todo)
dev harness status          # check result
dev harness verify          # re-run verify gate manually if needed
```

---

### How the Two Workflows Relate

```
┌────────────────────────────────────────────────────────────────────┐
│                                                                    │
│   [Workflow A — Data Pipeline]    [Workflow B — Agent Harness]     │
│                                                                    │
│   dev sync code ──────┐           dev harness run ─────┐          │
│   dev sync doc  ──────┤                                 │          │
│                        │                                │          │
│                        ▼                                ▼          │
│              ┌──────────────────┐          ┌──────────────────┐   │
│              │     Neo4j        │◄─────────│ context_selector │   │
│              │  (call graph +   │          │  query_subgraph  │   │
│              │   entities)      │          └──────────────────┘   │
│              └──────────────────┘                   │              │
│              ┌──────────────────┐          ┌──────────────────┐   │
│              │     Qdrant       │◄─────────│ context_selector │   │
│              │  (code + doc     │          │  hybrid_search   │   │
│              │   embeddings)    │          └──────────────────┘   │
│              └──────────────────┘                   │              │
│                        ▲                            │              │
│                        │                    context.json           │
│              Sync keeps the graph               fed to agent       │
│              fresh. Harness queries             work loop          │
│              it per task.                                          │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

**Sync must run first** (or alongside) to keep Neo4j + Qdrant populated.
Harness relies on the knowledge graph being up-to-date to produce useful context.

---

## 3. Usage from a Project Directory

After `pip install -e .`, navigate to any project folder and use `dev` directly.

```bash
cd /path/to/your-project

# First-time setup for this project
dev init .                  # initialise config in current directory
dev status                  # verify active config

# Data pipeline
dev sync code               # incremental code sync (git diff → mtime)
dev sync code all           # full sync, all analyzers
dev sync doc                # incremental doc sync
dev sync doc all            # full doc sync
dev mcp start               # start code-tiny (:8788) + doc-tiny (:8789)
dev mcp add                 # register servers in .mcp.json

# Agent harness
dev harness init            # scaffold .harness/ in current project
dev harness task add        # add a task to the backlog
dev harness task list       # view backlog
dev harness run             # run orchestrator on next todo task
dev harness status          # summary + MCP health check
```

The `dev init .` dot argument tells the CLI to use the **current directory** as the project root, regardless of where the cortex-harness repo is cloned.

---

## 4. Data Pipeline Configuration

Each project stores its own config under `.cortext-harness/config/<env>.json` (e.g. `dev.json`, `prod.json`).

Generated by `dev init`, the config holds separate sections for code and doc pipelines. Both sections support multiple source projects via `source.projects`:

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
    "source": {
      "projects": [
        {
          "git": "https://github.com/company/project-a.git",
          "folder": ["src", "libs"]
        },
        {
          "git": "https://github.com/company/project-b.git",
          "folder": ["backend", "scripts"]
        }
      ]
    }
  },
  "doc": {
    "env": {
      "NEO4J_URI": "bolt://localhost:7687",
      "EMBEDDING_MODEL": "BAAI/bge-m3",
      "...": "..."
    },
    "source": {
      "projects": [
        {
          "git": "",
          "folder": ["docs", "docs/design-docs", "docs/specs"]
        }
      ]
    }
  }
}
```

**Source project fields:**

| Field | Description |
| --- | --- |
| `git` | Git remote URL — informational only (for tracking purposes). Leave blank for local repos. |
| `folder` | List of relative or absolute folder paths to include in sync. |

**Managing projects:**

```bash
dev init                    # set up project #1 (code + doc)
dev sync code add           # append a new code project to the list
dev sync doc add            # append a new doc project to the list
dev status                  # shows all projects and their folder counts
```

Multiple environments are supported. Only one can be `"active": true` at a time — switching is handled automatically by `dev init`.

---

## 5. Harness Configuration

Harness config lives in `.harness/config.yaml` inside the target project (created by `dev harness init`):

```yaml
version: 1

mcp:
  graph_mcp_url: "http://127.0.0.1:8788/mcp"   # code-tiny endpoint
  mind_mcp_url:  "http://127.0.0.1:8789/mcp"   # doc-tiny endpoint
  graph_mcp_tool: "query_subgraph"              # tool name to call
  mind_mcp_tool:  "hybrid_search"
  mind_fallback_enabled: true                   # retry with alt queries if empty
  mind_fallback_top_k: 8
  budget_max_tool_calls: 50

budget:
  max_rounds: 2              # how many agent work loops per session
  max_tokens: 120000
  max_duration_seconds: 1800

verify:
  critical:
    test_cmd: "pytest -q"    # must pass for task to be marked done
    lint_cmd: ""
    type_cmd: ""
```

Task backlog lives in `.harness/state/feature_list.json`:

```json
{
  "features": [
    {
      "id": "task-001",
      "title": "Fix login timeout on mobile",
      "type": "bugfix",
      "status": "todo",
      "priority": 1,
      "graph_entry_node": "auth.LoginService.authenticate",
      "related_modules": ["auth", "session"],
      "related_files": ["src/auth/login.py"],
      "notes": "Reported in issue #42"
    }
  ]
}
```

Task lifecycle: `todo` → `in_progress` → `done` | `blocked`

---

## 6. Sync State & Incremental Logic

Sync state is stored per-folder in `.cortext-harness/sync-state/`. Each entry records the last git commit, timestamp, and file hashes used as baseline for the next incremental run.

**Code sync** delegates change detection to each analyzer's built-in `--changed-files-manifest` mechanism.

**Doc sync** detects changes in this priority order:
1. `git diff` since last synced commit
2. SHA-256 hash comparison against stored hashes
3. `mtime` comparison against last sync timestamp

Sensitive files (`.env`, `*.pem`, `*.key`, `*secret*`, etc.) are always excluded before transmission.

---

## 7. Environment Management

The CLI automatically locates the Python interpreter inside `.venv/` for both `code-tiny` and `doc-tiny`, falling back to the system Python if no virtual environment is found.

--- 

## NOTICE FOR WINDOW 

---

## Windows Installation Notes

### Quick Install (Automated)

**PowerShell (Recommended):**
```powershell
# Run as Administrator
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\install-windows.ps1
```

**Command Prompt:**
```cmd
# Run as Administrator
install-windows.bat
```

### Manual Install

### Prerequisites
- Python 3.10+ 
- NVIDIA GPU + CUDA drivers (for GPU acceleration)
- Git

### Installation Steps

**1. Clone and Install CortexHarness**
```powershell
git clone <repo-url>
cd cortex-harness

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -e .
```

**2. Install CUDA-enabled PyTorch (if you have NVIDIA GPU)**
```powershell
# Uninstall CPU-only PyTorch (if installed)
pip uninstall torch torchvision torchaudio

# Install CUDA version (for CUDA 12.4)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**3. Install Code-Tiny Dependencies**
```powershell
# Install additional ML dependencies
pip install -r code-tiny/requirements.txt

# Fix transformers compatibility (if needed)
pip install "transformers<5.0"
```

**4. Create Global CLI Command**

Due to Python environment limitations on Windows, create a global CLI using one of these methods:

**Option A: Using Scoop (Recommended)**
```powershell
# Create shim file
echo "path = `\"C:\ai\cortex-harness\.venv\Scripts\dev.exe`"" > C:\Users\$env:USERNAME\scoop\shims\dev.shim
```

**Option B: PowerShell Alias**
```powershell
# Add to PowerShell profile
Add-Content -Path $PROFILE -Value "`nfunction dev { & 'C:\ai\cortex-harness\.venv\Scripts\dev.exe' @Args }"

# Reload profile
. $PROFILE
```

**Option C: Manual Wrapper Script**
```powershell
# Create wrapper script in user directory
@echo off
set CORTEX_HARNESS_DIR=C:\ai\cortex-harness
set PYTHON_EXE=%CORTEX_HARNESS_DIR%\.venv\Scripts\python.exe
set DEV_MODULE=%CORTEX_HARNESS_DIR%\cortex_harness\dev.py
"%PYTHON_EXE%" "%DEV_MODULE%" %*
```

Save as `C:\Users\<username>\dev.cmd` and ensure this directory is in your PATH.

**5. Using in Other Projects**

For each new project, install cortex-harness as a dependency:
```powershell
cd C:\path\to\your-project
python -m venv .venv
.venv\Scripts\activate
pip install -e C:\ai\cortex-harness
pip install -r C:\ai\cortex-harness\code-tiny\requirements.txt
```

Then use `uv run dev <command>` or `.venv\Scripts\dev.exe <command>`.

**6. Verify Installation**
```powershell
# Test CUDA availability
python -c "import torch; print('CUDA available:', torch.cuda.is_available())"

# Test CLI
dev --help
dev status
```

### Common Windows Issues

**Issue**: `ModuleNotFoundError: No module named 'requests'`
**Fix**: Install code-tiny dependencies: `pip install -r C:\ai\cortex-harness\code-tiny\requirements.txt`

**Issue**: `TypeError: got multiple values for keyword argument 'fix_mistral_regex'`
**Fix**: Downgrade transformers: `pip install "transformers<5.0"`

**Issue**: `AssertionError: Torch not compiled with CUDA enabled`
**Fix**: Install CUDA PyTorch: `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124`

**Issue**: `'dev' is not recognized as a command`
**Fix**: Use one of the CLI setup methods above or run: `C:\ai\cortex-harness\.venv\Scripts\dev.exe <command>`

## CUDA ONLY 
Clean install
```
uv pip uninstall torch torchvision torchaudio
uv cache clean
uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```
check cuda
```
python -c "import torch; print('torch', torch.__version__); print('cuda', torch.version.cuda); print('cuda_available', torch.cuda.is_available()); print('gpu', torch.cuda.get_device_name(0))"
```
you can see:
```
torch 2.x.x+cu128
cuda 12.8
cuda_available True
gpu NVIDIA GeForce RTX 5060 Ti
```
