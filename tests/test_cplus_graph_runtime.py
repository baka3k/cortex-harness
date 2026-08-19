from __future__ import annotations

import asyncio
import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus import cplus_analyzer  # noqa: E402


def test_embedding_source_fingerprint_uses_owner_only_manifest_cache(tmp_path: Path):
    model_dir = tmp_path / "model"
    cache_dir = tmp_path / "fingerprints"
    model_dir.mkdir()
    weight_path = model_dir / "weights.bin"
    weight_path.write_bytes(b"model-v1")

    with mock.patch.dict(
        os.environ,
        {"CORTEX_EMBEDDING_FINGERPRINT_CACHE_DIR": str(cache_dir)},
    ):
        first = cplus_analyzer._embedding_source_fingerprint(str(model_dir))
        original_open = open

        def guarded_open(path, mode="r", *args, **kwargs):
            if os.path.realpath(path) == os.path.realpath(weight_path) and "r" in mode:
                raise AssertionError("cached fingerprint reread model bytes")
            return original_open(path, mode, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=guarded_open):
            second = cplus_analyzer._embedding_source_fingerprint(str(model_dir))

        weight_path.write_bytes(b"model-v2")
        third = cplus_analyzer._embedding_source_fingerprint(str(model_dir))

    cache_files = list(cache_dir.glob("*.json"))
    assert first == second
    assert third != first
    assert len(cache_files) == 1
    assert stat.S_IMODE(cache_files[0].stat().st_mode) == 0o600


class _FakeDriver:
    provider = "falkordb"

    def __init__(self, *, fail_parse_run: bool = False) -> None:
        self.fail_parse_run = fail_parse_run
        self.queries = []

    async def execute_query(self, query, parameters=None, database=None, **kwargs):
        if self.fail_parse_run and "MERGE (r:ParseRun" in query:
            raise RuntimeError("parse run unavailable")
        params = dict(parameters or {})
        self.queries.append((query, params, database))
        return ([{"count": len(params.get("rows", []))}], [], None)


class _FakeCodeWriter:
    def __init__(
        self,
        *,
        fail_parse_run: bool = False,
        fail_deferred_includes: bool = False,
    ) -> None:
        self.driver = _FakeDriver(fail_parse_run=fail_parse_run)
        self.database = "code"
        self.batch_size = 100
        self.calls = []
        self.node_writes = []
        self.file_ids = set()
        self.include_write_file_counts = []
        self.fail_deferred_includes = fail_deferred_includes

    async def write_nodes_batch(self, key, cypher, rows, state=None, state_writer=None):
        self.node_writes.append((key, cypher, list(rows)))
        return len(rows)

    async def write_all(self, **kwargs):
        for file_row in kwargs.get("files") or []:
            self.file_ids.add(file_row["id"])
        self.record_include_relations(kwargs.get("relations") or [])
        return {}

    async def write_relations_typed(
        self, relations, state=None, state_writer=None, **kwargs
    ):
        if self.fail_deferred_includes:
            raise RuntimeError("deferred include failure")
        self.record_include_relations(relations)
        return len(relations)

    def record_include_relations(self, relations):
        for relation in relations:
            if relation.get("rel_type") != "INCLUDES":
                continue
            self.assert_include_endpoints_exist(relation)
            self.include_write_file_counts.append(len(self.file_ids))

    def assert_include_endpoints_exist(self, relation):
        if relation["source_id"] not in self.file_ids:
            raise AssertionError(f"missing include source: {relation['source_id']}")
        if relation["target_id"] not in self.file_ids:
            raise AssertionError(f"missing include target: {relation['target_id']}")

    async def write_calls_with_site(self, calls):
        self.calls.extend(calls)
        return len(calls)


def _run_build(
    root: str,
    cache: str,
    writer: _FakeCodeWriter,
    *,
    parse_run_id: str,
    verbose: bool = False,
    neo4j_state_path: str | None = None,
) -> None:
    asyncio.run(
        cplus_analyzer.build_call_graph(
            root=root,
            code_writer=writer,
            qdrant_writer=None,
            embedder=None,
            batch_size=16,
            qdrant_batch_size=16,
            cache_dir=cache,
            keep_cache=False,
            parse_cache=False,
            neo4j_batch_size=16,
            neo4j_calls_batch_size=16,
            neo4j_state_path=neo4j_state_path,
            project_id="demo",
            project_name="Demo",
            language="cplus",
            repo=root,
            build_system="",
            event_map_path=None,
            call_stats_path=None,
            possible_calls_path=None,
            unresolved_calls_path=None,
            parse_errors_path=None,
            parse_run_id=parse_run_id,
            commit_sha="abc123",
            verbose=verbose,
        )
    )


