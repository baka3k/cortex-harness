"""Tests for collection tuning (phase 05): payload indexes, HNSW opt-in, rebuild script."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
for entry in (str(CODE_TINY),):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

from tools.common.local_qdrant import ensure_collection  # noqa: E402
from tools.common.primary_vector_sync import (  # noqa: E402
    SCOPE_INDEX_FIELDS,
    VectorDocument,
    sync_vector_documents,
)
from tests.test_primary_vector_sync import _Embedder, _LocalStore  # noqa: E402


@contextlib.contextmanager
def _tmpdir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class EnsureCollectionTuningTests(unittest.TestCase):
    def _store(self, exists=False):
        store = MagicMock()
        store.collection_exists.return_value = exists
        return store

    def tearDown(self):
        for key in ("QDRANT_HNSW_M", "QDRANT_HNSW_EF_CONSTRUCT", "QDRANT_SCALAR_QUANT"):
            os.environ.pop(key, None)
        import tools.common.local_qdrant as lq

        lq._INERT_TUNING_WARNED = False

    def test_env_unset_sends_no_tuning(self):
        store = self._store()
        ensure_collection(store, "col", 1024)
        kwargs = store.create_collection.call_args[1]
        self.assertNotIn("hnsw_config", kwargs)
        self.assertNotIn("quantization_config", kwargs)

    def test_env_set_forwards_hnsw_and_quantization(self):
        store = self._store()
        with patch.dict(os.environ, {
            "QDRANT_HNSW_M": "32",
            "QDRANT_HNSW_EF_CONSTRUCT": "256",
            "QDRANT_SCALAR_QUANT": "1",
        }):
            ensure_collection(store, "col", 1024)
        kwargs = store.create_collection.call_args[1]
        self.assertEqual(kwargs["hnsw_config"].m, 32)
        self.assertEqual(kwargs["hnsw_config"].ef_construct, 256)
        self.assertEqual(kwargs["quantization_config"].scalar.type, "int8")
        self.assertTrue(kwargs["quantization_config"].scalar.always_ram)

    def test_local_store_warns_inert_once(self):
        from cortex_harness.storage import LocalQdrantStore, QdrantStorageRole, resolve_storage

        with _tmpdir() as tmp:
            resolved = resolve_storage(tmp, qdrant_code_path=str(tmp / "c.qdrant"))
            local_store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
        buffer = io.StringIO()
        with patch.dict(os.environ, {"QDRANT_HNSW_M": "32"}):
            with contextlib.redirect_stdout(buffer):
                ensure_collection(local_store, "col_a", 8)
                ensure_collection(local_store, "col_b", 8)
        output = buffer.getvalue()
        self.assertIn("inert on local mode", output)
        # one warning per process, even across two ensure calls
        self.assertEqual(output.count("inert on local mode"), 1)


class _IndexedLocalStore(_LocalStore):
    """Adds collection info so the embed+upsert path can run."""

    def get_collection_info(self, collection):
        info = MagicMock()
        info.config.params.vectors = {"size": 2}
        return info


class ScopeIndexTests(unittest.TestCase):
    def test_all_filter_fields_indexed(self):
        self.assertEqual(
            SCOPE_INDEX_FIELDS,
            ("project_id_normalized", "parser", "root_scope", "file_path"),
        )

    def test_sync_creates_all_indexes_idempotent_calls(self):
        store = _IndexedLocalStore(collection_exists=True)
        documents = [
            VectorDocument(
                id="idx-doc-1",
                text="t",
                payload={"project_id": "project-a", "parser": "rust", "root_scope": "org/repo"},
            ),
        ]
        with patch.dict(os.environ, {}):
            count = sync_vector_documents(
                documents,
                url="local-code-store",
                collection="project_rust",
                model_name="m",
                device="cpu",
                embed_batch_size=1,
                qdrant_batch_size=8,
                parser="rust",
                project_id="project-a",
                root_scope="org/repo",
                embedder_factory=_Embedder,
                store=store,
            )
        self.assertEqual(count, 1)
        index_calls = [c for c in store.calls if c[0] == "create_payload_index"]
        self.assertEqual(
            [c[2]["field_name"] for c in index_calls],
            ["project_id_normalized", "parser", "root_scope", "file_path"],
        )
        self.assertTrue(all(c[2]["wait"] for c in index_calls))
        # single batch → the one upsert is the last batch → waits
        for call in (c for c in store.calls if c[0] == "upsert"):
            self.assertTrue(call[2]["wait"])


class _RebuildStubStore:
    """Minimal store implementing the surface the rebuild script uses."""

    def __init__(self, points, *, count_shortfall=0, second_src_shortfall=0):
        self.collections: dict[str, list[dict]] = {"src": list(points)}
        self.deleted: list[str] = []
        self.created: list[tuple] = []
        self.count_calls: list[str] = []
        self.count_shortfall = count_shortfall
        self.second_src_shortfall = second_src_shortfall

    def collection_exists(self, name):
        return name in self.collections

    def get_collection_info(self, name):
        # plain dict: model_to_dict passes dicts through unchanged
        return {"config": {"params": {"vectors": {"size": 8}}}}

    def create_collection(self, name, *, vectors_config, **kwargs):
        self.collections.setdefault(name, [])
        self.created.append((name, kwargs))

    def delete_collection(self, name):
        self.deleted.append(name)
        self.collections.pop(name, None)

    def upsert(self, name, points, *, wait):
        self.collections[name].extend(points)

    def scroll(self, name, *, limit, offset=None, with_payload=True, with_vectors=False):
        points = self.collections[name]
        start = offset or 0
        chunk = points[start:start + limit]
        next_offset = start + len(chunk)
        return chunk, (next_offset if next_offset < len(points) else None)

    def count(self, name, *, exact=True):
        self.count_calls.append(name)
        n = len(self.collections[name])
        if name == "src" and self.count_shortfall and self.count_calls.count("src") == 1:
            n -= self.count_shortfall  # step-3 validation sees fewer points
        if name == "src" and self.second_src_shortfall and self.count_calls.count("src") == 2:
            n -= self.second_src_shortfall  # step-4 assert on the target fails
        result = MagicMock()
        result.count = n
        return result


def _load_rebuild_module():
    spec = importlib.util.spec_from_file_location(
        "rebuild_vector_collection", CODE_TINY / "scripts" / "rebuild_vector_collection.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _points(n=10):
    return [
        {"id": f"p{i}", "vector": [0.1] * 8, "payload": {"text": "T" * 100, "symbol_id": f"s{i}"}}
        for i in range(n)
    ]


class RebuildScriptTests(unittest.TestCase):
    def setUp(self):
        self.rebuild = _load_rebuild_module()

    def _run(self, store, yes):
        buffer = io.StringIO()
        with patch.object(self.rebuild, "get_code_qdrant_store", return_value=store), patch.dict(
            os.environ, {"QDRANT_HNSW_M": "32"}
        ):
            with contextlib.redirect_stdout(buffer):
                code = self.rebuild.main(["src", "--yes"] if yes else ["src"])
        return code, buffer.getvalue()

    def test_dry_run_copies_and_does_not_touch_source(self):
        store = _RebuildStubStore(_points(10))
        code, output = self._run(store, yes=False)
        self.assertEqual(code, 0)
        self.assertEqual([name for name in store.deleted if name == "src"], [])
        self.assertIn("DRY-RUN", output)
        self.assertIn("count-assert", output)

    def test_count_mismatch_aborts_before_delete(self):
        store = _RebuildStubStore(_points(10), count_shortfall=3)
        code, output = self._run(store, yes=True)
        self.assertEqual(code, 1)
        self.assertNotIn("src", store.deleted)
        self.assertIn("ABORT", output)

    def test_full_run_second_count_assert_before_temp_drop(self):
        store = _RebuildStubStore(_points(10))
        code, output = self._run(store, yes=True)
        self.assertEqual(code, 0)
        # temp created once, source recreated, temp dropped only at the end
        self.assertIn("src_rebuild_tmp", store.deleted)
        self.assertIn("src", store.created[1][0] if len(store.created) > 1 else "src")
        self.assertEqual(store.count_calls[-1], "src")
        self.assertEqual(store.count_calls.count("src"), 2)
        self.assertEqual(len(store.collections["src"]), 10)

    def test_stale_temp_aborts_dry_run_without_yes(self):
        # A failed --yes run keeps temp as the recovery copy; a plain
        # dry-run must NOT silently delete it.
        store = _RebuildStubStore(_points(10))
        store.collections["src_rebuild_tmp"] = []
        code, output = self._run(store, yes=False)
        self.assertEqual(code, 1)
        self.assertIn("src_rebuild_tmp", store.collections)
        self.assertNotIn("src_rebuild_tmp", store.deleted)
        self.assertIn("ABORT", output)

    def test_stale_temp_dropped_with_yes(self):
        store = _RebuildStubStore(_points(10))
        store.collections["src_rebuild_tmp"] = []
        code, output = self._run(store, yes=True)
        self.assertEqual(code, 0)
        self.assertIn("src_rebuild_tmp", store.deleted)

    def test_second_count_assert_failure_keeps_temp(self):
        store = _RebuildStubStore(_points(10), second_src_shortfall=2)
        code, output = self._run(store, yes=True)
        self.assertEqual(code, 1)
        # temp must survive a failed target validation for recovery
        self.assertIn("src_rebuild_tmp", store.collections)
        self.assertNotIn("src_rebuild_tmp", store.deleted)
        self.assertIn("FAILED", output)


if __name__ == "__main__":
    unittest.main()
