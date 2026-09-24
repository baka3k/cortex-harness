# CortexHarness

**Give AI agents full understanding of your codebase — not just fragments.**

CortexHarness extracts real structure from your source code and documentation, stores it in a hybrid Graph + Vector engine, and serves it to AI agents through MCP. The result: agents that reason over your actual architecture instead of guessing from incomplete context.

---

## The Problem It Solves

AI coding agents hallucinate when context is missing. They see a file but not the call graph. They read a function but not the framework convention. They answer questions but can't trace impact.

CortexHarness fixes this by building a **persistent, queryable knowledge layer** that gives agents:

- **Structural truth** — call graphs, dependencies, type hierarchies, framework routes (not just text)
- **Semantic understanding** — embeddings for similarity search across code and docs
- **Complete context** — one query returns the function, its callers, its framework role, and related documentation
- **Less token waste** — agents ask the graph instead of stuffing entire files into prompts

## How It Works

```
 Your Codebase (20+ languages)        Your Documentation (PDF, DOCX, MD, PPTX)
          │                                      │
          ▼                                      ▼
 ┌────────────────────┐               ┌────────────────────┐
 │  Language-Specific │               │  GraphRAG Pipeline │
 │  Analyzers         │               │  Entity Extraction │
 │  Extract: calls,   │               │  Extract: entities,│
 │  types, flows,     │               │  relationships,    │
 │  framework roles   │               │  summaries         │
 └────────┬───────────┘               └────────┬───────────┘
          │                                      │
          ▼                                      ▼
 ┌─────────────────────────────────────────────────────────┐
 │           Hybrid Storage (Embedded or Remote)           │
 │                                                         │
 │   Graph Providers             Qdrant                    │
 │   ───────────────             ─────────────────────     │
 │   • FalkorDB (default)        • Code embeddings         │
 │   • LadybugDB (Kuzu-based)    • Doc embeddings          │
 │   • Neo4j (optional)          • Entity-linked paragraphs│
 │   • Call graphs, dependencies,• Semantic similarity     │
 │     type hierarchies, flows   • RAG-ready retrieval     │
 │                                                         │
 └────────────────────────┬────────────────────────────────┘
                          │
                          ▼
 ┌─────────────────────────────────────────────────────────┐
 │         Unified MCP Server — 30+ Query Tools            │
 │                                                         │
 │   "What calls this function?"        → Graph traversal  │
 │   "Find similar implementations"     → Vector search    │
 │   "Trace this API end-to-end"        → Fullstack chain  │
 │   "What breaks if I change this?"    → Impact analysis  │
 │   "Summarize this module"            → Graph + vectors  │
 └────────────────────────┬────────────────────────────────┘
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
      Claude Code    Qwen Code    Cursor / Copilot
```

---

## Why Developers Choose CortexHarness

### Zero-Config Start

Embedded FalkorDB + Qdrant = **no Docker, no daemon, no infrastructure**. Clone, `make install`, `dev sync code` — done. Switch to remote (Docker or cloud) when your team needs shared storage.

```bash
make install          # one command: venv + deps + global CLI
dev init              # interactive wizard
dev sync code         # graph + embeddings in one pass
dev start             # MCP servers ready for your AI agent
```

### Accurate, Not Just Fast

- **Graph-first pipeline**: topology and relationships are written before embeddings. Agents can query structure immediately.
- **Incremental sync**: Git-aware change detection → SHA-256 content verification → only reprocess what changed. A 500K-line repo syncs in seconds after a one-file commit.
- **Framework-aware**: Spring controllers, ASP.NET routes, Struts actions, MyBatis mappers — extracted as first-class graph nodes, not buried in raw text.
- **Crash-safe ingestion**: write-ahead journal for graph mutations. If sync fails mid-write, recovery replays exactly what was interrupted.

### Less Context, More Signal

AI agents have finite context windows. CortexHarness helps them use it wisely:

| Without CortexHarness | With CortexHarness |
| --- | --- |
| Agent reads 10 files to find callers | `query_subgraph` returns the call tree in one tool call |
| Agent guesses the framework pattern | `get_framework_context` returns the exact overlay |
| Agent stuffs entire modules into prompt | `semantic_search` returns the 5 relevant paragraphs |
| Agent hallucinates the dependency chain | `trace_flow` returns the verified path |

