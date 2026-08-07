from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common import embedding_runtime
from tools.common import react_role_classifier


class EmbeddingRuntimeTests(unittest.TestCase):
    def test_complete_snapshot_is_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)
            for name in ("config.json", "model.safetensors", "tokenizer.json"):
                (snapshot / name).write_text("{}", encoding="utf-8")
            with patch.object(embedding_runtime, "snapshot_download", return_value=str(snapshot)):
                source, local_only = embedding_runtime.resolve_embedding_cache("jinaai/jina-embeddings-v3")
        self.assertEqual(source, str(snapshot))
        self.assertTrue(local_only)

    def test_cache_miss_installs_network_audit_before_fallback(self) -> None:
        with (
            patch.object(embedding_runtime, "snapshot_download", side_effect=embedding_runtime.LocalEntryNotFoundError("missing")),
            patch.object(embedding_runtime, "_enable_audit") as enable_audit,
        ):
            source, local_only = embedding_runtime.resolve_embedding_cache("missing/model")
        self.assertEqual(source, "missing/model")
        self.assertFalse(local_only)
        enable_audit.assert_called_once()

    def test_audit_logs_final_redirect_without_query_string(self) -> None:
        session = embedding_runtime._AuditSession()
        response = SimpleNamespace(
            status_code=200,
            url="https://cdn.example.test/model.safetensors?signature=secret",
            history=(),
        )
        with (
            patch("requests.Session.request", return_value=response),
            patch("builtins.print") as output,
        ):
            session.request("GET", "https://huggingface.co/org/model/resolve/main/model.safetensors?token=secret")
        logs = " ".join(str(call) for call in output.call_args_list)
        self.assertIn("download missing model artifact", logs)
        self.assertIn("https://cdn.example.test/model.safetensors", logs)
        self.assertNotIn("signature=secret", logs)

    def test_react_role_request_logs_destination_and_purpose(self) -> None:
        response = SimpleNamespace(status=200, read=lambda: b'{"choices": []}')
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        with (
            patch("urllib.request.urlopen", return_value=response_context),
            patch("builtins.print") as output,
        ):
            react_role_classifier._http_post("https://api.example.test/v1/chat?key=secret", {}, {}, 20)
        logs = " ".join(str(call) for call in output.call_args_list)
        self.assertIn("classify uncertain React roles", logs)
        self.assertIn("https://api.example.test/v1/chat", logs)
        self.assertNotIn("key=secret", logs)
