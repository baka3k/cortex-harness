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

The lifecycle commands use Python on macOS/Linux and Windows PowerShell on Windows. Python 3.12+ is required. Qdrant and FalkorDBLite run as embedded, file-backed libraries; no database daemon or container runtime is required.

```bash
git clone https://github.com/baka3k/cortex-harness.git
cd cortex-harness

uv --version      # uv is required; install it first if this command is unavailable
make build       # create/reuse .venv and install dependencies with uv
make storage-init # create ~/.cortext-harness/v1/instances/default and its manifest
make storage-layout # show resolved owner paths, manifest, and leases
make install     # create/reuse .venv, install dependencies, and install global dev command
make doctor      # isolated Qdrant/FalkorDBLite round-trips plus MCP port diagnostics
make start       # open code-tiny (:8788) and doc-tiny (:8789) in separate terminal windows
make stop        # stop MCP terminal/processes started by make start
make uninstall   # remove the global dev command installed by make install
```

Set `UV` when the executable is not on the default `PATH`, for example `make build UV=/opt/homebrew/bin/uv`.

The default persistent-data tree is independent of every indexed source checkout:

```text
~/.cortext-harness/v1/instances/default/
├── manifest.json
├── qdrant/{code,doc}/
├── falkordb/{code,doc}/data.rdb
└── backups/
```

Set `CORTEX_DATA_HOME` for an isolated test/portable root and `CORTEX_STORAGE_INSTANCE` for a disjoint named deployment. Code and document processes own distinct stores; stop an owner before backup, migration, reset, or another direct open.

Legacy repository-local data is copied, verified, and retained:

```bash
make storage-migrate-layout                         # dry-run
make storage-migrate-layout MIGRATE_ARGS="--apply" # copy and verify
make storage-backup OWNER=code
```

`infra-up` and `infra-down` remain one-release compatibility aliases for storage initialization/no-op. They do not start or stop external services.

`make install` installs `dev.cmd` to `%USERPROFILE%\.local\bin` on Windows and `dev` to `~/.local/bin` on macOS/Linux. Make sure that directory is on your shell `PATH`.

After installation, every root Make lifecycle target has a matching global `dev` command and can be run from any directory:

```bash
dev help
dev build
dev install
dev uninstall
dev storage-layout
dev storage-init
dev storage-migrate-layout                 # dry-run
dev storage-migrate-layout --apply
dev storage-backup --owner code
dev doctor
dev start
dev stop
```

For example, `dev start` is equivalent to `make start`: it opens code-tiny (`:8788`) and doc-tiny (`:8789`) in separate terminal windows, but it can be invoked from any directory. Storage commands resolve the same centralized paths from any working directory.

`dev start` and `make start` keep that behavior when called without parameters. Parameterized starts create named instances that can run alongside one another:

```bash
# One code MCP for project SHOP / graph SHOP on :8790
dev start --server code --name shop-code --project SHOP --port 8790

# Both MCPs for project CRM, with independent ports
dev start --name crm --project CRM --code-port 8800 --doc-port 8801

# Separate graph databases or vector collections per service
dev start --name mixed --code-database CODE_DB --doc-database DOC_DB \
  --code-collection code_vectors --doc-collection doc_vectors \
  --code-port 8810 --doc-port 8811

# Stop one named instance; `dev stop` without options still stops every MCP
dev stop --name crm
```

The equivalent Make syntax passes lifecycle arguments through `START_ARGS` and `STOP_ARGS`:

```bash
make start START_ARGS="--server doc --name shop-doc --project SHOP --port 8791"
make stop STOP_ARGS="--name shop-doc"
```

Useful start options include `--server all|code|doc`, `--name`, `--project`, `--database`/`--db`, service-specific database and collection overrides, `--port`, `--code-port`, `--doc-port`, `--host`, `--path`, and `--provider falkordb|neo4j`. When `--project` is given, it also acts as the default graph database and vector collection unless a more specific option overrides it.

Lifecycle compatibility is gated in GitHub Actions on both Intel and Apple Silicon macOS runners. The gate executes the installed `~/.local/bin/dev` wrapper from outside the repository and validates Make/dev parity, Terminal launcher construction, and start/stop state handling.

Because the install is **editable** (`-e`), `git pull` automatically picks up any updates — no reinstall needed.

---

## 1. Commands

The CLI has **two independent command groups** serving different roles:

| Group | Purpose |
| --- | --- |
| `dev init / sync / mcp` | **Data pipeline** — ingest code & docs into Neo4j + Qdrant, manage MCP servers |

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
| `dev sync code` | Interactive folder picker; reliable hybrid incremental scan by default |
| `dev sync code all` | Run all analyzers on every non-overlapping configured root; still incremental unless `--full-scan` is set |
| `dev sync code add` | Add a new source project (git URL + folders) to the active config |

> **First run:** always a full sync (no baseline).
> **Later runs:** Git supplies committed/staged/unstaged/untracked candidates and SHA-256 inventory confirms content changes. Initialized submodules are discovered recursively. Non-Git roots automatically use hash mode.
> Use `--change-detection hash` or `--reconcile` for a full content check, `--submodules ignore` to disable recursive submodule coverage, and `--lock-timeout-seconds N` to control same-scope contention.
> Support: C#, C/C++, Java, JavaScript, Kotlin, PHP, PL/SQL, Swift, TypeScript, Android Kotlin, Android Java, Python, Go, Perl 5 (`.pl`, `.pm`, `.t`), Rust, Delphi

### Sync — Documentation

| Command | Description |
| --- | --- |
| `dev sync doc` | Interactive folder picker; incremental if baseline exists |
| `dev sync doc all` | Full sync for every configured doc folder |
| `dev sync doc add` | Add a new doc project (git URL + folders) to the active config |

> **First run:** always a full sync (no baseline).
> **Subsequent runs:** incremental — detects changes via git diff → SHA-256 hash comparison → mtime.
> **Supported formats:** `.pdf`, `.md`, `.docx`, `.txt`, `.pptx`, `.xlsx`


### Harness — Agent Session Orchestration

Make sure you installed `dev-kit`  https://github.com/baka3k/dev-kit
$ npx skill-dev
┌   devkit   Dev Kit Installer
│
◆  Select skills
│  ◼ hi-craft
│  ◼ hi-debug
│  ◼ hi-explorer
│  ◼ hi-fix
│  ◼ knows
│  ◼ hi-log
│  ◼ hi-plan (Should ALWAYS activate before implementing ANY implement , or fix.)
│  ◼ hi-predict
│  ◼ hi-problem-solving
│  ◼ hi-scenario
│  ◼ hi-security
│  ◼ hi-sequential-thinking
└ ....

◇ Select target agent
❯ Claude Code
  OpenCode
  Qwen Code
  GitHub Copilot
  Cursor
  Continue
  Generic

◇ Install location
❯ Global (~/.claude/skills)
  Current project

◇ Summary
Agent: Claude Code
Skills: 12 selected
Location: Global
Install? (Y/n)

---

```

### Common Windows Issues

**Issue**: `ModuleNotFoundError: No module named 'requests'`
**Fix**: Install code-tiny dependencies: `uv pip install --python C:\ai\cortex-harness\.venv\Scripts\python.exe --requirements C:\ai\cortex-harness\code-tiny\requirements.txt`

**Issue**: `TypeError: got multiple values for keyword argument 'fix_mistral_regex'`
**Fix**: Downgrade transformers: `uv pip install --python C:\ai\cortex-harness\.venv\Scripts\python.exe "transformers<5.0"`

**Issue**: `AssertionError: Torch not compiled with CUDA enabled`
**Fix**: Install CUDA PyTorch: `uv pip install --python C:\ai\cortex-harness\.venv\Scripts\python.exe torch torchvision torchaudio --default-index https://download.pytorch.org/whl/cu124`

**Issue**: `'dev' is not recognized as a command`
**Fix**: Use one of the CLI setup methods above or run: `C:\ai\cortex-harness\.venv\Scripts\dev.exe <command>`

## CUDA ONLY 
Clean install
```
uv pip uninstall torch torchvision torchaudio
uv cache clean
uv pip install torch torchvision torchaudio --default-index https://download.pytorch.org/whl/cu128
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

## ASP.NET Semantic Overlays

`dev sync code` supports detector-gated `aspnet_framework` and `aspnet_core`
overlays. Both require the canonical `csharp` analyzer and preserve exclusive
`.cs` ownership. The overlays add routes, request pipelines, controllers,
pages/views, services, configuration, state, validation, and result semantics
through one migration-oriented graph contract.

Use `aspnet-framework`, `asp.net-framework`, `aspnet-core`, or `asp.net-core`
as unified MCP parser aliases. Roslyn workspace loading is attempted in
`auto` mode; unavailable legacy reference assemblies or SDK workloads produce
explicit partial coverage.
