"""Phase 03 Pro*C artifact/context manifest, dependency index, cache
fingerprint, and exact invalidation/downgrade contract tests."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.proc_manifest import (  # noqa: E402
    PROC_MANIFEST_VERSION,
    ProcDependencyIndex,
    build_proc_manifest,
    classify_proc_downgrade,
    extract_exec_sql_includes,
    normalized_option_fingerprint,
    proc_cache_fingerprint,
    redact_proc_option,
)

SAMPLE_PC = """#include <stdio.h>
EXEC SQL INCLUDE sqlca;
EXEC SQL INCLUDE myapp_common;
int main() {
    EXEC SQL SELECT count(*) INTO :n FROM orders;
    return 0;
}
"""


def _write_proc_tree(tmp: str) -> dict:
    root = Path(tmp)
    (root / "src").mkdir(exist_ok=True)
    original = root / "src" / "sample.pc"
    original.write_text(SAMPLE_PC)
    generated = root / "src" / "sample.c"
    generated.write_text("int main(){return 0;}\n")
    source_map = root / "src" / "sample.map"
    source_map.write_text("original\\tgenerated\\n")
    return {
        "original": str(original),
        "generated": str(generated),
        "map": str(source_map),
    }


class ProcManifestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = _write_proc_tree(self._tmp.name)

    def _manifest(self, **overrides):
        kwargs = dict(
            root=self._tmp.name,
            original_path=self.paths["original"],
            generated_path=self.paths["generated"],
            source_map_id="map-1",
            source_map_path=self.paths["map"],
            language_mode="c",
            precompiler_identity="proc 12.2",
            options=["CODE=CPP", "USERID=scott/tiger"],
        )
        kwargs.update(overrides)
        return build_proc_manifest(**kwargs)

    def test_manifest_hashes_all_layers_and_is_redacted(self):
        manifest = self._manifest()
        self.assertTrue(manifest.eligible)
        data = manifest.to_json()
        self.assertEqual(data["version"], PROC_MANIFEST_VERSION)
        self.assertNotIn("scott", data["option_fingerprint"])
        self.assertNotIn("tigger", str(data))
        self.assertNotIn("scott", str(data))
        self.assertTrue(manifest.original_sha256)
        self.assertTrue(manifest.generated_sha256)
        self.assertTrue(manifest.source_map_sha256)

    def test_redacted_option_fingerprint_stable_and_credential_free(self):
        self.assertEqual(redact_proc_option("USERID=scott/tiger"), "userid=<redacted>")
        first = normalized_option_fingerprint(["USERID=scott/tiger", "CODE=CPP"])
        second = normalized_option_fingerprint(["CODE=CPP", "USERID=other/secret"])
        self.assertEqual(first, second)  # credential value changes do not matter

    def test_exec_sql_include_resolution(self):
        includes = extract_exec_sql_includes(self.paths["original"])
        self.assertIn("myapp_common", includes)
        self.assertNotIn("sqlca", includes)

    def test_missing_generated_layer_is_stale_and_ineligible(self):
        Path(self.paths["generated"]).unlink()
        manifest = self._manifest()
        self.assertFalse(manifest.eligible)
        self.assertEqual(
            classify_proc_downgrade(manifest, changed_files=[manifest.original_rel_path]),
            "sql_only",
        )

    def test_fingerprint_changes_for_every_reviewed_input(self):
        baseline = self._manifest()
        changed = {
            "original edit": self._manifest(),
            "generated edit": self._manifest(),
            "map id": self._manifest(source_map_id="map-2"),
            "language mode": self._manifest(language_mode="cpp"),
            "precompiler": self._manifest(precompiler_identity="proc 19"),
            "options": self._manifest(options=["CODE=ANSI_C"]),
        }
        Path(self.paths["original"]).write_text(SAMPLE_PC + "\n")
        changed["original edit"] = self._manifest()
        Path(self.paths["generated"]).write_text("int main(){return 1;}\n")
        changed["generated edit"] = self._manifest()
        for name, manifest in changed.items():
            self.assertNotEqual(
                baseline.fingerprint(), manifest.fingerprint(), name
            )

    def test_cache_fingerprint_separates_compiler_context(self):
        manifest = self._manifest()
        self.assertNotEqual(
            proc_cache_fingerprint(manifest, "cfg-a"),
            proc_cache_fingerprint(manifest, "cfg-b"),
        )


class ProcDependencyIndexTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = _write_proc_tree(self._tmp.name)

    def _index(self) -> ProcDependencyIndex:
        manifest = build_proc_manifest(
            root=self._tmp.name,
            original_path=self.paths["original"],
            generated_path=self.paths["generated"],
            source_map_id="map-1",
            source_map_path=self.paths["map"],
            language_mode="c",
            precompiler_identity="proc 12.2",
            options=["CODE=CPP"],
        )
        index = ProcDependencyIndex()
        index.add_manifest(manifest, generated_dependencies=["inc/common.h"])
        return index, manifest

    def test_exact_invalidation_covers_whole_replacement_set(self):
        index, manifest = self._index()
        # Real watcher/git paths only — no synthetic keys.
        impacted = index.impacted_originals(
            [
                manifest.original_rel_path,
                manifest.generated_rel_path,
                "src/sample.map",
                "inc/common.h",
                "inc/myapp_common.h",
            ]
        )
        self.assertEqual(impacted, {manifest.original_rel_path})
        self.assertEqual(
            index.impacted_originals(["src/unrelated.c"]), set()
        )

    def test_sqlcheck_keeps_semantic_identity(self):
        # SQLCHECK changes generated code; it must not be credential-redacted.
        first = normalized_option_fingerprint(["SQLCHECK=SYNTAX"])
        second = normalized_option_fingerprint(["SQLCHECK=FULL"])
        self.assertNotEqual(first, second)
        self.assertEqual(redact_proc_option("SQLCHECK=FULL"), "SQLCHECK=FULL")

    def test_downgrade_from_semantic_complete_to_sql_only(self):
        index, manifest = self._index()
        self.assertEqual(
            classify_proc_downgrade(manifest, changed_files=["other.c"]),
            "unchanged",
        )
        self.assertEqual(
            classify_proc_downgrade(
                manifest, changed_files=[manifest.original_rel_path]
            ),
            "semantic_complete",
        )
        # Generated artifact replaced without regenerating the original layer:
        # publication must downgrade to SQL-only until layers realign.
        Path(self.paths["map"]).unlink()
        stale = build_proc_manifest(
            root=self._tmp.name,
            original_path=self.paths["original"],
            generated_path=self.paths["generated"],
            source_map_id="map-1",
            source_map_path=self.paths["map"],
            language_mode="c",
            precompiler_identity="proc 12.2",
            options=["CODE=CPP"],
        )
        self.assertFalse(stale.eligible)
        self.assertEqual(
            classify_proc_downgrade(stale, changed_files=[stale.generated_rel_path]),
            "sql_only",
        )

    def test_deleted_proc_source_stops_claiming_generated_unit(self):
        index, manifest = self._index()
        Path(self.paths["original"]).unlink()
        impacted = index.impacted_originals([manifest.original_rel_path])
        self.assertEqual(impacted, {manifest.original_rel_path})
        rebuilt = build_proc_manifest(
            root=self._tmp.name,
            original_path=self.paths["original"],
            generated_path=self.paths["generated"],
            source_map_id="map-1",
            source_map_path=self.paths["map"],
            language_mode="c",
            precompiler_identity="proc 12.2",
            options=["CODE=CPP"],
        )
        self.assertFalse(rebuilt.eligible)


if __name__ == "__main__":
    unittest.main()
