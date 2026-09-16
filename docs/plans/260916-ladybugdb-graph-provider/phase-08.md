# Phase 8: Tests

## Mục tiêu
Comprehensive test coverage cho LadybugDB integration.

## Test Categories

### 1. Unit Tests: Driver
**File:** `code-tiny/tests/test_ladybug_driver.py`

```python
class TestLadybugDBDriver:
    def test_open_close(self, tmp_path):
        """Driver opens .lbdb file and closes cleanly."""
    
    def test_verify_connection(self, tmp_path):
        """verify_connection() returns True after open."""
    
    def test_execute_query_read(self, tmp_path):
        """Execute MATCH query returns records."""
    
    def test_execute_query_write(self, tmp_path):
        """Execute CREATE query writes data."""
    
    def test_parameterized_query(self, tmp_path):
        """$param syntax works correctly."""
    
    def test_cypher_normalize_remove(self):
        """REMOVE → SET NULL rewrite."""
    
    def test_cypher_normalize_labels(self):
        """labels() → label() rewrite."""
    
    def test_cypher_reject_call_subquery(self):
        """CALL { } raises UnsupportedCypherError."""
    
    def test_schema_ensure(self, tmp_path):
        """ensure_schema() creates NODE TABLE + REL TABLE."""
    
    def test_batch_write_nodes(self, tmp_path):
        """batch_write_nodes writes multiple nodes."""
    
    def test_batch_write_edges(self, tmp_path):
        """batch_write_edges writes multiple edges."""
    
    def test_thread_safety(self, tmp_path):
        """Concurrent reads don't crash."""
```

### 2. Unit Tests: Provider Registration
**File:** `code-tiny/tests/test_ladybug_provider.py`

```python
class TestLadybugProviderRegistration:
    def test_enum_value(self):
        assert GraphProvider.LADYBUG.value == "ladybug"
    
    def test_normalize_ladybug(self):
        assert normalize_graph_provider_name("ladybug") == "ladybug"
    
    def test_normalize_ladybugdb_alias(self):
        assert normalize_graph_provider_name("ladybugdb") == "ladybug"
    
    def test_normalize_ladybug_casefold(self):
        assert normalize_graph_provider_name("LADYBUG") == "ladybug"
    
    def test_factory_create(self):
        driver = GraphDriverFactory.create_driver(
            GraphProvider.LADYBUG, {"path": "/tmp/test.lbdb"}
        )
        assert isinstance(driver, LadybugDBDriver)
    
    def test_isolate_env_strips_falkordb(self):
        env = {"GRAPH_PROVIDER": "ladybug", "FALKORDB_PATH": "/x", "LADYBUG_PATH": "/y"}
        isolate_graph_provider_environment(env, "GRAPH_PROVIDER")
        assert "FALKORDB_PATH" not in env
        assert env["LADYBUG_PATH"] == "/y"
    
    def test_isolate_env_strips_neo4j(self):
        env = {"GRAPH_PROVIDER": "ladybug", "NEO4J_URI": "bolt://x"}
        isolate_graph_provider_environment(env, "GRAPH_PROVIDER")
        assert "NEO4J_URI" not in env
```

### 3. Integration Tests: Project ID Rules
**File:** `code-tiny/tests/test_ladybug_project_id.py`

```python
class TestLadybugProjectIdRules:
    def test_unscoped_returns_all(self, tmp_path, seeded_driver):
        """R1: No project_id → all results."""
    
    def test_exact_match(self, tmp_path, seeded_driver):
        """R2: project_id='bank_android' → only that project."""
    
    def test_prefix_match(self, tmp_path, seeded_driver):
        """R4: project_id='bank' → bank_android + bank_cplus."""
    
    def test_casefold_match(self, tmp_path, seeded_driver):
        """R3: project_id='Bank' → same as 'bank'."""
    
    def test_write_exact_isolation(self, tmp_path):
        """R6: Write to project A doesn't affect project B."""
```

### 4. Integration Tests: Ingest
**File:** `code-tiny/tests/test_ladybug_ingest.py`

```python
class TestLadybugIngest:
    def test_code_graph_ingest(self, tmp_path, sample_codebase):
        """Ingest code graph → verify nodes/edges."""
    
    def test_doc_graph_ingest(self, tmp_path, sample_docs):
        """Ingest doc graph → verify entities/relations."""
    
    def test_schema_auto_created(self, tmp_path):
        """Schema tables created automatically before ingest."""
```

### 5. Storage Tests
**File:** `tests/test_ladybug_storage.py`

```python
class TestLadybugStorage:
    def test_default_path(self):
        """Default ladybug path resolves correctly."""
    
    def test_env_override(self, monkeypatch):
        """LADYBUG_PATH env var overrides default."""
    
    def test_role_based_paths(self):
        """code vs doc paths are different."""
    
    def test_layout_includes_ladybug(self):
        """storage-layout output shows ladybug paths."""
```

### 6. Dev CLI Tests
**File:** `tests/test_ladybug_dev.py`

```python
class TestLadybugDevCli:
    def test_graph_provider_ladybug(self):
        """_graph_provider() accepts 'ladybug'."""
    
    def test_env_isolation(self):
        """_isolate_graph_provider_environment() strips other providers."""
    
    def test_doctor_ladybug(self):
        """dev doctor reports LadybugDB status."""
```

## Test Fixtures
- `tmp_path` → temporary `.lbdb` file
- `seeded_driver` → driver with pre-loaded test data (multiple projects)
- `sample_codebase` → minimal code files for ingest
- `sample_docs` → minimal documents for doc ingest

## CI Integration
- LadybugDB package install: `pip install ladybug` trong CI
- Skip on Windows if LadybugDB doesn't support Windows (check)
- Mark tests: `@pytest.mark.ladybug` cho optional execution

## Acceptance
- All tests pass
- Coverage: driver, provider, project_id rules, ingest, storage, CLI
- No regression: existing FalkorDB/Neo4j tests still pass
