"""Recording-driver test asserting every writer in language_writer.py emits
``project_id_normalized`` on the nodes and CALLS edges it writes.

Per the unified ingest/query contract plan (Phase 03), the standard
predicate ``n.project_id_normalized = $project_id_normalized`` must return
the same node count whether the writer is FunctionType, Field, Alias,
Template, or CALLS. This test runs each writer against a recording driver
and asserts the resulting Cypher touches the normalized field.

Run from the repo root::

    PYTHONPATH=code-tiny python -m unittest \
        code-tiny.tests.test_language_writer_project_scope
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


_CODE_TINY = Path(__file__).resolve().parents[1]
_LANGUAGE_WRITER_PATH = _CODE_TINY / "tools" / "graph" / "writer" / "language_writer.py"
_PROJECT_SCOPE_PATH = _CODE_TINY / "tools" / "common" / "project_scope.py"


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Load project_scope first because language_writer.py imports from it.
_load_module("tools.common.project_scope", _PROJECT_SCOPE_PATH)
language_writer = _load_module(
    "language_writer_under_test", _LANGUAGE_WRITER_PATH
)


class _RecordingDriver:
    """Captures every (query, params, database) tuple for assertions."""

    def __init__(self) -> None:
        self.calls = []

    async def execute_query(self, query, parameters=None, database=None):
        self.calls.append((query, dict(parameters or {}), database))
        return ([{"count": 0}], [], None)


def _row(project_id: str = "cortext", **overrides):
    """Build a minimal writer row with normalized scope populated."""
    base = {
        "id": "row-1",
        "name": "x",
        "qualified_name": "ns::x",
        "scope_name": "ns",
        "kind": "typedef",
        "target_name": "y",
        "type_signature": "void()",
        "file_path": "src/x.cpp",
        "start_line": 1,
        "end_line": 2,
        "code": "code",
        "comment": "",
        "summary": "",
        "note": "",
        "imports": [],
        "exports": [],
        "jsx_tags": [],
        "jsx_components": [],
        "external": False,
        "builtin": False,
        "react_role": "",
        "middleware_kind": "",
        "project_id": project_id,
        "project_name": "Cortex",
        "language": "cplus",
        "repo": "git@x",
        "build_system": "cmake",
    }
    base.update(overrides)
    # Enrich with normalized scope (mirrors the production pipeline).
    return sys.modules["tools.common.project_scope"].enrich_project_scope(base)


class LanguageWriterProjectScopeTests(unittest.IsolatedAsyncioTestCase):
    """Every writer must store ``project_id_normalized`` so the standard
    predicate matches every node/edge the writer creates."""

    def _assert_normalized_set(self, calls, label: str) -> None:
        combined = "\n".join(query for query, _, _ in calls)
        self.assertIn(
            f"{label}.project_id_normalized = row.project_id_normalized",
            combined,
            msg=(
                f"writer for {label} did not SET project_id_normalized. "
                f"Combined Cypher was:\n{combined}"
            ),
        )

    async def test_write_fields_sets_project_id_normalized(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        await writer.write_fields([_row()])
        self._assert_normalized_set(driver.calls, "f")

    async def test_write_aliases_sets_project_id_normalized(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        await writer.write_aliases([_row()])
        self._assert_normalized_set(driver.calls, "a")

    async def test_write_templates_sets_project_id_normalized(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        await writer.write_templates([_row()])
        self._assert_normalized_set(driver.calls, "t")

    async def test_write_function_types_sets_project_id_normalized(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        await writer.write_function_types([_row()])
        self._assert_normalized_set(driver.calls, "ft")

    async def test_write_calls_sets_project_id_normalized_on_edge(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        await writer.write_calls(
            [
                {
                    "caller_id": "fn-1",
                    "callee_id": "fn-2",
                    "call_type": "direct",
                    "project_id": "cortext",
                }
            ]
        )
        # ``enrich_project_scope`` was called by write_calls' batch pipeline
        # OR upstream. The Cypher itself must reference the normalized field
        # so the standard predicate matches CALLS edges.
        combined = "\n".join(query for query, _, _ in driver.calls)
        self.assertIn(
            "r.project_id_normalized = row.project_id_normalized",
            combined,
            msg="write_calls did not write r.project_id_normalized",
        )

    async def test_call_edge_carries_normalized_value_in_params(self) -> None:
        driver = _RecordingDriver()
        writer = language_writer.LanguageCodeWriter(driver, database="db")
        # ``write_batches`` enriches each row via ``enrich_project_scope``,
        # but the current implementation passes rows verbatim. Verify the
        # contract: callers must pre-enrich rows OR the writer will store
        # null. Document the precondition.
        rows = [
            {
                "caller_id": "fn-1",
                "callee_id": "fn-2",
                "call_type": "direct",
                "project_id": "cortext",
                "project_id_normalized": "cortext",
            }
        ]
        await writer.write_calls(rows)
        # Find the params dict and assert the normalized field is forwarded.
        params_seen = [
            params for _, params, _ in driver.calls if "rows" in params
        ]
        self.assertTrue(params_seen)
        first_batch = params_seen[0]["rows"]
        self.assertEqual(first_batch[0]["project_id_normalized"], "cortext")


if __name__ == "__main__":
    unittest.main()
