from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.batch_chain_linker import build_cplus_stem_index, link_shell_calls_to_cplus


SHELL_RELATIONS = [
    {
        "source_id": "file::script",
        "source_label": "File",
        "target_id": "file::BBSEAB02.sh",
        "target_label": "File",
        "rel_type": "CALLS",
        "properties": {"callee_ref": "./BBSEAB02.sh", "line": "10"},
    },
    {
        "source_id": "file::script",
        "source_label": "File",
        "target_id": "file::BZZAAB02",
        "target_label": "File",
        "rel_type": "CALLS",
        "properties": {"callee_ref": "${CONFIG_DIR}/BZZAAB02", "line": "11"},
    },
    {
        "source_id": "file::script",
        "source_label": "File",
        "target_id": "cfg::x",
        "target_label": "ConfigEntry",
        "rel_type": "READS_CONFIG",
        "properties": {"config_key": "X"},
    },
]

CPLUS_FILE_PATHS = [
    "02.Cソース/BZZAAB01.c",
    "02.Cソース/BZZAAB02.pc",
]


class BatchChainLinkerTest(unittest.TestCase):
    def test_build_cplus_stem_index(self):
        index = build_cplus_stem_index(CPLUS_FILE_PATHS)
        self.assertEqual(index["BZZAAB01"], "02.Cソース/BZZAAB01.c")
        self.assertEqual(index["BZZAAB02"], "02.Cソース/BZZAAB02.pc")

    def test_link_shell_calls_to_cplus_resolves_matching_stem(self):
        resolved = link_shell_calls_to_cplus(SHELL_RELATIONS, CPLUS_FILE_PATHS)
        self.assertEqual(len(resolved), 1)
        rel = resolved[0]
        self.assertEqual(rel["rel_type"], "CALLS")
        self.assertEqual(rel["properties"]["matched_path"], "02.Cソース/BZZAAB02.pc")
        self.assertEqual(rel["properties"]["resolved_via"], "batch_chain_linker")

    def test_no_cplus_files_returns_empty(self):
        self.assertEqual(link_shell_calls_to_cplus(SHELL_RELATIONS, []), [])

    def test_non_calls_relations_ignored(self):
        resolved = link_shell_calls_to_cplus(
            [r for r in SHELL_RELATIONS if r["rel_type"] != "CALLS"], CPLUS_FILE_PATHS
        )
        self.assertEqual(resolved, [])


if __name__ == "__main__":
    unittest.main()
