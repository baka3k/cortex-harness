# Phase 01 — Core Helpers: project_scope.py + project_registry.py

## Objective

Remove `search_full` from the three helper functions in `project_scope.py`
and remove `ProjectScopeRequiredError` from `project_registry.py`. These
are the foundation — every other change depends on these helpers being
clean first.

## Files

- `code-tiny/tools/common/project_scope.py`
- `code-tiny/tools/common/project_registry.py`

## Changes

### 1. `project_scope.py` — `prepare_project_scope_parameters`

**Remove** the `search_full` injection block:

```python
# DELETE these lines:
if "search_full" not in prepared:
    prepared["search_full"] = False
```

The function still adds `*_normalized` variants for recognized
project-scope keys — that behavior is unchanged. It simply stops
injecting a dead `search_full` parameter.

Update the docstring: remove all references to `$search_full` and the
predicate `AND ($search_full OR ...)`.

### 2. `project_scope.py` — `qdrant_project_filter`

**Signature change**: drop `search_full` parameter.

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

**Body**: remove the `if search_full: return None` early exit. The
function now returns `None` only when `project_id` is falsy (which is
already handled by `project_id_lookup_key` returning `None`).

### 3. `project_scope.py` — `matches_project_scope`

**Signature change**: drop `search_full` parameter.

```python
# BEFORE
def matches_project_scope(
    candidate: Mapping[str, Any],
    project_id: Any,
    search_full: bool = False,
) -> bool:

# AFTER
def matches_project_scope(
    candidate: Mapping[str, Any],
    project_id: Any,
) -> bool:
```

**Body**: remove the `if search_full: return True` early exit. The
function returns `True` when `project_id` is `None` (already handled).

### 4. `project_registry.py` — Remove `ProjectScopeRequiredError`

**Delete** the `ProjectScopeRequiredError` class entirely (lines ~70-90).

Update all internal references:
- `_resolve_graph_database` in `unified_mcp.py` (Phase 02) is the only
  consumer — it will be rewritten in Phase 02.

**Do NOT delete** `ProjectNotRegisteredError` — that stays. It is raised
when a specific `project_id` is given but not found in the registry.

### 5. `project_registry.py` — Update docstrings

Remove all mentions of `search_full` from module docstring and class
docstrings.

## Verification

- `python -c "from tools.common.project_scope import prepare_project_scope_parameters; print(prepare_project_scope_parameters('MATCH (n)', {'project_id': 'x'}))"` — should NOT contain `search_full` key.
- `python -c "from tools.common.project_scope import qdrant_project_filter; print(qdrant_project_filter('x'))"` — should return filter dict.
- `python -c "from tools.common.project_scope import qdrant_project_filter; print(qdrant_project_filter(None))"` — should return `None`.
- `grep -rn "search_full" code-tiny/tools/common/` → 0 matches.
- `grep -rn "ProjectScopeRequiredError" code-tiny/tools/common/` → 0 matches.