### End-to-End Workflow

Not just ingestion — a complete pipeline from source to agent query:

1. **Ingest** — `dev sync code` / `dev sync doc` extracts structure and semantics; doc sync also generates dynamic YAKE entity rules per document before GLiNER extraction
2. **Store** — graph relationships + vector embeddings persisted to disk
3. **Serve** — MCP servers expose 30+ query tools on localhost
4. **Query** — AI agents call MCP tools instead of reading raw files
5. **Update** — next sync only processes changes, graph stays current

### ADLC — Skills That Teach Agents How to Work

The ADLC skill pack (`make install-adlc`) installs 12+ battle-tested skills into your AI agent:

- **Plan before code** — architecture-first planning, not dive-and-pray
- **Debug systematically** — root cause analysis with evidence, not guess-and-check
- **Predict risks** — 5-persona panel reviews changes before implementation
- **Generate edge cases** — 12-dimension scenario coverage
- **Audit security** — STRIDE + OWASP with automated fix suggestions

Works with: Claude Code, Qwen Code, OpenCode, GitHub Copilot, Cursor, Continue.

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (fast Python package manager)
- Node.js ≥ 18 (only for ADLC skill pack)

### Install & Sync

```bash
git clone https://github.com/baka3k/cortex-harness.git
cd cortex-harness

make install          # create .venv, install deps, register global `dev` CLI
make storage-init     # initialize persistent storage at ~/.cortext-harness/v1/
make doctor           # verify Qdrant + FalkorDB connectivity

dev init              # interactive wizard — configure project, backend, folders
dev sync code         # ingest source code → graph + embeddings
dev sync doc          # ingest documentation → knowledge graph + vectors
dev start             # MCP servers: code (:8788) + doc (:8789)
```

Point your AI agent at `http://localhost:8788` (code) or `http://localhost:8789` (doc). The agent gets full graph + vector query access through standard MCP protocol.

---

## Supported Languages

Compiler-grade parsers — not regex, not naive AST. Each analyzer uses the right tool for the language:

| Language | Parser Technology | What It Extracts |
| --- | --- | --- |
| **C# / VB.NET** | Roslyn workspace (semantic model) | Full type resolution, method invocations, LINQ, async/await, ASP.NET overlays |
| **VB6 / VBA** | ANTLR4 (whole-program parse) | Call graphs, control flow, designer forms, control arrays |
| **Java / Kotlin** | Tree-sitter + Spring/Struts/MyBatis overlays | Calls, generics, inheritance, framework routes, DI wiring |
| **C / C++** | Clang-based | Pointers, templates, macros, header resolution |
| **Go, Rust, Swift** | Tree-sitter | Calls, types, interfaces, concurrency patterns |
| **TypeScript / JavaScript** | Tree-sitter | Imports, exports, async, React/Vue components |
| **Python** | AST + import graph | Decorators, generators, type hints |
| **COBOL** | Custom parser (copybook-aware) | Paragraphs, COPY resolution, file I/O |
| **Perl, Delphi, Dart** | Tree-sitter / custom | Module structure, calls, inheritance |
| **Android** | Mixed Java + Kotlin analyzer | Cross-language calls, Activity lifecycle |

**Framework overlays**: Spring, Struts, MyBatis, Flutter, ASP.NET Core, ASP.NET Framework, Express.js, FastAPI/Django, Laravel

**Document formats**: PDF, Markdown, DOCX, PPTX, XLSX, TXT

---

## Storage

**Embedded by default** — data lives on disk, no network required.

```text
~/.cortext-harness/v1/instances/default/
├── manifest.json
├── qdrant/{code,doc}/              # vector embeddings
├── falkordb/{code,doc}/data.rdb    # graph relationships
└── backups/
```

**Remote mode** for shared team infrastructure:

```bash
make infra-up     # start Docker: Qdrant (:6333) + FalkorDB (:6379)
dev init          # select "remote" backend, enter connection details
```

