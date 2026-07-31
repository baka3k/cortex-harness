from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.jp1.jp1_analyzer import analyze_root
from tools.jp1.jp1_parser import build_relations, looks_like_jp1_unit_definition, parse_jp1_file


def _job_unit(suffix: str, from_suffix: str = None, to_suffixes=()) -> str:
    lines = [
        f"unit=JBCWV013_ALL-{suffix},,,;",
        "{",
        "  ty=j;",
        f'  cm="job {suffix}";',
        "  sz=100x100;",
        "  el=100,100,300,300;",
    ]
    for to_suffix in to_suffixes:
        lines.append(f"  ar=(f=JBCWV013_ALL-{suffix},t=JBCWV013_ALL-{to_suffix},pri);")
    lines.append(f'  te="@BOSAPDIR@/sh/ALL/BBSEAB{suffix}.sh";')
    lines.append("}")
    return "\n".join(lines)


JP1_FIXTURE = "\n".join(
    [
        "unit=JBCWV013_ALL,,,;",
        "{",
        "  ty=n;",
        '  cm="ALL batch jobnet";',
        "  sz=100x100;",
        "  el=100,100,300,300;",
        _job_unit("010", to_suffixes=("020",)),
        _job_unit("020", to_suffixes=("030", "040")),
        _job_unit("030", to_suffixes=("050",)),
        _job_unit("040", to_suffixes=("050",)),
        _job_unit("050", to_suffixes=("060",)),
        _job_unit("060", to_suffixes=("070",)),
        _job_unit("070", to_suffixes=("080",)),
        _job_unit("080"),
        "}",
        "",
    ]
)


class Jp1ParserTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.def_path = self.root / "JBCWV013_ALL.txt"
        self.def_path.write_text(JP1_FIXTURE, encoding="utf-8")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_content_sniff_detects_jp1_unit_definition(self):
        self.assertTrue(looks_like_jp1_unit_definition(JP1_FIXTURE))
        self.assertFalse(looks_like_jp1_unit_definition("just some plain text\nno unit here\n"))

    def test_units_extracted_with_correct_hierarchy(self):
        definition = parse_jp1_file(str(self.def_path), str(self.root))
        self.assertEqual(len(definition.units), 9)  # top-level jobnet + 8 sub-jobs
        top = next(u for u in definition.units if u.unit_id == "JBCWV013_ALL")
        self.assertEqual(top.unit_type, "n")
        self.assertIsNone(top.parent_id)
        job_010 = next(u for u in definition.units if u.unit_id == "JBCWV013_ALL-010")
        self.assertEqual(job_010.unit_type, "j")
        self.assertEqual(job_010.parent_id, "JBCWV013_ALL")
        self.assertEqual(job_010.comment, "job 010")

    def test_te_exec_command_decoded(self):
        definition = parse_jp1_file(str(self.def_path), str(self.root))
        job_010 = next(u for u in definition.units if u.unit_id == "JBCWV013_ALL-010")
        self.assertEqual(job_010.exec_command, "@BOSAPDIR@/sh/ALL/BBSEAB010.sh")

    def test_sequence_edges_extracted(self):
        definition = parse_jp1_file(str(self.def_path), str(self.root))
        job_020 = next(u for u in definition.units if u.unit_id == "JBCWV013_ALL-020")
        targets = {edge.to_unit for edge in job_020.sequence_edges}
        self.assertEqual(targets, {"JBCWV013_ALL-030", "JBCWV013_ALL-040"})

    def test_build_relations_emits_contains_precedes_executes(self):
        definition = parse_jp1_file(str(self.def_path), str(self.root))
        relations = build_relations(definition)
        rel_types = {r.rel_type for r in relations}
        self.assertEqual(rel_types, {"CONTAINS", "PRECEDES", "EXECUTES"})

        contains = [r for r in relations if r.rel_type == "CONTAINS"]
        self.assertEqual(len(contains), 8)  # jobnet -> each of the 8 sub-jobs

        precedes = [r for r in relations if r.rel_type == "PRECEDES"]
        self.assertEqual(len(precedes), 8)  # 8 ar= sequence edges

        executes = [r for r in relations if r.rel_type == "EXECUTES"]
        self.assertEqual(len(executes), 8)  # each ty=j unit has a te= command
        self.assertTrue(all(r.target_label == "File" for r in executes))
        self.assertTrue(all(r.properties["script_ref"].startswith("sh/ALL/") for r in executes))

    def test_analyze_root_discovers_and_counts(self):
        rows = analyze_root(str(self.root))
        self.assertEqual(len(rows["files"]), 1)
        self.assertEqual(len(rows["functions"]), 9)
        self.assertEqual(len(rows["relations"]), 24)

    def test_non_jp1_txt_file_ignored(self):
        (self.root / "readme.txt").write_text("just a readme\n", encoding="utf-8")
        rows = analyze_root(str(self.root))
        self.assertEqual(len(rows["files"]), 1)


if __name__ == "__main__":
    unittest.main()
