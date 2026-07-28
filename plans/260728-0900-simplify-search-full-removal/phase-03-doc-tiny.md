# Phase 03 — doc-tiny: project_contract.py + mcp_graph_rag.py

## Objective

Mirror the code-tiny changes in doc-tiny: remove `search_full` from
helper signatures, remove `ProjectScopeRequiredError`, and simplify the
MCP tools.

## Files

- `doc-tiny/project_contract.py`
- `doc-tiny/mcp_graph_rag.py`
- `doc-tiny/tests/test_project_contract.py`

## Changes

### 1. `project_contract.py` — Remove `ProjectScopeRequiredError`

**Delete** the `ProjectScopeRequiredError` class entirely.

Update the module docstring: remove references to `search_full` and
`ProjectScopeRequiredError`.

### 2. `project_contract.py` — `qdrant_project_filter`

**Signature change**:

```python
# BEFORE
def qdrant_project_filter(
    project_id: Any, search_full: bool = False
) -> Optional[Dict[str, Any]]:

# AFTER
def qdrant_project_filter(
    project_id: Any
) -> Optional[Dict[str, Any]]:
```

Remove the `if search_full: return None` early exit. The function
returns `None` when `project_id` is falsy.

### 3. `mcp_graph_rag.py` — `semantic_search`

Remove `search_full` from the function signature:

```python
# BEFORE
def semantic_search(
    query,
    top_k=5,
    source_id=None,
    collection=None,
    project_id=None,
    search_full=False,
    ...
):

# AFTER
def semantic_search(
    query,
    top_k=5,
    source_id=None,
    collection=None,
    project_id=None,
    ...
):
```

Remove the guard:

```python
# DELETE:
if collection is None and not project_id and not search_full:
    raise ProjectScopeRequiredError()
```

Remove `search_full` from the `qdrant_search_entity_payload` call:

```python
# BEFORE
payloads = qdrant_search_entity_payload(
    ...
    search_full=search_full,
)

# AFTER
payloads = qdrant_search_entity_payload(
    ...
)
```

Update the docstring: remove mentions of `search_full`.

### 4. `mcp_graph_rag.py` — `query_graph_rag_langextract`

Same pattern: remove `search_full` from signature, from the
`qdrant_search_entity_payload` call, and from docstrings.

### 5. `mcp_graph_rag.py` — Remove `ProjectScopeRequiredError` import

```python
# DELETE:
from project_contract import ProjectScopeRequiredError
```

### 6. Check `qdrant_search_entity_payload`

If `qdrant_search_entity_payload` itself accepts `search_full`, remove
it and update the internal `qdrant_project_filter` call to use the
single-argument form.

## Verification

- `grep -rn "search_full" doc-tiny/` → 0 matches (excluding plan files).
- `grep -rn "ProjectScopeRequiredError" doc-tiny/` → 0 matches.
- `python -c "from project_contract import qdrant_project_filter; print(qdrant_project_filter('x'))"` — returns filter dict.
- `python -c "from project_contract import qdrant_project_filter; print(qdrant_project_filter(None))"` — returns `None`.