Multiple projects share one storage instance with isolated graph/collection namespaces. Override paths with `CORTEX_DATA_HOME` or `CORTEX_STORAGE_INSTANCE`.

---

## CLI Reference

### Lifecycle

| Command | Description |
| --- | --- |
| `dev build` | Create/reuse `.venv`, install dependencies |
| `dev doctor` | Connectivity probes + MCP port diagnostics |
| `dev start` | Launch MCP servers (code + doc) |
| `dev start --server code --name shop --port 8790` | Named instance with custom port |
| `dev stop` | Stop all MCP servers |
| `dev stop --name shop` | Stop one named instance |

### Sync

| Command | Description |
| --- | --- |
| `dev sync code` | Incremental code sync (graph + embeddings) |
| `dev sync code all` | Full sync across all configured source roots |
| `dev sync code --sync-mode graph --full-scan` | Graph-only rebuild |
| `dev sync code --sync-mode embedding --full-scan` | Embedding-only rebuild |
| `dev sync doc` | Incremental doc sync (GraphRAG + vectors) |
| `dev sync doc all` | Full doc sync across all configured doc roots |

### Configuration & Maintenance

| Command | Description |
| --- | --- |
| `dev init` | Interactive setup wizard |
| `dev status` | Show active config |
| `dev ignore add <FOLDER>...` | Add folders to scan-ignore list |
| `dev install-adlc` | Bootstrap ADLC skill pack for AI agents |
| `make export-db OUTPUT=project.cortexdb` | Export project database |
| `make import-db ARCHIVE=project.cortexdb` | Import project database |

---

## MCP Tools for AI Agents

One server, every query pattern:

| Category | Key Tools |
| --- | --- |
| **Symbol & graph** | `search_functions`, `get_symbol`, `query_subgraph`, `explore_graph` |
| **Flow tracing** | `trace_flow`, `reconstruct_flow`, `find_workflows_containing` |
| **Impact analysis** | `analyze_workflow_impact`, `find_paths`, `compute_scc` |
| **Fullstack bridge** | `get_api_call_chain`, `find_callers_of_endpoint` |
| **Semantic search** | `semantic_search`, `search_by_code` |
| **Project context** | `get_project_modules`, `get_framework_context`, `get_public_apis` |
| **Dependency planning** | `topological_sort`, `plan_dependency_order` |

---

## Architecture

```
cortex-harness/
├── cortex_harness/     # CLI (dev.py), config, storage management
├── code-tiny/          # Language analyzers, MCP servers, graph writers
│   ├── tools/          # Per-language analyzers (csharp, java, go, rust, ...)
│   ├── mcp/            # Unified MCP server + per-language backends
│   └── tools/graph/    # Shared graph schema, writers, CLI helpers
├── doc-tiny/           # Document pipeline, GraphRAG, entity extraction
└── scripts/            # Lifecycle, MCP management, migrations
```

**Design principles:**
- **Embedded-first** — zero infrastructure for individual developers
- **Provider-neutral** — swap FalkorDB ↔ Neo4j without touching analyzers
- **Project-scoped** — multiple projects, one storage, isolated namespaces
- **Incremental by default** — Git-aware, SHA-256 verified, only changed files
- **Crash-safe** — write-ahead journal for graph mutations with automatic replay

---

## Troubleshooting

<details>
<summary>Windows: <code>ModuleNotFoundError: No module named 'requests'</code></summary>

```powershell
uv pip install --python .venv\Scripts\python.exe --requirements code-tiny\requirements.txt
```
</details>

<details>
<summary>Windows: <code>'dev' is not recognized as a command</code></summary>

Run directly: `.venv\Scripts\dev.exe <command>`, or add `%USERPROFILE%\.local\bin` to PATH.
</details>

<details>
<summary>CUDA / PyTorch issues</summary>

```bash
uv pip install torch torchvision torchaudio --default-index https://download.pytorch.org/whl/cu128
python -c "import torch; print(torch.cuda.is_available())"
```
</details>

<details>
<summary>Port already in use</summary>

`dev start --port 8790` to pick a different port, or `dev stop` to clear existing servers.
</details>

---

## License

See [LICENSE](LICENSE) for details.
