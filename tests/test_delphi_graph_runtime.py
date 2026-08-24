import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.delphi import delphi_analyzer  # noqa: E402


_DELPHI_FIELD_FIXTURE = """\
unit UnitA;

interface

type
  TFoo = class
  private
    FCount: Integer;
  end;

implementation

end.
"""


class _CapturingWriter:
    def __init__(self) -> None:
        self.batch_size = 100
        self.database = "hyperpack"
        self.driver = object()
        self.write_all_calls = []

    async def write_all(self, **kwargs):
        self.write_all_calls.append(kwargs)
        return {}


class DelphiGraphRuntimeTests(unittest.TestCase):
    def test_build_call_graph_emits_project_scoped_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unit_a.pas").write_text(
                _DELPHI_FIELD_FIXTURE,
                encoding="utf-8",
            )
            writer = _CapturingWriter()

            asyncio.run(
                delphi_analyzer.build_call_graph(
                    root=str(root),
                    code_writer=writer,
                    qdrant_writer=None,
                    embedder=None,
                    batch_size=8,
                    qdrant_batch_size=8,
                    cache_dir=str(root / ".cache"),
                    keep_cache=False,
                    parse_cache=False,
                    neo4j_batch_size=8,
                    neo4j_calls_batch_size=8,
                    neo4j_state_path=None,
                    project_id="hyperpack",
                    project_name="Hyper Pack",
                    language="delphi",
                    repo="git@example/hyper-pack.git",
                    build_system="delphi",
                    call_stats_path=None,
                    unresolved_calls_path=None,
                    parse_errors_path=None,
                    parse_run_id="test-run",
                    commit_sha="deadbeef",
                    verbose=False,
                )
            )

            self.assertEqual(len(writer.write_all_calls), 1)
            fields = writer.write_all_calls[0]["fields"]
            self.assertEqual(len(fields), 1)
            self.assertEqual(
                {
                    key: fields[0].get(key)
                    for key in (
                        "project_id",
                        "project_name",
                        "language",
                        "repo",
                        "build_system",
                    )
                },
                {
                    "project_id": "hyperpack",
                    "project_name": "Hyper Pack",
                    "language": "delphi",
                    "repo": "git@example/hyper-pack.git",
                    "build_system": "delphi",
                },
            )

    def test_main_disables_unfingerprinted_legacy_graph_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build_call_graph = AsyncMock(return_value=None)
            with (
                patch.object(delphi_analyzer, "prepare_graph_args", return_value=False),
                patch.object(delphi_analyzer, "build_call_graph", new=build_call_graph),
            ):
                result = asyncio.run(
                    delphi_analyzer.main(
                        [
                            "--root",
                            directory,
                            "--project-id",
                            "hyperpack",
                            "--disable-message-scan",
                        ]
                    )
                )

            self.assertEqual(result, 0)
            self.assertIsNone(
                build_call_graph.await_args.kwargs["neo4j_state_path"]
            )

    def test_main_rejects_explicit_legacy_graph_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build_call_graph = AsyncMock(return_value=None)
            with (
                patch.object(delphi_analyzer, "prepare_graph_args", return_value=False),
                patch.object(delphi_analyzer, "build_call_graph", new=build_call_graph),
            ):
                result = asyncio.run(
                    delphi_analyzer.main(
                        [
                            "--root",
                            directory,
                            "--neo4j-state",
                            str(Path(directory) / "legacy-state.json"),
                            "--disable-message-scan",
                        ]
                    )
                )

            self.assertEqual(result, 2)
            build_call_graph.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
