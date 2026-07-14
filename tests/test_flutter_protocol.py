import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.flutter.protocol import ProtocolError, parse_jsonl, serialize_records  # noqa: E402


GOLDEN = ROOT / "tests" / "fixtures" / "flutter-protocol-v1.jsonl"


class FlutterProtocolTest(unittest.TestCase):
    def test_golden_stream_round_trips_deterministically(self):
        text = GOLDEN.read_text(encoding="utf-8")
        facts = parse_jsonl(text.splitlines())
        records = [
            facts.header,
            *facts.nodes,
            *facts.edges,
            *facts.diagnostics,
            facts.summary,
        ]
        self.assertEqual(serialize_records(records), text)
        self.assertEqual(facts.header.schema_version, "1.0")
        self.assertEqual(len(facts.nodes), 2)

    def test_rejects_unsupported_major_before_any_graph_work(self):
        lines = [
            '{"type":"header","schema_version":"2.0","analyzer_version":"x","sdk_version":"x","root":"/tmp/x","project_id":"x"}',
            '{"type":"summary","processed_files":0,"skipped_files":0,"error_count":0,"elapsed_ms":0}',
        ]
        with self.assertRaisesRegex(ProtocolError, "unsupported protocol major"):
            parse_jsonl(lines)

    def test_rejects_stdout_noise_and_dangling_edges(self):
        with self.assertRaisesRegex(ProtocolError, "invalid JSON"):
            parse_jsonl(["progress... 10%"])
        text = GOLDEN.read_text(encoding="utf-8").replace('"target":"function:main"', '"target":"missing"')
        with self.assertRaisesRegex(ProtocolError, "edge endpoints are missing"):
            parse_jsonl(text.splitlines())


if __name__ == "__main__":
    unittest.main()
