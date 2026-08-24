import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common import message_scan  # noqa: E402
from tools.common.local_qdrant import model_to_dict  # noqa: E402


class _RecordingStore:
    def __init__(self) -> None:
        self.calls = []

    def collection_exists(self, collection):
        self.calls.append(("collection_exists", collection, {}))
        return True

    def create_payload_index(self, collection, field_name, *, wait):
        self.calls.append(
            (
                "create_payload_index",
                collection,
                {"field_name": field_name, "wait": wait},
            )
        )

    def delete(self, collection, *, filter_selector, wait):
        self.calls.append(
            (
                "delete",
                collection,
                {
                    "filter_selector": model_to_dict(filter_selector),
                    "wait": wait,
                },
            )
        )


class _RecordingWriter:
    instances = []

    def __init__(
        self,
        url,
        collection,
        vector_size,
        timeout=300.0,
        retries=3,
        retry_sleep=2.0,
    ) -> None:
        self.url = url
        self.collection = collection
        self.vector_size = vector_size
        self.timeout = timeout
        self.retries = retries
        self.retry_sleep = retry_sleep
        self._store = _RecordingStore()
        self.calls = []
        self.__class__.instances.append(self)

    def ensure_collection(self):
        self.calls.append(("ensure_collection", self.collection))

    def upsert(self, points):
        self.calls.append(("upsert", list(points)))


def _message_record() -> message_scan.MessageRecord:
    return message_scan.MessageRecord(
        id="msg::one",
        name="Notify",
        sender="UnitA.TFoo",
        receiver="Queue",
        payload="value",
        response=None,
        explanation="fixture",
        file_path="unit_a.pas",
        line=7,
        confidence=1.0,
        language="delphi",
        project_id="HyperPack",
    )


def _run_pipeline(root: str, **overrides):
    options = {
        "root": root,
        "parser": "delphi",
        "project_id": "HyperPack",
        "project_name": "Hyper Pack",
        "language": "delphi",
        "repo": "example/hyper-pack",
        "build_system": "delphi",
        "incremental": False,
        "changed_files": None,
        "deleted_files": None,
        "driver": None,
        "neo4j_database": None,
        "qdrant_url": "http://localhost:6333",
        "qdrant_collection": "hyperpack_mess",
        "output_dir": str(Path(root) / "artifacts"),
        "cache_dir": None,
        "commit_sha_before": "before",
        "commit_sha_after": "after",
        "qdrant_vector_size": 4,
    }
    options.update(overrides)
    return asyncio.run(message_scan.run_message_scan_pipeline(**options))


class MessageScanQdrantRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        _RecordingWriter.instances = []

    def test_full_scan_routes_cleanup_and_upsert_through_one_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory, (
            patch.object(
                message_scan,
                "LocalQdrantWriter",
                _RecordingWriter,
                create=True,
            )
        ), patch.object(
            message_scan,
            "get_code_qdrant_store",
            side_effect=AssertionError("legacy locator must not receive a remote URL"),
            create=True,
        ), patch.object(
            message_scan,
            "collect_messages_for_parser",
            return_value=[_message_record()],
        ):
            result = _run_pipeline(directory)

        self.assertEqual(result["qdrant_upserted"], 1)
        self.assertEqual(len(_RecordingWriter.instances), 1)
        writer = _RecordingWriter.instances[0]
        self.assertEqual(writer.url, "http://localhost:6333")
        self.assertEqual(
            [call[0] for call in writer._store.calls],
            ["collection_exists", "delete", "create_payload_index"],
        )
        full_filter = writer._store.calls[1][2]["filter_selector"]["filter"]
        self.assertEqual(
            full_filter["must"],
            [
                {
                    "key": "project_id_normalized",
                    "match": {"value": "hyperpack"},
                }
            ],
        )
        self.assertEqual(
            [call[0] for call in writer.calls],
            ["ensure_collection", "upsert"],
        )
        self.assertEqual(
            writer.calls[1][1][0]["payload"]["project_id_normalized"],
            "hyperpack",
        )

    def test_incremental_delete_only_routes_cleanup_through_writer_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory, (
            patch.object(
                message_scan,
                "LocalQdrantWriter",
                _RecordingWriter,
                create=True,
            )
        ), patch.object(
            message_scan,
            "get_code_qdrant_store",
            side_effect=AssertionError("legacy locator must not receive a remote URL"),
            create=True,
        ), patch.object(
            message_scan,
            "collect_messages_for_parser",
            return_value=[],
        ):
            result = _run_pipeline(
                directory,
                incremental=True,
                changed_files=[],
                deleted_files=["gone.pas"],
            )

        self.assertEqual(result["qdrant_upserted"], 0)
        self.assertEqual(len(_RecordingWriter.instances), 1)
        writer = _RecordingWriter.instances[0]
        self.assertEqual(
            [call[0] for call in writer._store.calls],
            ["collection_exists", "delete"],
        )
        incremental_filter = writer._store.calls[1][2]["filter_selector"]["filter"]
        self.assertEqual(
            incremental_filter["must"],
            [
                {"key": "project_id", "match": {"value": "HyperPack"}},
                {"key": "file_path", "match": {"any": ["gone.pas"]}},
            ],
        )
        self.assertEqual(writer.calls, [])

    def test_incremental_noop_does_not_open_qdrant(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            message_scan,
            "LocalQdrantWriter",
            _RecordingWriter,
            create=True,
        ), patch.object(
            message_scan,
            "collect_messages_for_parser",
            return_value=[],
        ):
            result = _run_pipeline(
                directory,
                incremental=True,
                changed_files=[],
                deleted_files=[],
            )

        self.assertEqual(result["qdrant_upserted"], 0)
        self.assertEqual(_RecordingWriter.instances, [])

    def test_incremental_cleanup_writer_open_failure_remains_best_effort(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            message_scan,
            "LocalQdrantWriter",
            side_effect=RuntimeError("local store lease failed"),
            create=True,
        ), patch.object(
            message_scan,
            "collect_messages_for_parser",
            return_value=[],
        ):
            result = _run_pipeline(
                directory,
                incremental=True,
                changed_files=[],
                deleted_files=["gone.pas"],
            )

        self.assertEqual(result["qdrant_upserted"], 0)


if __name__ == "__main__":
    unittest.main()
