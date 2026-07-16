import argparse
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.flutter import flutter_analyzer  # noqa: E402
from tools.go import go_analyzer  # noqa: E402
from tools.perl import perl_analyzer  # noqa: E402
from tools.rust import rust_analyzer  # noqa: E402
from tools.swift import swift_analyzer  # noqa: E402
from tools.sync import incremental_sync  # noqa: E402


def _payload(file_path):
    return {
        "file_def": {"file_path": file_path, "code": "source"},
        "namespaces": [],
        "types": [],
        "functions": [],
        "fields": [],
        "aliases": [],
        "templates": [],
        "relations": [],
        "calls": [],
    }


class PrimaryAnalyzerVectorContractTests(unittest.TestCase):
    def test_rust_go_and_swift_entrypoints_invoke_vector_sync(self):
        cases = (
            (rust_analyzer, "src/lib.rs"),
            (go_analyzer, "src/main.go"),
            (swift_analyzer, "Sources/App.swift"),
        )
        for module, file_path in cases:
            with self.subTest(parser=module.__name__), tempfile.TemporaryDirectory() as directory:
                with (
                    patch.object(module, "build_call_graph", return_value={"files": [_payload(file_path)]}),
                    patch.object(module, "_write_graph", new=AsyncMock(return_value={})),
                    patch.object(module, "_sync_vectors", return_value=1) as vector_sync,
                ):
                    result = asyncio.run(
                        module.main(
                            [
                                "--root", directory,
                                "--project-id", "project-a",
                                "--qdrant-url", "http://qdrant:6333",
                                "--qdrant-collection", "project_vectors",
                            ]
                        )
                    )
                self.assertEqual(result, 0)
                vector_sync.assert_called_once()

    def test_perl_and_dart_entrypoints_invoke_vector_sync(self):
        perl_fixture = ROOT / "tests" / "fixtures" / "perl-application"
        perl_result = SimpleNamespace(
            project_id="project-a",
            files=[SimpleNamespace()],
            symbols=[
                SimpleNamespace(kind="subroutine"),
                SimpleNamespace(kind="package"),
            ],
            changed_paths=(),
            deleted_paths=(),
            diagnostics=(),
            coverage="complete",
            to_json=lambda pretty=False: '{"project_id":"project-a"}',
        )
        with (
            tempfile.TemporaryDirectory() as cache,
            patch.object(perl_analyzer, "run_perl_analysis", return_value=perl_result),
            patch.object(perl_analyzer, "_write_graph", new=AsyncMock(return_value={})),
            patch.object(perl_analyzer, "_sync_vectors", return_value=1) as perl_sync,
        ):
            result = asyncio.run(
                perl_analyzer.main(
                    [
                        "--root", str(perl_fixture),
                        "--project-id", "project-a",
                        "--cache-dir", cache,
                        "--qdrant-url", "http://qdrant:6333",
                    ]
                )
            )
        self.assertEqual(result, 0)
        perl_sync.assert_called_once()

        dart_fixture = ROOT / "tests" / "fixtures" / "flutter-app"
        with (
            patch.object(flutter_analyzer, "write_graph", new=AsyncMock(return_value={})),
            patch.object(flutter_analyzer, "sync_vectors", return_value=1) as dart_sync,
        ):
            result = flutter_analyzer.main(
                [
                    "--root", str(dart_fixture),
                    "--mode", "dart",
                    "--project-id", "project-a",
                    "--qdrant-url", "http://qdrant:6333",
                    "--qdrant-collection", "project_vectors",
                ]
            )
        self.assertEqual(result, 0)
        dart_sync.assert_called_once()

    def test_vector_failure_has_a_distinct_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(rust_analyzer, "build_call_graph", return_value={"files": [_payload("src/lib.rs")]}),
                patch.object(rust_analyzer, "_write_graph", new=AsyncMock(return_value={})),
                patch.object(rust_analyzer, "_sync_vectors", side_effect=RuntimeError("qdrant unavailable")),
            ):
                result = asyncio.run(
                    rust_analyzer.main(
                        [
                            "--root", directory,
                            "--project-id", "project-a",
                            "--qdrant-url", "http://qdrant:6333",
                        ]
                    )
                )
        self.assertEqual(result, 4)

    def test_incremental_sync_normalizes_embedding_environment(self):
        args = incremental_sync.parse_args(
            [
                "--root", str(ROOT),
                "--embed-model", "fixture-model",
                "--embed-device", "mps",
                "--embed-batch-size", "7",
                "--max-embed-chars", "900",
            ]
        )
        env = incremental_sync._build_analyzer_env(args)
        self.assertEqual(env["CODE_EMBEDDING_MODEL"], "fixture-model")
        self.assertEqual(env["EMBED_DEVICE"], "mps")
        self.assertEqual(env["EMBED_BATCH_SIZE"], "7")
        self.assertEqual(env["MAX_EMBED_CHARS"], "900")

    def test_new_primary_analyzer_commands_include_configured_embedding_model(self):
        for parser_name in ("dart", "go", "perl", "rust", "swift"):
            with self.subTest(parser=parser_name):
                command = incremental_sync._build_analyzer_cmd(
                    python_bin="python",
                    analyzer=incremental_sync.ANALYZERS[parser_name],
                    root=str(ROOT),
                    project_id="project-a",
                    project_name="Project A",
                    before_sha="before",
                    after_sha="after",
                    changed_manifest=None,
                    deleted_manifest=None,
                    qdrant_collection="project_vectors",
                    message_scan_enabled=False,
                    message_output_dir=None,
                    message_qdrant_collection=None,
                    incremental=False,
                    verbose=False,
                    embed_model="jinaai/jina-embeddings-v3",
                )

                model_index = command.index("--embed-model")
                self.assertEqual(command[model_index + 1], "jinaai/jina-embeddings-v3")

    def test_new_analyzers_accept_android_style_direct_vector_flags(self):
        cases = (
            (rust_analyzer, "rust"),
            (go_analyzer, "go"),
            (swift_analyzer, "swift"),
            (perl_analyzer, "perl"),
            (flutter_analyzer, "dart"),
        )
        for module, language in cases:
            with self.subTest(language=language):
                args = module.parse_args(
                    [
                        "--qdrant-url", "http://localhost:6333",
                        "--qdrant-collection", "digital_key",
                        "--embed-model", "jinaai/jina-embeddings-v3",
                        "--device", "cuda",
                        "--root", r"C:\android-projects\digital_key",
                        "--repo", r"C:\android-projects\digital_key",
                        "--project-id", "digital_key",
                        "--project-name", "digital_key",
                        "--language", language,
                        "--batch-size", "1",
                        "--max-embed-chars", "800",
                        "--verbose",
                    ]
                )

                self.assertEqual(args.qdrant_url, "http://localhost:6333")
                self.assertEqual(args.qdrant_collection, "digital_key")
                self.assertEqual(args.embed_model, "jinaai/jina-embeddings-v3")
                self.assertEqual(args.device, "cuda")
                self.assertEqual(args.language, language)
                self.assertEqual(args.batch_size, 1)
                self.assertEqual(args.max_embed_chars, 800)
                self.assertTrue(args.verbose)

    def test_incremental_summary_extracts_analyzer_vector_count(self):
        output = "log line\n[SCAN_RESULT] parser=rust files=2 vectors=17 vector_status=success\n"

        self.assertEqual(incremental_sync._scan_result_vector_count(output), 17)
        self.assertIsNone(incremental_sync._scan_result_vector_count("[SCAN_RESULT] parser=rust files=2"))


if __name__ == "__main__":
    unittest.main()
