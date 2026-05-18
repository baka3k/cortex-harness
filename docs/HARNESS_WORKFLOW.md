---

# Harness Engineering Workflow

End-to-end guide: from initial setup to daily operations with an AI coding agent.

---

## System Overview

```
┌──────────────────────────────────────────────────────────────┐
│  Source code / Docs                                          │
│  (your project)                                              │
└────────────┬─────────────────────────────────────────────────┘
             │ dev sync code / dev sync doc
             ▼
┌────────────────────────────┐   ┌──────────────────────────┐
│  Neo4j                     │   │  Qdrant                  │
│  call graph, symbols,      │   │  code embeddings,        │
│  entities, relations       │   │  doc paragraph vectors   │
└────────────┬───────────────┘   └────────────┬─────────────┘
             │                                │
             └──────────────┬─────────────────┘
                            │ MCP (HTTP)
             ┌──────────────┴──────────────┐
             │  code-tiny :8788            │  graph_mcp
             │  doc-tiny  :8789            │  mind_mcp
             └──────────────┬──────────────┘
                            │ context_selector.py
                            ▼
             ┌──────────────────────────────┐
             │  .harness/                   │
             │  ├── config.yaml             │
             │  ├── state/                  │
             │  │   ├── feature_list.json   │
             │  │   ├── progress.md         │
             │  │   └── session_log/        │
             │  └── scripts/               │
             │      ├── init.sh             │
             │      ├── verify.sh           │
             │      ├── context_selector.py │
             │      └── orchestrator.py     │
             └──────────────┬──────────────┘
                            │ dev harness run
                            ▼
                     AI Coding Agent

```

---

## Part 1 — One-Time Setup per Machine

```bash
git clone <repo-url> /path/to/cortex-harness
cd /path/to/cortex-harness

python -m venv .venv
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows

pip install -r requirements.txt
pip install -e .

```

After this step, the `dev` command can be invoked from any directory.

---

## Part 2 — Initializing a New Project

### Step 1: Initialize the Data Pipeline Configuration

Navigate to your project directory and run:

```bash
cd /path/to/your-project
dev init .

```

The wizard will prompt you for the following inputs:

| Prompt | What to Fill |
| --- | --- |
| Project code | Short alphanumeric ID (no accents/spaces), e.g., `my-app` |
| Project name | Display name |
| NEO4J_URI | `bolt://localhost:7687` |
| NEO4J_PASS | Neo4j password |
| QDRANT_HOST | `localhost` |
| EMBEDDING_MODEL | Keep default or change to your local model |
| device | `cpu` / `mps` (Apple Silicon) / `cuda` |
| Source folders | Directories containing source code (e.g., `/path/to/your-project/src`) |
| Doc folders | Directories containing documentation (e.g., `/path/to/your-project/docs`) |

The configuration will be saved to `.cortext-harness/config/dev.json` within the project.

### Step 2: Initialize the Harness

```bash
dev harness init

```

The wizard will ask for additional configuration:

| Prompt | What to Fill |
| --- | --- |
| graph_mcp_url | `[http://127.0.0.1:8788/mcp](http://127.0.0.1:8788/mcp)` |
| mind_mcp_url | `[http://127.0.0.1:8789/mcp](http://127.0.0.1:8789/mcp)` |
| critical test_cmd | The project's test command, e.g., `pytest -q` or `./gradlew test` |
| critical lint_cmd | The lint command, e.g., `ruff check src/` or leave empty |
| critical type_cmd | The typecheck command, e.g., `mypy src/` or leave empty |

**Result** — The `.harness/` directory structure is generated:

```
.harness/
├── config.yaml                 ← MCP endpoints + budget + verify commands
├── harness_manifest.json       ← Initialization metadata
├── scripts/
│   ├── init.sh                 ← MCP health check
│   ├── verify.sh               ← Test/lint/type gate
│   ├── context_selector.py     ← Queries graph + vector context
│   └── orchestrator.py         ← Handles session lifecycle
├── state/
│   ├── feature_list.json       ← Task backlog
│   ├── progress.md             ← Session continuity log
│   └── session_log/            ← JSON logs for each session
└── templates/
    ├── AGENT.md                ← Working contract/instructions for the agent
    ├── session-handoff.md      ← Handoff template between sessions
    └── ...

```

