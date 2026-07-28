# Phase 04 — Tests + Metadata + Documentation

## Objective

Update all tests, tool metadata, and documentation to reflect the
simplified contract. Ensure zero remaining references to `search_full`.

## Files

### Tests

- `code-tiny/tools/common/test_project_scope_search_full.py` — **rename** to `test_project_scope.py` and rewrite
- `code-tiny/tools/common/test_project_registry.py` — update if it references `ProjectScopeRequiredError`
- `tests/test_unified_mcp_input_coercion.py` — update assertions
- `doc-tiny/tests/test_project_contract.py` — update `test_search_full_returns_none`

### Metadata

- `code-tiny/mcp/tool_metadata.py` — remove `search_full` from descriptions
- `code-tiny/mcp/fastmcp_server.py` — update comment at line ~172

### Other MCP servers (comments only)

- `code-tiny/mcp/cplus/cplus_mcp.py` — deprecation notice at ~1362
- `code-tiny/mcp/android/android_mcp.py` — deprecation notice at ~1334
- `code-tiny/mcp/java/java_mcp.py` — deprecation notice at ~1059
- `code-tiny/mcp/fastmcp_server.py` — deprecation notice at ~1265

## Changes

### 1. Rewrite test file

Rename `test_project_scope_search_full.py` → `test_project_scope.py`.

Rewrite all test classes to assert the new behavior:

**`QdrantFilterTests`**:
```python
class QdrantFilterTests(unittest.TestCase):
    def test_filters_by_project(self):
        filt = project_scope.qdrant_project_filter("cortext")
        self.assertEqual(filt, {"must": [{"key": "project_id_normalized",
                                           "match": {"value": "cortext"}}]})

    def test_no_project_id_returns_none(self):
        self.assertIsNone(project_scope.qdrant_project_filter(None))
        self.assertIsNone(project_scope.qdrant_project_filter(""))
```

**`MatchesProjectScopeTests`**:
```python
class MatchesProjectScopeTests(unittest.TestCase):
    def test_matching_project(self):
        candidate = {"project_id_normalized": "proja"}
        self.assertTrue(project_scope.matches_project_scope(candidate, "ProjA"))

    def test_non_matching_project(self):
        candidate = {"project_id_normalized": "projb"}
        self.assertFalse(project_scope.matches_project_scope(candidate, "ProjA"))

    def test_no_project_id_always_matches(self):
        candidate = {"project_id_normalized": "projb"}
        self.assertTrue(project_scope.matches_project_scope(candidate, None))
```

**`PrepareParametersTests`**:
```python
class PrepareParametersTests(unittest.TestCase):
    def test_adds_normalized_key(self):
        params = project_scope.prepare_project_scope_parameters(
            "MATCH (n)", {"project_id": "cortext"})
        self.assertEqual(params["project_id_normalized"], "cortext")

    def test_does_not_set_search_full(self):
        params = project_scope.prepare_project_scope_parameters(
            "MATCH (n)", {"project_id": "cortext"})
        self.assertNotIn("search_full", params)

    def test_empty_params(self):
        params = project_scope.prepare_project_scope_parameters("MATCH (n)", None)
        self.assertNotIn("search_full", params)
```

### 2. Update `test_project_registry.py`

Remove or update any test that imports or asserts
`ProjectScopeRequiredError`. If a test asserts that the error is raised,
change it to assert that resolution succeeds (returns a default graph).

### 3. Update `test_unified_mcp_input_coercion.py`

The existing tests at lines 252-309 test `_run_project_context_tool` with
`project_id`. These should still pass. If any test passes `search_full`
as an argument, remove it.

Add a test asserting that calling a project-context tool without
`project_id` and without `db` does NOT raise — it resolves to the env
default graph.

### 4. Update `test_project_contract.py` (doc-tiny)

Rename `test_search_full_returns_none` → `test_no_project_id_returns_none`:

```python
def test_no_project_id_returns_none(self):
    self.assertIsNone(project_contract.qdrant_project_filter(None))
    self.assertIsNone(project_contract.qdrant_project_filter(""))
```

### 5. `tool_metadata.py`

Remove `search_full` from the `activate_project_removed` description
(line ~36). Update any tool description that mentions `search_full`.

### 6. Update deprecation notices in other MCP servers

All four MCP server files (`cplus_mcp.py`, `android_mcp.py`,
`java_mcp.py`, `fastmcp_server.py`) have identical deprecation notice
text for `activate_project_removed`. Update all to remove `search_full`
references:

```python
# BEFORE
"project-scoped call. Callers must pass ``project_id`` (or "
"``search_full=true`` for cross-project queries) on every "

# AFTER
"project-scoped call. Callers must pass ``project_id`` to scope "
"to one project; omit it for cross-project queries. "
```

### 7. `fastmcp_server.py` comment (line ~172)

```python
# BEFORE
# ``search_full`` per call. There is no stateful default.

# AFTER — remove or replace with:
# ``project_id`` per call. Omit for cross-project queries.
```

## Verification

- `grep -rn "search_full" code-tiny/ doc-tiny/` → 0 matches.
- `grep -rn "ProjectScopeRequiredError" code-tiny/ doc-tiny/` → 0 matches.
- All unit tests pass:
  ```bash
  PYTHONPATH=code-tiny python -m pytest code-tiny/tools/common/test_project_scope.py -v
  PYTHONPATH=code-tiny python -m pytest tests/test_unified_mcp_input_coercion.py -v
  ```
- Doc-tiny tests pass:
  ```bash
  cd doc-tiny && python -m pytest tests/test_project_contract.py -v
  ```
