# Phase 02 — Remote Connectivity Probe & Provisioning

## Objective

Thêm logic provision resources trên remote servers khi `infra-up --provision`
được gọi. Provisioning tạo collections (Qdrant) và graphs + indexes (FalkorDB)
nếu chưa tồn tại. Không re-ingest data — chỉ đảm bảo schema/container sẵn
sàng.

## Scope

- `scripts/mcp-lifecycle.py`: `_provision_remote_project()`
- `cortex_harness/storage/remote_probe.py`: thêm provision helpers

## Design Decisions

### Provisioning Scope

Chỉ tạo **containers** (collections, graphs) — không tạo data.
Data được tạo bởi ingest scripts (`graphrag_ingest_langextract.py`,
analyzer scripts). Provisioning đảm bảo:

1. **Qdrant**: collection tồn tại với đúng vector config.
2. **FalkorDB**: graph tồn tại, constraints/indexes đã setup.

### Collection/Graph Naming

Dùng convention hiện tại:
- Qdrant code collection: `{project_id}_code` hoặc từ project config
- Qdrant doc collection: `{project_id}_doc` hoặc từ project config
- FalkorDB code graph: `hyper_graph` (default) hoặc từ project config
- FalkorDB doc graph: `{project_id}_doc_graph` hoặc từ project config

Tên cụ thể đọc từ project config `code.env` và `doc.env` sections.

## Implementation

### 2.1 Provision Helper Functions

Trong `cortex_harness/storage/remote_probe.py`:

```python
@dataclass(frozen=True)
class ProvisionResult:
    """Outcome of provisioning a single resource."""
    resource: str       # e.g. "qdrant:my_project_code"
    action: str         # "created", "exists", "failed"
    message: str
    cause: Optional[BaseException] = None


def provision_qdrant_collection(
    config: RemoteStorageConfig,
    collection_name: str,
    *,
    vector_size: int = 384,
    distance: str = "COSINE",
) -> ProvisionResult:
    """Create a Qdrant collection on the remote server if it doesn't exist."""
    if not config.qdrant_url:
        return ProvisionResult(
            f"qdrant:{collection_name}", "skipped", "no qdrant_url configured"
        )
    try:
        from .qdrant_remote import get_remote_client
        from qdrant_client.http import models as qmodels

        client = get_remote_client(config.qdrant_url, api_key=config.qdrant_api_key)
        distance_enum = getattr(qmodels.Distance, distance.upper())

        if client.collection_exists(collection_name=collection_name):
            return ProvisionResult(
                f"qdrant:{collection_name}", "exists",
                f"collection '{collection_name}' already exists"
            )

        client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=vector_size, distance=distance_enum
            ),
        )
        return ProvisionResult(
            f"qdrant:{collection_name}", "created",
            f"collection '{collection_name}' created (dim={vector_size})"
        )
    except Exception as exc:
        return ProvisionResult(
            f"qdrant:{collection_name}", "failed", str(exc), cause=exc
        )


def provision_falkordb_graph(
    config: RemoteStorageConfig,
    graph_name: str,
) -> ProvisionResult:
    """Ensure a FalkorDB graph exists and is queryable on the remote server."""
    if not config.falkordb_uri:
        return ProvisionResult(
            f"falkordb:{graph_name}", "skipped", "no falkordb_uri configured"
        )
    try:
        from tools.graph.driver.falkordb_driver import FalkorDBDriver

        driver = FalkorDBDriver(
            uri=config.falkordb_uri,
            password=config.falkordb_password,
            ssl=config.falkordb_ssl,
            graph=graph_name,
            _suppress_deprecation=True,
        )
        # FalkorDB auto-creates graphs on first query
        driver.execute_query("RETURN 1 AS ok")
        return ProvisionResult(
            f"falkordb:{graph_name}", "exists",
            f"graph '{graph_name}' is accessible"
        )
    except Exception as exc:
        return ProvisionResult(
            f"falkordb:{graph_name}", "failed", str(exc), cause=exc
        )
```

### 2.2 Provision Orchestrator

Trong `scripts/mcp-lifecycle.py`:

```python
def _provision_remote_project(
    project_id: str,
    remote_config: RemoteStorageConfig,
) -> None:
    """Provision remote resources for one project."""
    from cortex_harness.storage.remote_probe import (
        provision_qdrant_collection,
        provision_falkordb_graph,
    )

    # Derive collection/graph names from project config or conventions.
    # These match what ingest scripts expect.
    code_collection = f"{project_id}_code"
    doc_collection = f"{project_id}_doc"
    code_graph = "hyper_graph"
    doc_graph = f"{project_id}_doc"

    results = []

    # Qdrant provisioning
    if remote_config.qdrant_url:
        results.append(provision_qdrant_collection(
            remote_config, code_collection
        ))
        results.append(provision_qdrant_collection(
            remote_config, doc_collection
        ))

    # FalkorDB provisioning
    if remote_config.falkordb_uri:
        results.append(provision_falkordb_graph(
            remote_config, code_graph
        ))
        results.append(provision_falkordb_graph(
            remote_config, doc_graph
        ))

    for result in results:
        tag_map = {"created": "[new]", "exists": "[ok]", "skipped": "[skip]", "failed": "[fail]"}
        tag = tag_map.get(result.action, "[?]")
        print(f"[infra-up]     {tag} {result.resource}: {result.message}")
```

### 2.3 Collection Name Resolution

Project config có thể override collection/graph names qua `code.env` và
`doc.env`. Đọc từ project config:

```python
def _resolve_collection_names(
    project_id: str,
    config_path: str,
) -> dict[str, str]:
    """Read collection/graph names from project config env sections."""
    try:
        document = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        document = {}

    code_env = document.get("code", {}).get("env", {})
    doc_env = document.get("doc", {}).get("env", {})

    return {
        "code_collection": code_env.get("QDRANT_COLLECTION")
                           or code_env.get("QDRANT_COLLECTION_CODE")
                           or f"{project_id}_code",
        "doc_collection": doc_env.get("QDRANT_COLLECTION_DOC")
                         or f"{project_id}_doc",
        "code_graph": code_env.get("FALKORDB_GRAPH") or "hyper_graph",
        "doc_graph": doc_env.get("DOC_FALKORDB_GRAPH")
                    or doc_env.get("FALKORDB_GRAPH")
                    or f"{project_id}_doc",
    }
```

### 2.4 FalkorDB Schema Setup (Optional)

Sau khi graph tồn tại, optionally chạy `setup_constraints.py` để tạo
indexes/constraints trên remote FalkorDB. Đây là optional vì:
- Một số projects đã có schema setup riêng.
- `setup_constraints.py` đã support `--graph-provider falkordb`.

```python
def _setup_remote_schema(
    project_id: str,
    remote_config: RemoteStorageConfig,
    graph_name: str,
) -> ProvisionResult:
    """Run schema setup against remote FalkorDB (idempotent)."""
    if not remote_config.falkordb_uri:
        return ProvisionResult(
            f"falkordb:{graph_name}:schema", "skipped", "no falkordb_uri"
        )
    try:
        # Delegate to existing setup_constraints.py logic
        # via subprocess to avoid import coupling
        result = subprocess.run(
            [
                str(venv_python()),
                "code-tiny/scripts/setup_constraints.py",
                "--graph-provider", "falkordb",
                "--falkordb-uri", remote_config.falkordb_uri,
                "--falkordb-graph", graph_name,
            ]
            + (["--falkordb-password", remote_config.falkordb_password]
               if remote_config.falkordb_password else []),
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return ProvisionResult(
                f"falkordb:{graph_name}:schema", "created",
                "constraints and indexes ensured"
            )
        return ProvisionResult(
            f"falkordb:{graph_name}:schema", "failed",
            result.stderr.strip() or f"exit code {result.returncode}"
        )
    except Exception as exc:
        return ProvisionResult(
            f"falkordb:{graph_name}:schema", "failed", str(exc), cause=exc
        )
```

## Acceptance Criteria

- [ ] `provision_qdrant_collection` tạo collection nếu chưa tồn tại,
      skip nếu đã có
- [ ] `provision_falkordb_graph` verify graph accessible (auto-creates)
- [ ] `_provision_remote_project` orchestrate cả Qdrant + FalkorDB
- [ ] Collection/graph names đọc từ project config env sections
- [ ] Schema setup delegates to `setup_constraints.py`
- [ ] Provisioning output rõ ràng: [new], [ok], [skip], [fail] per resource
