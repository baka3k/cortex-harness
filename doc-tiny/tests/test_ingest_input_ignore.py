"""``_iter_input_files`` directory excludes (plan 260909-0854 phase 04).

The doc full-sync walker must skip files inside default excluded dirs
(``node_modules``, ``.git``, ...) and inside user-configured
``CORTEX_EXTRA_IGNORE_DIRS`` folders, while keeping the pre-existing
file-level filters (``~$`` office temp + extension allowlist) intact.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_DOC_TINY = Path(__file__).resolve().parents[1]


def _load_ingestor():
    spec = importlib.util.spec_from_file_location(
        "graphrag_ingest_langextract_under_test",
        _DOC_TINY / "graphrag_ingest_langextract.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ingestor = _load_ingestor()

ENV_VAR = "CORTEX_EXTRA_IGNORE_DIRS"


class IterInputFilesIgnoreTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from tools.common.scan_ignore import reset_extra_ignore_dirs
        except Exception:
            self.fail("code-tiny scan_ignore must be importable from doc-tiny tests")
        reset_extra_ignore_dirs()
        self.addCleanup(reset_extra_ignore_dirs)

    def _write(self, root: Path, rel: str, content: str = "# x\n") -> Path:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_iter_input_files_skips_common_excluded_dirs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write(root, "docs/keep.md")
            self._write(root, "node_modules/pkg/readme.md")
            self._write(root, ".git/notes.md")

            found = ingestor._iter_input_files(root)

            self.assertEqual([f.relative_to(root).as_posix() for f in found],
                             ["docs/keep.md"])

    def test_iter_input_files_skips_env_extra_ignore_dirs(self):
        with unittest.mock.patch.dict("os.environ", {ENV_VAR: "archive"}):
            from tools.common.scan_ignore import reset_extra_ignore_dirs
            reset_extra_ignore_dirs()
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self._write(root, "docs/keep.md")
                self._write(root, "archive/old.md")

                found = ingestor._iter_input_files(root)

                self.assertEqual(
                    [f.relative_to(root).as_posix() for f in found],
                    ["docs/keep.md"],
                )

    def test_iter_input_files_without_env_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            keep = self._write(root, "docs/keep.md")
            self._write(root, "docs/~$temp.docx", "temp")     # office temp
            self._write(root, "docs/data.csv", "a,b")          # unsupported ext

            found = ingestor._iter_input_files(root)

            self.assertEqual(found, [keep])

    def test_iter_input_files_supports_user_globs(self):
        with unittest.mock.patch.dict("os.environ", {ENV_VAR: "generated-*"}):
            from tools.common.scan_ignore import reset_extra_ignore_dirs
            reset_extra_ignore_dirs()
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self._write(root, "docs/keep.md")
                self._write(root, "generated-ui/gen.md")

                found = ingestor._iter_input_files(root)

                self.assertEqual(
                    [f.relative_to(root).as_posix() for f in found],
                    ["docs/keep.md"],
                )


if __name__ == "__main__":
    unittest.main()
