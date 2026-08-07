from __future__ import annotations

import asyncio
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus import cplus_analyzer


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
    def __init__(self, *, fail_parse_run: bool = False) -> None:
        self.driver = _FakeDriver(fail_parse_run=fail_parse_run)
        self.database = "code"
        self.batch_size = 100
        self.calls = []

    async def write_nodes_batch(self, key, cypher, rows, state=None, state_writer=None):
        return len(rows)

    async def write_all(self, **kwargs):
        return {}

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
