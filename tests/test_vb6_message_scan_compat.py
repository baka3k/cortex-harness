"""Phase 05: message_scan contract compatibility on the vb6 fixture (5.5).

Verifies the message-scan pipeline still consumes vb6 payloads unchanged in
shape (no Qdrant/embedder — the artifact lane only).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.message_scan import run_message_scan_pipeline  # noqa: E402


class Vb6MessageScanCompatTest(unittest.TestCase):
    def test_pipeline_runs_on_fixture_without_contract_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vb6_msgscan_") as tmp:
            summary = asyncio.run(run_message_scan_pipeline(
                root=str(FIXTURE_DIR),
                parser="vb6",
                project_id="vb6-fixture",
                project_name="vb6-fixture",
                language="vb6",
                repo=str(FIXTURE_DIR),
                build_system="",
                incremental=False,
                changed_files=[],
                deleted_files=[],
                driver=None,
                neo4j_database=None,
                qdrant_url=None,
                qdrant_collection=None,
                output_dir=tmp,
                cache_dir=None,
                commit_sha_before="",
                commit_sha_after="",
                embed_texts=None,
                verbose=False,
            ))
        self.assertIsInstance(summary, dict)
        self.assertIn("message_count", summary)
        self.assertGreaterEqual(int(summary.get("message_count", 0)), 0)


if __name__ == "__main__":
    unittest.main()
