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

The lifecycle commands use Python on macOS/Linux and Windows PowerShell on Windows. Python 3.10+ is required.

```bash
git clone https://github.com/baka3k/cortex-harness.git
cd cortex-harness

make install     # create/reuse .venv, install dependencies, and install global dev command
make build       # create/reuse .venv and install root, code-tiny, and doc-tiny dependencies only
make infra-up    # pull/start Qdrant (:6333) and FalkorDB (:6379) Docker containers
make doctor      # check Python deps, Docker, database ports, and MCP ports
make start       # open code-tiny (:8788) and doc-tiny (:8789) in separate terminal windows
make stop        # stop MCP terminal/processes started by make start
make infra-down  # stop the Qdrant/FalkorDB containers managed by make infra-up
make uninstall   # remove the global dev command installed by make install
```

`make install` installs `dev.cmd` to `%USERPROFILE%\.local\bin` on Windows and `dev` to `~/.local/bin` on macOS/Linux. Make sure that directory is on your shell `PATH`.

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
| `dev sync code` | Interactive folder picker; auto-detects languages; incremental if baseline exists |
| `dev sync code all` | Run **all** available analyzers on every configured folder |
| `dev sync code add` | Add a new source project (git URL + folders) to the active config |

> **First run:** always a full sync (no baseline).
> Support: C#, C/C++, Java, JavaScript, Kotlin, PHP, PL/SQL, Swift, TypeScript, TypeScript, Android Kotlin, Android Java, Python, Go, Rust, Delphi 

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
