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

The lifecycle commands use Python on macOS/Linux and Windows PowerShell on Windows. Python 3.12+ is required. Qdrant runs as an embedded, file-backed library and the graph store is an embedded database (LadybugDB on Windows by default, FalkorDBLite elsewhere); no database daemon or container runtime is required.

### Graph providers

| | Windows 10/11 (x64, ARM64) | macOS 12–14 | macOS 15+ | Linux (glibc 2.26+) |
|---|---|---|---|---|
| Default graph provider | **ladybug** (embedded) | falkordb (embedded) | falkordb (embedded) | falkordb (embedded) |
| Opt-in ladybug | `GRAPH_PROVIDER=ladybug` | `GRAPH_PROVIDER=ladybug` | `GRAPH_PROVIDER=ladybug` | `GRAPH_PROVIDER=ladybug` |
| Opt-in embedded falkordb | install extra + remote URI (see rollback) | default | default | default |
| Remote graph (`FALKORDB_URI` / Neo4j) | `GRAPH_PROVIDER=falkordb` or `neo4j` | supported | supported | supported |

LadybugDB ([PyPI `ladybug`](https://pypi.org/project/ladybug/)) is an embedded graph database — one store file per named graph, no server, no Docker. The default store for the code owner lives at `~/.cortext-harness/v1/instances/default/ladybug/code/code.lbug/hyper_graph`. Useful environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GRAPH_PROVIDER` | `ladybug` on win32, `falkordb` elsewhere | Provider selection (`ladybug`, `falkordb`, `neo4j`; `kuzu` is accepted as a deprecated alias of `ladybug`) |
| `LADYBUG_PATH` | derived from `CORTEX_DATA_HOME` | Store file for the active owner's primary graph |
| `LADYBUG_GRAPH` | `hyper_graph` | Primary named graph |
| `LADYBUG_QUERY_TIMEOUT_MS` | `120000` | Per-query timeout (mirrors `FALKORDB_QUERY_TIMEOUT_MS`) |
| `LADYBUG_BUFFER_POOL_SIZE` | library default | Byte size of Ladybug's buffer pool |
| `CORTEX_GRAPH_AUTO_DDL` | on | Auto-`ALTER TABLE` when analyzers write properties missing from the static schema (off = fail closed). Every auto-DDL statement is logged loudly. |

**Windows quickstart (no Docker):**

```bat
git clone https://github.com/baka3k/cortex-harness.git && cd cortex-harness
install-windows.bat     \ REM or install-windows.ps1
dev storage-init
dev doctor              \ REM includes the ladybug round-trip probe
dev sync-processes      \ REM ingests into the embedded LadybugDB store
```

**Rollback to FalkorDB** (any platform): set `GRAPH_PROVIDER=falkordb` plus `FALKORDB_URI` for a remote server, or install the historical embedded backend on POSIX with `pip install -e '.[falkordb-local]'` and `FALKORDB_PATH=<data.rdb>`. On Windows only the remote path is available (FalkorDBLite has no win32 wheels). No code changes or data migration required — providers write to separate stores.

**macOS note:** the LadybugDB wheel floor is macOS 15 (Sequoia) per PyPI metadata; older macOS keeps FalkorDBLite as the default and can still use ladybug if the wheel installs.

```bash
git clone https://github.com/baka3k/cortex-harness.git
cd cortex-harness

make build       # build the cortex-dev binary (cargo workspace) + provision the ONNX Runtime dylib
make storage-init # create ~/.cortext-harness/v1/instances/default and its manifest
make storage-layout # show resolved owner paths, manifest, and leases
make install     # install the cortex-dev binary + the global `dev` command (~/.local/bin)
make doctor      # isolated Qdrant/graph round-trips (FalkorDBLite + LadybugDB) plus MCP port diagnostics
make start       # open code-tiny (:8788) and doc-tiny (:8789) in separate terminal windows
make stop        # stop MCP terminal/processes started by make start
make uninstall   # remove the global dev command installed by make install
```

The dev/make layer is the **cortex-dev binary** (phase-06 cutover): `dev.sh`,
`dev.bat`/`dev.ps1`, the global wrappers, and every Makefile lifecycle target
resolve the binary via `CORTEX_DEV_BIN` → `~/.local/bin/cortex-dev` →
`rust/target/release/`. There is no Python entrypoint rollback; to downgrade,
reinstall the previous release binary (see `docs/cutover-runbook.md`). The
Python source tree remains only as the parity reference for
`scripts/rust_parity/` plus a small documented forced list (torch device
probe, `.harness` project scripts, the doc-tiny ingestor and the
required-journal consumer until their Rust ports land).

The default persistent-data tree is independent of every indexed source checkout:

```text
~/.cortext-harness/v1/instances/default/
├── manifest.json
├── qdrant/{code,doc}/
├── falkordb/{code,doc}/data.rdb
├── ladybug/{code,doc}/{owner}.lbug/<graph>   # one store file per named graph
└── backups/
```

Set `CORTEX_DATA_HOME` for an isolated test/portable root and `CORTEX_STORAGE_INSTANCE` for a disjoint named deployment. Code and document processes own distinct stores; stop an owner before backup, migration, reset, or another direct open.

Legacy repository-local data is copied, verified, and retained:

```bash
make storage-migrate-layout                         # dry-run
make storage-migrate-layout MIGRATE_ARGS="--apply" # copy and verify
make storage-backup OWNER=code
```

`infra-up` and `infra-down` now also manage the local Docker containers for projects that point at remote Qdrant + FalkorDB endpoints over `127.0.0.1`:

- `make infra-up` is idempotent: it inspects `cortex-qdrant` and `cortex-falkordb` first. Running containers are left alone; stopped containers are restarted; missing containers are pulled (only when the image is not cached) and started with ports pinned to `127.0.0.1`. Re-running the command never re-pulls or re-creates an already-running container.
- `make infra-down` stops both managed containers (idempotent — no-op when they are missing or the docker daemon is unreachable) and closes cached remote clients. Local file-backed storage is left on disk by design.
- The FalkorDB image (`falkordb/falkordb`) ships with a Browser UI on `http://127.0.0.1:3000`; the Qdrant Dashboard is reachable at `http://127.0.0.1:6333/dashboard`. Port pinning to `127.0.0.1` means the services are never exposed beyond the local machine.
- Because FalkorDB serves the database and the Browser UI on separate ports, `infra-up` lists both under the container line so it is clear which is which:

  ```
  [ok] cortex-falkordb running (http://127.0.0.1:3000)
         redis      : redis://127.0.0.1:6379
         Browser UI : http://127.0.0.1:3000
  ```

- For a project on `storage_backend: remote`, `make doctor` appends the Browser UI URL to the FalkorDB check once it is reachable — for example `remote:my_app:falkordb - redis://db.internal:6379 — reachable — Browser UI: http://db.internal:3000`. The host is derived from the configured `falkordb_uri`; the URL is an informational hint and is never probed, so a remote host that does not publish port 3000 still passes. A `unix://` socket URI has no host and therefore shows no UI URL.
- Override the image tag with `QDRANT_IMAGE` / `FALKORDB_IMAGE`; override the host ports with `QDRANT_HTTP_PORT`, `QDRANT_GRPC_PORT`, `FALKORDB_PORT`, `FALKORDB_UI_PORT`. Invalid port values fall back to the defaults rather than crashing `infra-up`. `FALKORDB_UI_PORT` drives the advertised UI URL in both `infra-up` and `doctor`.
- When the docker daemon is unreachable, `make infra-up` logs `[warn] docker not available — skipping container ensure` and continues — purely local projects are unaffected, and remote projects that point at `127.0.0.1` will simply fail the subsequent reachability probe.

If a host port is already bound (for example, by a natively running Qdrant/FalkorDB), `infra-up` reports `[fail] host port already in use — service may already be running natively; remote probe will report reachability` and lets the probe decide.

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
| `dev ignore add <FOLDER>...` | Add folders (names or globs like `generated-*`) to the scan-ignore list of the active config |
| `dev ignore remove <FOLDER>...` | Remove entries from the scan-ignore list (exact match) |
| `dev ignore list` | Print the configured ignore folders |
| `dev status` | Show active config (Neo4j, Qdrant, folders, environments) |
| `dev journal status --journal-path PATH` | Show payload-free recovery queue state |
| `dev journal purge --journal-path PATH --run-id ID --project-id ID --root ROOT` | Purge one expired terminal run after exact-scope safety checks |

> **Remote storage:** `dev init` prompts for `local` or `remote` backend. Press Enter
> to accept local Docker defaults (`http://localhost:6333` / `localhost:6379`), then
> run `dev infra-up --provision` to start containers. For remote servers, enter
> non-localhost URLs and the wizard will prompt for credentials. Secrets are stored
> in plaintext in `.cortext-harness/config/{env}.json` — do not commit populated
> configs to shared repos.

> **Ignore folders:** `dev init` also asks for folders to skip while scanning
> (comma-separated, globs like `generated-*` allowed). Entries add to the
> built-in defaults and apply to both code and doc sync; manage them later
> with `dev ignore add|remove|list`.

Backend selection is per project. A remote project may configure only Qdrant
or only FalkorDB; the missing component is resolved as an explicit local
component before ingest starts. Runtime connection failures never cause a
local fallback. Changing an endpoint, graph, collection, TLS mode, or backend
requires a fresh ingest because journal compatibility is bound to the
credential-free effective target fingerprint. See
[`docs/DATABASE_INTEGRATION.md`](docs/DATABASE_INTEGRATION.md) for the schema,
switching procedure, and force-local behavior.

### Sync — Source Code

| Command | Description |
| --- | --- |
| `dev sync code` | Interactive folder picker; reliable hybrid incremental scan by default |
| | **Sync-plane is Rust-only** (2026-09-16 cutover): the Python orchestrator (`code-tiny/tools/sync/`) was deleted; embedded FalkorDB is fail-closed (use `FALKORDB_URI` remote or `GRAPH_PROVIDER=ladybug`). `CORTEX_SYNC_BACKEND` is retired. Rollback = `git revert` (tag `pre-syncplane-delete`). |
| `dev sync code all` | Run all analyzers on every non-overlapping configured root; still incremental unless `--full-scan` is set |
| `dev sync code add` | Add a new source project (git URL + folders) to the active config |

> **First run:** always a full sync (no baseline).
> **Later runs:** Git supplies committed/staged/unstaged/untracked candidates and SHA-256 inventory confirms content changes. Initialized submodules are discovered recursively. Non-Git roots automatically use hash mode.
> Use `--change-detection hash` or `--reconcile` for a full content check, `--submodules ignore` to disable recursive submodule coverage, and `--lock-timeout-seconds N` to control same-scope contention.
> Support: C#, C/C++, Java, JavaScript, Kotlin, PHP, PL/SQL, Swift, TypeScript, Android Kotlin, Android Java, Python, Go, Perl 5 (`.pl`, `.pm`, `.t`), Rust, Delphi

Graph-write recovery is controlled by `CORTEX_GRAPH_JOURNAL_MODE`: `off`,
`cplus-canary`, `shared-shadow`, or `shared-required`. Global `all-required`
remains fail-closed until every direct mutation path is migrated. Positive
integer overrides are available for `CORTEX_GRAPH_JOURNAL_MAX_BATCHES`,
`CORTEX_GRAPH_JOURNAL_MAX_PAYLOAD_BYTES`,
`CORTEX_GRAPH_JOURNAL_MAX_ARTIFACT_BYTES`,
`CORTEX_GRAPH_JOURNAL_MAX_DATABASE_BYTES`,
`CORTEX_GRAPH_JOURNAL_MIN_FREE_BYTES`,
`CORTEX_GRAPH_JOURNAL_RETENTION_SECONDS`,
`CORTEX_GRAPH_JOURNAL_LEASE_SECONDS`,
`CORTEX_GRAPH_JOURNAL_MAX_ATTEMPTS`, and the retry base/maximum seconds. Invalid
values stop ingestion before graph mutation. Use
`dev journal status --journal-path <exact-path> --json-output` for payload-free
queue state. `dev journal purge` additionally requires the exact run, project,
and source root; active, leased, or retained runs are refused.

#### Graph and embedding phases

`dev sync code` uses `--sync-mode both` by default. In this mode it writes the
primary graph and framework overlays first, runs `project_topology`, and only
then writes embeddings to Qdrant. This guarantees that graph and module
topology queries are available without waiting for embedding to finish.

| Mode | Runs | Storage isolation |
| --- | --- | --- |
| `both` | Graph, framework overlays, topology, then embedding | Normal incremental sync is supported |
| `graph` | Graph, framework overlays, and topology only | Does not open or write Qdrant |
| `embedding` | Embedding only | Does not open or mutate the graph database |

```bash
# Full graph + embedding pipeline
dev sync code --full-scan --sync-mode both

# Rebuild graph and project topology only
dev sync code --full-scan --sync-mode graph

# Rebuild embeddings only
dev sync code --full-scan --sync-mode embedding

# Graph-only full scan for every configured source root
dev sync code --full-scan --sync-mode graph all
```

The specialized `graph` and `embedding` modes require `--full-scan`. They do
not replace the shared incremental baseline; use the default `both` mode for
normal incremental synchronization. If embedding fails in `both` mode after
topology succeeds, the graph/topology result remains persisted and the command
returns a non-zero status with separate phase results.

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
