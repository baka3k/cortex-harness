import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.primary_vector_sync import (  # noqa: E402
    deterministic_point_id,
    documents_from_rows,
    redact_text,
    sync_vector_documents,
)


class _HttpError(RuntimeError):
    def __init__(self, response):
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _HttpError(self)


class _Requests:
    RequestException = _HttpError

    def __init__(self, *, fail_upsert=False):
        self.calls = []
        self.fail_upsert = fail_upsert

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "GET":
            return _Response(404)
        if self.fail_upsert and method == "PUT" and "/points?" in url:
            return _Response(503)
        return _Response(200)


class _Vectors(list):
    @property
    def shape(self):
        return (len(self), len(self[0]) if self else 0)


class _Embedder:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def encode(self, texts, **kwargs):
        return _Vectors([[float(index + 1), 0.5] for index, _ in enumerate(texts)])


def _documents(count=3):
    rows = {
        "functions": [
            {
                "id": f"fn::{index}",
                "name": f"function_{index}",
                "qualified_name": f"pkg.function_{index}",
                "kind": "function",
                "file_path": f"src/file_{index}.rs",
                "code": "password=secret-value\nfn body() {}",
                "project_id": "project-a",
                "project_name": "Project A",
                "language": "rust",
                "repo": "org/repo",
            }
            for index in range(count)
        ],
        "relations": [{"source_id": "ignored"}],
    }
    return documents_from_rows(rows, parser="rust", root_scope="org/repo", max_chars=120)


class PrimaryVectorSyncTests(unittest.TestCase):
    def test_contract_is_deterministic_bounded_and_redacted(self):
        first = _documents(1)[0]
        second = _documents(1)[0]

        self.assertEqual(first.id, second.id)
        self.assertLessEqual(len(first.text), 120)
        self.assertNotIn("secret-value", first.text)
        self.assertNotIn("secret-value", first.payload["text"])
        self.assertEqual(
            {
                "node_type",
                "symbol_id",
                "project_id",
                "project_name",
                "language",
                "repo",
                "file_path",
                "name",
                "qualified_name",
                "parser",
                "root_scope",
                "text",
            },
            set(first.payload),
        )

    def test_point_identity_is_isolated_by_root_scope(self):
        first = deterministic_point_id("rust", "project-a", "fn::one", "org/repo-a")
        second = deterministic_point_id("rust", "project-a", "fn::one", "org/repo-b")

        self.assertNotEqual(first, second)
        self.assertEqual(
            first,
            deterministic_point_id("rust", "project-a", "fn::one", "org/repo-a"),
        )

    def test_redaction_covers_quoted_json_yaml_shell_and_multiline_values(self):
        source = "\n".join(
            (
                "password='secret value'",
                '\"api_key\": \"json secret\"',
                "access_token: yaml-secret",
                'export AUTH_TOKEN="shell secret"',
                'secret="first line\nsecond line"',
            )
        )

        redacted = redact_text(source)

        for secret in ("secret value", "json secret", "yaml-secret", "shell secret", "first line", "second line"):
            self.assertNotIn(secret, redacted)
        self.assertGreaterEqual(redacted.count("[REDACTED]"), 5)

    def test_unbounded_text_and_cross_scope_documents_are_rejected(self):
        rows = {
            "functions": [
                {
                    "id": "fn::one",
                    "project_id": "project-a",
                    "file_path": "src/one.rs",
                    "code": "source",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "max_chars"):
            documents_from_rows(rows, parser="rust", root_scope="org/repo", max_chars=0)

        requests = _Requests()
        with self.assertRaisesRegex(ValueError, "scope"):
            sync_vector_documents(
                _documents(1),
                url="http://qdrant:6333",
                collection="project_rust",
                model_name="fixture-model",
                device="cpu",
                embed_batch_size=1,
                qdrant_batch_size=1,
                parser="rust",
                project_id="other-project",
                root_scope="org/repo",
                requests_module=requests,
                embedder_factory=_Embedder,
                retries=0,
            )
        self.assertFalse(requests.calls)

    def test_upserts_in_batches_before_incremental_scoped_cleanup(self):
        requests = _Requests()
        documents = _documents(3)

        count = sync_vector_documents(
            documents,
            url="http://qdrant:6333",
            collection="project_rust",
            model_name="fixture-model",
            device="cpu",
            embed_batch_size=2,
            qdrant_batch_size=2,
            parser="rust",
            project_id="project-a",
            root_scope="org/repo",
            cleanup_paths=["src/file_0.rs", "deleted.rs"],
            requests_module=requests,
            embedder_factory=_Embedder,
            retries=0,
        )

        self.assertEqual(count, 3)
        point_upserts = [call for call in requests.calls if call[0] == "PUT" and "/points?" in call[1]]
        self.assertEqual([len(call[2]["json"]["points"]) for call in point_upserts], [2, 1])
        self.assertEqual(requests.calls[-1][0], "POST")
        point_filter = requests.calls[-1][2]["json"]["filter"]
        self.assertIn({"key": "project_id", "match": {"value": "project-a"}}, point_filter["must"])
        self.assertIn({"key": "parser", "match": {"value": "rust"}}, point_filter["must"])
        self.assertIn({"key": "root_scope", "match": {"value": "org/repo"}}, point_filter["must"])
        self.assertIn(
            {"key": "file_path", "match": {"any": ["deleted.rs", "src/file_0.rs"]}},
            point_filter["must"],
        )
        self.assertEqual(point_filter["must_not"], [{"has_id": [item.id for item in documents]}])

    def test_delete_only_incremental_run_does_not_load_embedder(self):
        requests = _Requests()

        count = sync_vector_documents(
            [],
            url="http://qdrant:6333",
            collection="project_rust",
            model_name="",
            device="cpu",
            embed_batch_size=1,
            qdrant_batch_size=1,
            parser="rust",
            project_id="project-a",
            root_scope="org/repo",
            cleanup_paths=["deleted.rs"],
            requests_module=requests,
            embedder_factory=lambda *args, **kwargs: self.fail("embedder must not be loaded"),
            retries=0,
        )

        self.assertEqual(count, 0)
        self.assertEqual([call[0] for call in requests.calls], ["POST"])

    def test_configured_upsert_failure_is_fatal_and_skips_cleanup(self):
        requests = _Requests(fail_upsert=True)

        with self.assertRaises(_HttpError):
            sync_vector_documents(
                _documents(1),
                url="http://qdrant:6333",
                collection="project_rust",
                model_name="fixture-model",
                device="cpu",
                embed_batch_size=1,
                qdrant_batch_size=1,
                parser="rust",
                project_id="project-a",
                root_scope="org/repo",
                full_replace=True,
                requests_module=requests,
                embedder_factory=_Embedder,
                retries=0,
            )

        self.assertFalse(any(call[0] == "POST" for call in requests.calls))


if __name__ == "__main__":
    unittest.main()