---

## Part 3 — Ingesting Code and Docs into the System

### Initial Sync (Full Sync)

```bash
# Start the MCP servers first
dev mcp start

# Ingest the entire source code base
dev sync code all

# Ingest all documentation
dev sync doc all

```

This process duration varies depending on the project size. `code-tiny` parses ASTs, call graphs, and symbols, then indexes them into Neo4j + Qdrant. `doc-tiny` extracts entities and embeds paragraph vectors.

### Subsequent Syncs (Incremental Sync)

```bash
dev sync code     # Syncs modified files only (git diff → mtime fallback)
dev sync doc      # Syncs modified files only (git diff → hash → mtime)

```

Incremental sync runs automatically once a baseline exists from the initial sync. No extra flags are needed.

### Adding New Folders to the Project

```bash
dev sync code add /path/to/new-module
dev sync doc add /path/to/new-docs

```

Or add multiple paths with git integration:

```bash
dev sync code add /path/a /path/b --git https://github.com/org/repo.git

```

### Checking Sync Status

```bash
dev status     # View active configuration: folders, environment, and project count

```

---

## Part 4 — Configuring `.harness/config.yaml`

This file governs the entire behavior of the harness. Edit it directly after running `dev harness init`:

```yaml
version: 1

mcp:
  graph_mcp_url: "http://127.0.0.1:8788/mcp"   # code-tiny
  mind_mcp_url:  "http://127.0.0.1:8789/mcp"   # doc-tiny
  graph_mcp_tool: "query_subgraph"              # Primary tool on graph_mcp
  mind_mcp_tool:  "hybrid_search"               # Primary tool on mind_mcp
  mind_fallback_enabled: true                   # Retry using alternative queries if results are empty
  mind_fallback_top_k: 8                        # Number of docs returned per fallback query
  budget_max_tool_calls: 50                     # Limit on MCP tool calls per session

budget:
  max_rounds: 2              # Number of agent iterations before running verification
  max_tokens: 120000
  max_duration_seconds: 1800

verify:
  critical:
    test_cmd: "pytest -q"    # MUST PASS for the task to be marked done
    lint_cmd: "ruff check src/"
    type_cmd: "mypy src/"

```

**Verification Rule**: If `verify.sh` exits with a non-zero status → the task status is set to `blocked` instead of `done`. The agent must resolve the failures for the session to complete successfully.

**Configuration hot-swapping** — `orchestrator.py` and `dev harness verify` read directly from this file, meaning changes take effect instantly without reinstalling.

---

## Part 5 — Task Creation and Management

### Creating a New Task

```bash
dev harness task add

```

The wizard will prompt you for the following fields:

| Prompt | Explanation |
| --- | --- |
| Title | Short description, e.g., "Fix login timeout on mobile" |
| Type | `feature` / `bugfix` / `refactor` |
| Priority | Integer value, where 1 = highest priority |
| Graph entry node | The entry point in the call graph, e.g., `auth.LoginService.authenticate` |
| Related modules | Impacted modules, e.g., `auth,session` |
| Related files | Specific files the agent is expected to modify, e.g., `src/auth/login.py` |
| Notes | Additional context, issue tracker links, or bug reproductions |

The **Graph entry node** is a crucial field: `context_selector.py` leverages this value to invoke `query_subgraph` via code-tiny, fetching the surrounding call graph to feed into the agent's context.

To discover entry nodes, query the MCP server directly or search inside Neo4j:

```bash
# Query through the context selector
dev harness context task-001

# Or search the code base and format according to these patterns:
# package.ClassName.methodName
# module.function_name

```

### Viewing the Task Backlog

```bash
dev harness task list

```

Output:

```
── Task backlog ─────────────────────────
  task-001  [todo]        P1  bugfix   Fix login timeout
  task-002  [in_progress] P1  feature  Add OAuth support
  task-003  [done]        P2  refactor Extract auth middleware
  task-004  [blocked]     P1  bugfix   Payment webhook crash

```

### Inspecting Task Details

```bash
dev harness task show task-001

```

This returns the full JSON output containing all metadata fields.

### Editing a Task

