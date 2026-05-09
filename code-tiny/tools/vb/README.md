# VB Family Analyzer Guide (VB.NET, VB6, VBA, VBScript)

This document describes how to set up the environment and run the VB analyzer group within `hyper-graph`.

---

## 1) Scope

The scripts include:

* `tools/vb/vbnet_analyzer.py`
* `tools/vb/vb6_analyzer.py`
* `tools/vb/vba_analyzer.py`
* `tools/vb/vbscript_analyzer.py`

The pipeline follows the repository's standard workflow:

1. **Incremental cleanup** (Neo4j/Qdrant)
2. **Source parsing**
3. **Call graph resolution**
4. **Neo4j writing**
5. **Embedding + Qdrant upsert**
6. **Message scan**

---

## 2) Environment Requirements

### Mandatory

* **Python 3.11+** (3.12 recommended)
* **Neo4j**
* **Qdrant** (required for semantic vector search)

### For VB.NET Roslyn Engine

* **.NET SDK** (9.x recommended; 8.x is still supported)
* **Worker target:** `net8.0;net9.0`
* The analyzer will automatically build the worker when running VB.NET in `auto|roslyn` mode.

Quick check:

```bash
dotnet --info
dotnet --list-runtimes

```

---

## 3) Installation

From the `hyper-dev/hyper-graph` directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

```

The `requirements.txt` file already includes grammar packages for:

* VB.NET tree-sitter
* VB6 tree-sitter
* VBA tree-sitter
* VBScript tree-sitter

---

## 4) Recommended Environment Variables

```bash
export ROOT=/path/to/source
export PROJECT_ID=my_project
export PROJECT_NAME=my_project

export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASS=your_password
export NEO4J_DB=neo4j

export QDRANT_URL=http://localhost:6333
export QDRANT_COLLECTION=my_project
export CODE_EMBEDDING_MODEL=jinaai/jina-embeddings-v3
export EMBED_DEVICE=cpu

```

---

## 5) Running VB Analyzers

### 5.1 Dry-run

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py --root "$ROOT" --dry-run
.venv/bin/python tools/vb/vb6_analyzer.py --root "$ROOT" --dry-run
.venv/bin/python tools/vb/vba_analyzer.py --root "$ROOT" --dry-run
.venv/bin/python tools/vb/vbscript_analyzer.py --root "$ROOT" --dry-run

```

### 5.2 VB.NET Default (Roslyn AUTO)

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py \
  --root "$ROOT" \
  --project-id "$PROJECT_ID" \
  --project-name "$PROJECT_NAME" \
  --neo4j-uri "$NEO4J_URI" \
  --neo4j-user "$NEO4J_USER" \
  --neo4j-password "$NEO4J_PASS" \
  --neo4j-db "$NEO4J_DB" \
  --qdrant-url "$QDRANT_URL" \
  --qdrant-collection "$QDRANT_COLLECTION" \
  --vbnet-parser-engine auto \
  --vbnet-semantic auto \
  --verbose

```

### 5.3 Force Roslyn Syntax-only

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py \
  --root "$ROOT" \
  --vbnet-parser-engine roslyn \
  --vbnet-semantic off \
  --disable-message-scan \
  --verbose

```

### 5.4 Force Regex Fallback (No Roslyn)

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py \
  --root "$ROOT" \
  --vbnet-parser-engine regex \
  --disable-message-scan \
  --verbose

```

### 5.5 Incremental via Owner Manifest

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py \
  --root "$ROOT" \
  --project-id "$PROJECT_ID" \
  --project-name "$PROJECT_NAME" \
  --incremental \
  --changed-files-manifest .cache/owner_manifests/$PROJECT_ID/vbnet_changed_owner.json \
  --deleted-files-manifest .cache/owner_manifests/$PROJECT_ID/vbnet_deleted_owner.json \
  --vbnet-parser-engine auto \
  --vbnet-semantic auto \
  --verbose

```

---

## 6) Important VB.NET Flags

* `--vbnet-parser-engine auto|roslyn|regex`
* Default: `auto` (prioritizes Roslyn, fallbacks to regex if it fails).


* `--vbnet-semantic auto|on|off`
* Default: `auto`:
* If `.sln/.vbproj` exists => semantic path.
* No workspace => syntax-only.




* `--vbnet-roslyn-worker-project <path.csproj>`: Overrides the worker path.
* `--vbnet-roslyn-timeout-sec <float>`
* `--vbnet-roslyn-workspace-timeout-ms <int>`
* `--vbnet-roslyn-file-timeout-ms <int>`

---

## 7) Key Log Meanings

* `[cleanup][qdrant] files=...`: Deleting old vectors for changed/deleted files before new ingestion.
* `[parse][start] parser=vbnet files=N ...`: Beginning the parse phase; `N` is the number of files in the current batch.
* `[parse][engine] parser=vbnet engine=... semantic=...`: The actual engine used for VB.NET (Roslyn/regex).
* `[parse][progress] parser=vbnet completed=x/N ...`: Progress of file parsing.
* `[parse][fallback] ...`: Roslyn failure (batch or file level); switching to regex to avoid blocking the job.
* `[parse][done] parser=vbnet parsed=N/N`: Parse phase complete.
* `[vb] calls: total/resolved`: Ratio of resolved calls after parsing.
* `[SCAN_RESULT] parser=vbnet files=... functions=... classes=...`: Parser summary.

---

## 8) Quick Troubleshooting

### VB.NET is unusually slow

1. Enable `--verbose` and check for high frequency of `[parse][fallback]`.
2. If many fallbacks occur, check:
* `dotnet --list-runtimes`
* `dotnet build tools/vb/roslyn_worker/RoslynVbWorker.csproj -c Release`


3. To debug the parser alone (without writing to DB/vector):
* Omit `--neo4j-*`
* Omit `--qdrant-url`
* Add `--disable-message-scan`



### Roslyn cannot be used on the current machine

* Temporarily run using: `--vbnet-parser-engine regex`

---

## 9) Compatibility Notes

* `parse_meta` for VB.NET now includes additional fields:
* `parser_engine`, `semantic_mode`, `semantic_enabled`
* `fallback_reason`, `worker_elapsed_ms`
* `workspace_kind`, `solution_or_project_path`
* `semantic_errors`, `requested_engine`


* `PARSE_CACHE_VERSION` has been bumped to prevent old cache contract conflicts.