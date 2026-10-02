"""Regression: an id registered under several node lanes must not abort write_all.

A module declaring `Private Enum X` while another producer emits the same id
as :Type made write_all raise "cannot infer target_label ... candidates=
['Enum', 'Type']" and abort the whole graph write. Contract now: drop the
ambiguous row, count it as `ambiguous_relations`, keep writing. A required
edge to a node that does not exist at all still raises (producer bug worth
failing on).
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402


class CapturingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, parameters=None, database=None):
        self.calls.append((query, parameters or {}, database))
        rows = (parameters or {}).get("rows", [])
        return ([{"count": len(rows)}], [], None)

    async def create_indexes(self, specs, database=None):
        return None


ENUM_ID = "DemoEnum@demo/src/DemoForm.frm"
FN_ID = "DemoForm.DemoProc/0@demo/src/DemoForm.frm"
VAR_ID = "DemoForm.demoState@demo/src/DemoForm.frm"


class AmbiguousLabelRelationTest(unittest.TestCase):
    def test_multi_lane_id_drops_row_instead_of_raising(self) -> None:
        driver = CapturingDriver()
        writer = LanguageCodeWriter(driver, database="ambig", batch_size=100)
        counts = asyncio.run(writer.write_all(
            projects=[{"id": "p1", "name": "p1"}],
            functions=[{"id": FN_ID, "name": "DemoProc"}],
            enums=[{"id": ENUM_ID, "name": "DemoEnum"}],
            types=[{"id": ENUM_ID, "name": "DemoEnum"}],
            variables=[{"id": VAR_ID, "name": "demoState"}],
            relations=[
                {
                    # labelless row onto the ambiguous id — raised before the fix
                    "source_id": FN_ID,
                    "target_id": ENUM_ID,
                    "rel_type": "USES_TYPE",
                    "properties": {},
                },
                {
                    # clean row must still be written
                    "source_id": FN_ID,
                    "target_id": VAR_ID,
                    "rel_type": "USES",
                    "properties": {},
                },
            ],
            use_full_writers=True,
        ))
        self.assertEqual(counts.get("ambiguous_relations"), 1)
        written = [
            row
            for _query, params, _db in driver.calls
            for row in params.get("rows", [])
            if isinstance(row, dict) and "target_id" in row
        ]
        targets = [row.get("target_id") for row in written]
        self.assertNotIn(ENUM_ID, targets)
        self.assertIn(VAR_ID, targets)

    def test_missing_node_on_required_relation_still_raises(self) -> None:
        driver = CapturingDriver()
        writer = LanguageCodeWriter(driver, database="missing", batch_size=100)
        with self.assertRaises(ValueError):
            asyncio.run(writer.write_all(
                projects=[{"id": "p1", "name": "p1"}],
                functions=[{"id": FN_ID, "name": "DemoProc"}],
                relations=[{
                    "source_id": FN_ID,
                    "target_id": "Ghost@nowhere",
                    "rel_type": "USES",
                    "properties": {},
                }],
                use_full_writers=True,
            ))


if __name__ == "__main__":
    unittest.main()