Edit `.harness/state/feature_list.json` directly. The file is stored as standard plain JSON.

### Task Lifecycle

```
todo → in_progress → done
                  ↘ blocked

```

* `todo` → The agent automatically flips this to `in_progress` upon execution.
* `done` → Assigned only when `verify.sh` exits with code 0.
* `blocked` → Triggered if verification checks continue to fail after reaching `max_rounds`.

---

## Part 6 — Running a Harness Session

### Method 1: Via `dev harness run` (Orchestrated)

```bash
dev harness run                        # Runs the highest-priority `todo` task
dev harness run --task-id task-003     # Targets a specific task
dev harness run --max-rounds 3         # Overrides the execution rounds defined in config

```

Executing `dev harness run` triggers `orchestrator.py` which executes the following workflow:

1. Loads the target task from `feature_list.json`
2. Updates the task status to `in_progress`
3. Executes `init.sh` (MCP health check)
4. Executes `context_selector.py` → compiles the context markdown file
5. Launches the agent command (if passed via `--agent-command`)
6. Executes `verify.sh` with environment variables loaded from `config.yaml`
7. If checks pass → sets to `done`; if they fail → retries up to `max_rounds` times
8. Appends the execution logs to `.harness/state/session_log/<session-id>.json`

### Method 2: Agent-Driven Execution (Manual)

When interacting directly with Claude Code / Cursor / Copilot, the agent consumes `AGENT.md` and drives the workflow natively using these commands:

```bash
# 1. Agent runs the initial diagnostic check
bash .harness/scripts/init.sh

# 2. Agent builds the context payload for the assigned task
dev harness context task-001

# 3. Agent modifies code and implements changes...

# 4. Agent runs verification before declaring victory
dev harness verify

```

### Manually Building Context

```bash
dev harness context task-001              # Outputs content to stdout
dev harness context task-001 --output ctx.md   # Saves content straight to a markdown file

```

