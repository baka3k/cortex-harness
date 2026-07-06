
---

# Database Integration Guide

This document describes how to configure multiple database types for Cortex Harness, set up the environment, and run sample scan commands using Neo4j or FalkorDB.

## Database Architecture

Cortex Harness uses two main database groups:

| Group | Database | Role | Provider flag |
| --- | --- | --- | --- |
| Graph store | Neo4j | Stores code graphs, document graphs, relationships, workflows, and message edges | `--graph-provider neo4j` |
| Graph store | FalkorDB | Stores graphs similar to Neo4j via the Redis/FalkorDB protocol | `--graph-provider falkordb` |
| Vector store | Qdrant | Stores embeddings for code/doc/message searches | Does not use `--graph-provider`; configured via `QDRANT_*` variables |

`code-tiny/tools` uses a shared helper `tools.graph.cli`:

* `CODE_GRAPH_PROVIDER` or `GRAPH_PROVIDER`
* `--graph-provider neo4j|falkordb`
* `--falkordb-*` flags when FalkorDB is selected
* `--neo4j-*` flags are retained as compatibility aliases for legacy commands

`doc-tiny` uses its own adapter `doc-tiny/graph_store.py`:

* `DOC_GRAPH_PROVIDER` or `GRAPH_PROVIDER`
* `--graph-provider neo4j|falkordb`

## Python Environment Setup

Run the following commands from the repository root:

```powershell
cd C:\ai\cortex-harness
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

```

If the repository already has a `.venv`, simply activate it:

```powershell
cd C:\ai\cortex-harness
.\.venv\Scripts\Activate.ps1

```

Verify key dependencies:

```powershell
python -c "import neo4j, falkordb, qdrant_client; print('database deps ok')"

```

## Running Databases Locally

### Neo4j

Docker example:

```powershell
docker run --name cortex-neo4j `
  -p 7474:7474 -p 7687:7687 `
  -e NEO4J_AUTH=neo4j/password `
  neo4j:5

```

Environment variables:

```powershell
$env:GRAPH_PROVIDER="neo4j"
$env:CODE_GRAPH_PROVIDER="neo4j"
$env:DOC_GRAPH_PROVIDER="neo4j"
$env:NEO4J_URI="bolt://localhost:7687"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASS="password"
$env:NEO4J_DB="neo4j"

```

### FalkorDB

Docker example:

```powershell
docker run --name cortex-falkordb `
  -p 6379:6379 `
  falkordb/falkordb

```

Environment variables:

```powershell
$env:GRAPH_PROVIDER="falkordb"
$env:CODE_GRAPH_PROVIDER="falkordb"
$env:DOC_GRAPH_PROVIDER="falkordb"
$env:FALKORDB_HOST="localhost"
$env:FALKORDB_PORT="6379"
$env:FALKORDB_GRAPH="neo4j"
$env:FALKORDB_PASSWORD=""

```

If using a URI:

```powershell
$env:FALKORDB_URI="redis://localhost:6379"
$env:FALKORDB_GRAPH="neo4j"

```

### Qdrant

Qdrant operates independently of the graph provider and is shared by both Neo4j and FalkorDB.

```powershell
docker run --name cortex-qdrant `
  -p 6333:6333 `
  qdrant/qdrant

```

Environment variables:

```powershell
$env:QDRANT_URL="http://localhost:6333"
$env:QDRANT_COLLECTION="code_functions"
$env:MESSAGE_QDRANT_COLLECTION="code_messages"

```

## Scanning Code with Neo4j

Example of scanning a Python project:

```powershell
cd C:\ai\cortex-harness
.\.venv\Scripts\Activate.ps1

python code-tiny\tools\python\python_analyzer.py `
  --root C:\path\to\project `
  --graph-provider neo4j `
  --neo4j-uri bolt://localhost:7687 `
  --neo4j-user neo4j `
  --neo4j-password password `
  --neo4j-db neo4j `
  --qdrant-url http://localhost:6333 `
  --qdrant-collection my_project_functions `
  --project-id my_project `
  --project-name "My Project" `
  --repo my_project `
  --verbose

```

Example of scanning messages separately:

```powershell
python code-tiny\tools\sync\message_scan.py `
  --root C:\path\to\project `
  --graph-provider neo4j `
  --neo4j-uri bolt://localhost:7687 `
  --neo4j-user neo4j `
  --neo4j-password password `
  --neo4j-db neo4j `
  --qdrant-url http://localhost:6333 `
  --parsers python,ts,java `
  --project-id my_project `
  --project-name "My Project" `
  --repo my_project `
  --verbose

```

## Scanning Code with FalkorDB

Example of scanning a Python project:

```powershell
cd C:\ai\cortex-harness
.\.venv\Scripts\Activate.ps1

python code-tiny\tools\python\python_analyzer.py `
  --root C:\path\to\project `
  --graph-provider falkordb `
  --falkordb-host localhost `
  --falkordb-port 6379 `
  --falkordb-graph my_project_graph `
  --qdrant-url http://localhost:6333 `
  --qdrant-collection my_project_functions `
  --project-id my_project `
  --project-name "My Project" `
  --repo my_project `
  --verbose