class CPlusGraphRuntimeTests(unittest.TestCase):
    def test_proc_sql_custom_nodes_persist_normalized_project_scope(self) -> None:
        source = """\
int main(void) {
    EXEC SQL SELECT NAME INTO :customer_name FROM CUSTOMER;
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            Path(root, "sample.pc").write_text(source, encoding="utf-8")
            writer = _FakeCodeWriter()
            _run_build(root, cache, writer, parse_run_id="scope-run")

        sql_writes = [
            (key, cypher, rows)
            for key, cypher, rows in writer.node_writes
            if key in {"SqlStatement", "SqlDirective", "SqlCursor", "SqlHostVariable", "DatabaseTable"}
        ]
        self.assertTrue(sql_writes)
        self.assertTrue(
            all("project_id_normalized = row.project_id_normalized" in cypher for _, cypher, _ in sql_writes)
        )

    def test_parse_metadata_separates_error_missing_and_decode_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            malformed = Path(root, "malformed.c")
            malformed.write_bytes(b"void broken( {\n\x81")
            payload = cplus_analyzer.parse_c_family_file(str(malformed), root, False)

        parse_meta = payload[-1]
        self.assertTrue(parse_meta["has_error"])
        self.assertGreaterEqual(parse_meta["error_nodes"], 0)
        self.assertGreaterEqual(parse_meta["missing_nodes"], 0)
        self.assertGreater(parse_meta["error_nodes"] + parse_meta["missing_nodes"], 0)
        self.assertEqual(parse_meta["source_encoding"], "cp1252")
        self.assertTrue(parse_meta["lossy_decode"])

    def test_buffer_progress_is_absolute_and_call_identity_survives_new_run_id(self) -> None:
        source = "void target(void) {}\nvoid caller(void) { target(); missing(); }\n"
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            Path(root, "sample.c").write_text(source, encoding="utf-8")
            first = _FakeCodeWriter()
            second = _FakeCodeWriter()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                _run_build(root, str(Path(cache, "one")), first, parse_run_id="run-one", verbose=True)
            _run_build(root, str(Path(cache, "two")), second, parse_run_id="run-two")

        output = stdout.getvalue()
        self.assertIn(
            "[graph] buffer_started index=1/1 files=1-1 processed=0/1 size=1",
            output,
        )
        self.assertIn(
            "[graph] buffer_finished index=1/1 files=1-1 processed=1/1",
            output,
        )
        self.assertTrue(first.calls)
        self.assertEqual(
            [call["site_id"] for call in first.calls],
            [call["site_id"] for call in second.calls],
        )
        self.assertTrue(
            all(call["site_id"] == call["props"]["stable_site_id"] for call in first.calls)
        )
        self.assertTrue(all(call["props"]["parse_run_id"] == "run-one" for call in first.calls))
        self.assertTrue(all(call["props"]["parse_run_id"] == "run-two" for call in second.calls))

        first_unknown = [
            row
            for query, params, _ in first.driver.queries
            if "UNKNOWN_CALL" in query
            for row in params.get("rows", [])
        ]
        second_unknown = [
            row
            for query, params, _ in second.driver.queries
            if "UNKNOWN_CALL" in query
            for row in params.get("rows", [])
        ]
        self.assertTrue(first_unknown)
        self.assertEqual(
            [row["site_id"] for row in first_unknown],
            [row["site_id"] for row in second_unknown],
        )
        self.assertTrue(
            all(row["site_id"] == row["props"]["stable_site_id"] for row in first_unknown)
        )

    def test_include_edges_wait_for_targets_in_later_file_buffers(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            Path(root, "a000.h").write_text('#include "z500.h"\n', encoding="utf-8")
            for index in range(1, 500):
                Path(root, f"a{index:03d}.h").write_text("", encoding="utf-8")
            Path(root, "z500.h").write_text("", encoding="utf-8")

            writer = _FakeCodeWriter()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                _run_build(root, cache, writer, parse_run_id="run-one", verbose=True)

        self.assertEqual(writer.include_write_file_counts, [501])
        output = stdout.getvalue()
        self.assertIn(
            "[graph] deferred_includes write_started count=1 storage=memory-only "
            "persistent_retry_queue=false retry=next-full-idempotent-replay",
            output,
        )
        self.assertIn("[graph] deferred_includes write_finished count=1", output)

    def test_failed_deferred_includes_log_replay_policy(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            Path(root, "a.h").write_text('#include "z.h"\n', encoding="utf-8")
            Path(root, "z.h").write_text("", encoding="utf-8")
            writer = _FakeCodeWriter(fail_deferred_includes=True)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                with self.assertRaisesRegex(RuntimeError, "deferred include failure"):
                    _run_build(root, cache, writer, parse_run_id="run-one", verbose=True)

        self.assertIn(
            "[graph] deferred_includes write_failed count=1 error=RuntimeError "
            "persistent_retry_queue=false retry=next-full-idempotent-replay",
            stdout.getvalue(),
        )

    def test_explicit_graph_resume_state_fails_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            writer = _FakeCodeWriter()
            with self.assertRaisesRegex(ValueError, "graph resume is disabled"):
                _run_build(
                    root,
                    cache,
                    writer,
                    parse_run_id="run-one",
                    neo4j_state_path=str(Path(cache, "graph-state.json")),
                )
        self.assertFalse(writer.driver.queries)

    def test_parse_run_lifecycle_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            writer = _FakeCodeWriter(fail_parse_run=True)
            with self.assertRaisesRegex(RuntimeError, "parse run unavailable"):
                _run_build(root, cache, writer, parse_run_id="run-one")


if __name__ == "__main__":
    unittest.main()