The `context_selector` queries `graph_mcp` (fetching the call graph local to the `graph_entry_node`) and `mind_mcp` (performing a hybrid search driven by the task's title, type, and keywords), merges the outputs, and structures it into clean markdown.

---

## Part 7 — System Health Check

```bash
dev harness status

```

Output:

```
── Harness status: /path/to/project ─────────────
  Tasks total : 5
  todo        : 2
  in_progress : 1
  done        : 1
  blocked     : 1

  In-progress : [task-002] Add OAuth support

── MCP endpoints ────────────────────────────────
  graph_mcp  http://127.0.0.1:8788/mcp
             → reachable (HTTP 200)
  mind_mcp   http://127.0.0.1:8789/mcp
             → reachable (HTTP 200)

```

---

## Part 8 — Troubleshooting Common Issues

### MCP Endpoint Unreachable

```
[init][warn] graph_mcp may be unreachable: http://127.0.0.1:8788/mcp (HTTP 000)

```

**Resolution:**

```bash
dev mcp start                  # Restart both backend servers
dev harness status             # Verify connectivity status

```

If the error persists, check whether Neo4j and Qdrant are active — both `code-tiny` and `doc-tiny` require stable database connections during boot.

### verify.sh Failures

```
[verify][fail][critical] critical-test
[verify] Result: FAIL (critical checks failed)

```

**Resolution:**

```bash
# Identify which precise check failed
dev harness verify

# Fix the code errors locally, then re-verify
dev harness verify

# If a task was prematurely blocked, reset it manually
# Edit .harness/state/feature_list.json: set "status": "todo"
dev harness run --task-id task-001

```

### Task Stuck in `in_progress`

This usually happens if a session crashes midway. You can resolve this with a manual file update:

```bash
# Open the following state file:
# .harness/state/feature_list.json
# Change "status": "in_progress" → "status": "todo"
# Delete the "session_id" field if present

dev harness task list   # Verify status reset
dev harness run         # Resume runner execution

```

### Databases (Neo4j / Qdrant) Not Receiving Data

```bash
# Validate pipeline connection credentials
dev status

# Trigger a clean sync (bypassing any incremental cache)
dev sync code all
dev sync doc all

```

To wipe the slate clean and sync from scratch, clear the synchronization tracking state:

```bash
rm -rf .cortext-harness/sync-state/
dev sync code all
dev sync doc all

```

### Appending New Folders to an Active Project

```bash
dev sync code add /path/to/new-folder
dev sync doc add /path/to/new-docs
dev status   # Confirm paths were appended correctly

```

### Invalid Pipeline Configuration (NEO4J_PASS, QDRANT_HOST...)

```bash
dev init .                # Re-run the configuration wizard to overwrite settings
dev sync code all         # Force a full sync to re-index data under new credentials

```

### GLiNER Model Loading Failures

```
FileNotFoundError: GLiNER local model path not found: /path/to/model

```

**Resolution:**

```bash
# Option 1: Unset the local path variable to fall back to HuggingFace downloads
unset GLINER_MODEL_PATH

# Option 2: Download the model files explicitly into the configured path
huggingface-cli download urchade/gliner_large-v2.1 \
  --local-dir /path/to/model

# Option 3: Swap out providers to bypass GLiNER entirely
dev sync doc --entity-provider langextract

```

---

## Part 9 — Daily Workflow

### Morning Check-in / Starting Work

```bash
cd /path/to/project

# 1. Sync the latest changes to your code and docs
dev sync code
dev sync doc

# 2. Check system and endpoint health
dev harness status

# 3. Inspect your active backlog
dev harness task list

```

### Initiating a Feature or Bugfix

```bash
# 1. Provision a new task tracking item
dev harness task add

# 2. Fire up the orchestration session
dev harness run --task-id task-XXX

# Alternatively, let the runner select the highest priority item automatically
dev harness run

```

### Pushing Code Changes

```bash
git add . && git commit -m "..."
git push

# Keep your knowledge base graph aligned with the latest remote commit
dev sync code

```

### Modifying Documentation or Specifications

```bash
dev sync doc     # Fast, incremental updates indexing changed files only

```

---

## Part 10 — Registering MCP Servers for AI Agents

To allow systems like Claude Code, Cursor, or VS Code to call tools directly:

```bash
# Workspace Scope (Project specific) — generates a local .mcp.json file
dev mcp add

# Global Scope (Available across all machine profiles)
dev mcp add --scope global

# Target specific IDE environments or agents
dev mcp add --agent claude-code
dev mcp add --agent cursor
dev mcp add --agent vscode
dev mcp add --agent all

```

Once registered, your agent can natively execute:

* `query_subgraph` → Pulls code call graphs using code-tiny.
* `hybrid_search` → Searches through indexed text using doc-tiny.

---

## Part 11 — Managing Development vs. Production Environments

```bash
# Initialize a distinct production environment configuration
dev init --env prod .

# Execute syncing explicitly targeted at your production backend
dev sync code --env prod     # Overrides active config environment

```

Each environment manages its data isolated inside `.cortext-harness/config/prod.json`. Only a single environment can have `active: true` at a time — running `dev init` automatically deactivates previous environments when spinning up a new target profile.

---

## Quick Reference

```bash
# ── Setup ──────────────────────────────────────────────────
dev init .                        # init data pipeline config
dev harness init                  # init harness structure

# ── Sync ───────────────────────────────────────────────────
dev sync code                     # incremental code sync
dev sync code all                 # full code sync
dev sync doc                      # incremental doc sync
dev sync doc all                  # full doc sync
dev sync code add /path           # add folder to code pipeline
dev sync doc add /path            # add folder to doc pipeline

# ── MCP ────────────────────────────────────────────────────
dev mcp start                     # start code-tiny + doc-tiny
dev mcp start --force-restart     # kill & restart
dev mcp add                       # register in .mcp.json
dev mcp add --scope global        # register globally

# ── Harness — status & config ──────────────────────────────
dev harness status                # backlog summary + MCP health
dev status                        # data pipeline config overview

# ── Harness — tasks ────────────────────────────────────────
dev harness task list             # view backlog
dev harness task add              # add task interactively
dev harness task show task-001    # inspect task JSON

# ── Harness — sessions ─────────────────────────────────────
dev harness run                   # run next todo task
dev harness run --task-id task-002       # run specific task
dev harness run --max-rounds 3           # override rounds
dev harness context task-001             # build + print context
dev harness context task-001 --output ctx.md   # save context
dev harness verify                       # run verify gate

```