```

Example using a URI:

```powershell
python code-tiny\tools\python\python_analyzer.py `
  --root C:\path\to\project `
  --graph-provider falkordb `
  --falkordb-uri redis://localhost:6379 `
  --falkordb-graph my_project_graph `
  --qdrant-url http://localhost:6333 `
  --project-id my_project `
  --project-name "My Project" `
  --repo my_project `
  --verbose

```

Example of scanning messages separately with FalkorDB:

```powershell
python code-tiny\tools\sync\message_scan.py `
  --root C:\path\to\project `
  --graph-provider falkordb `
  --falkordb-host localhost `
  --falkordb-port 6379 `
  --falkordb-graph my_project_graph `
  --qdrant-url http://localhost:6333 `
  --parsers python,ts,java `
  --project-id my_project `
  --project-name "My Project" `
  --repo my_project `
  --verbose

```

## Incremental Sync

Neo4j:

```powershell
python code-tiny\tools\sync\incremental_sync.py `
  --root C:\path\to\project `
  --graph-provider neo4j `
  --neo4j-uri bolt://localhost:7687 `
  --neo4j-user neo4j `
  --neo4j-password password `
  --neo4j-db neo4j `
  --qdrant-url http://localhost:6333 `
  --project-id my_project `
  --project-name "My Project" `
  --parsers auto `
  --allow-full-fallback `
  --verbose

```

FalkorDB:

```powershell
python code-tiny\tools\sync\incremental_sync.py `
  --root C:\path\to\project `
  --graph-provider falkordb `
  --falkordb-host localhost `
  --falkordb-port 6379 `
  --falkordb-graph my_project_graph `
  --qdrant-url http://localhost:6333 `
  --project-id my_project `
  --project-name "My Project" `
  --parsers auto `
  --allow-full-fallback `
  --verbose

```

`incremental_sync.py` forwards `CODE_GRAPH_PROVIDER` and `FALKORDB_*` variables to downstream child analyzers via the environment, so you do not need to repeat the provider flags in each subprocess.

## Document Scanning with doc-tiny

Neo4j:

```powershell
python doc-tiny\graphrag_ingest_langextract.py `
  --raw-text "OpenAI builds AI systems. Azure hosts cloud services." `
  --source-id sample_doc `
  --collection sample_docs `
  --graph-provider neo4j `
  --neo4j-uri bolt://localhost:7687 `
  --neo4j-user neo4j `
  --neo4j-pass password `
  --qdrant-url http://localhost:6333 `
  --entity-provider gliner `
  --graph-batch-size 50

```

FalkorDB:

```powershell
python doc-tiny\graphrag_ingest_langextract.py `
  --raw-text "OpenAI builds AI systems. Azure hosts cloud services." `
  --source-id sample_doc `
  --collection sample_docs `
  --graph-provider falkordb `
  --falkordb-host localhost `
  --falkordb-port 6379 `
  --falkordb-graph sample_docs_graph `
  --qdrant-url http://localhost:6333 `
  --entity-provider gliner `
  --graph-batch-size 50

```

Note that `--neo4j-batch-size` remains available as a legacy alias for `--graph-batch-size`.

## Provider and Environment Priority

`code-tiny/tools`:

1. CLI `--graph-provider`
2. `CODE_GRAPH_PROVIDER`
3. `GRAPH_PROVIDER`
4. Default: `neo4j`

`doc-tiny`:

1. CLI `--graph-provider`
2. `DOC_GRAPH_PROVIDER`
3. `GRAPH_PROVIDER`
4. Default: `neo4j`

## Compatibility Notes

* Neo4j specifies the database name via `--neo4j-db`.
* FalkorDB specifies the graph name via `--falkordb-graph`.
* During this transition phase, some internal variables are still named `neo4j_db`; when using FalkorDB, these are mapped to the graph name.
* Qdrant functions independently of Neo4j or FalkorDB; settings like `QDRANT_URL`, collection, timeout, and retries remain unchanged.
* Legacy documentation and examples may still refer to Neo4j, as Neo4j remains a fully supported provider.

## Quick Troubleshooting

Verify if the CLI detects the new provider:

```powershell
python code-tiny\tools\sync\message_scan.py --help | Select-String "graph-provider|falkordb"
python doc-tiny\graphrag_ingest_langextract.py --help | Select-String "graph-provider|falkordb|graph-batch"

```

If FalkorDB returns a "connection refused" error:

```powershell
docker ps
Test-NetConnection localhost -Port 6379

```

If Neo4j returns an "authentication failed" error:

```powershell
$env:NEO4J_URI="bolt://localhost:7687"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASS="password"

```

If the analyzer fails to write to the graph:

* Double-check the `--graph-provider` flag.
* For Neo4j, ensure `--neo4j-uri`, `--neo4j-user`, and `--neo4j-password` are all correctly provided.
* For FalkorDB, ensure the service is running at the specified `--falkordb-host/--falkordb-port` or `--falkordb-uri`.
* Enable the `--verbose` flag to inspect connection and batch write logs.