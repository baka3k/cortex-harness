"""dev sync doc YAKE wiring (plan 260924-1642-yake-dynamic-rules-doc-sync phase 03).

Covers: default-on flag propagation (D5), doc.env YAKE_* overrides, the
group-level --yake/--no-yake option placed before the subcommand (M5), the
per-project rules dir (D2/C3) and rule-file pruning on deleted docs (D8/M1).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from cortex_harness.dev import (
    _doc_file_hash,
    _save_state,
    _sync_doc_folder,
    cli,
)


def _write_config(root: Path, source: Path, doc_env: dict | None = None) -> None:
    config_path = root / ".cortext-harness" / "config" / "dev.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "active": True,
            "project": {"code": "shop", "name": "Shop"},
            "code": {
                "env": {
                    "GRAPH_PROVIDER": "falkordb",
                    "CODE_GRAPH_PROVIDER": "falkordb",
                    "FALKORDB_GRAPH": "shop",
                    "QDRANT_COLLECTION": "shop",
                    "CORTEX_DATA_HOME": str(root / "data"),
                },
                "source": {"projects": [{"git": "", "folder": [str(source)]}]},
            },
            "doc": {
                "env": {
                    "GRAPH_PROVIDER": "falkordb",
                    "DOC_GRAPH_PROVIDER": "falkordb",
                    "FALKORDB_GRAPH": "shop_doc",
                    "CORTEX_DATA_HOME": str(root / "data"),
                    **(doc_env or {}),
                },
                "source": {"projects": [{"git": "", "folder": [str(source)]}]},
            },
        }),
        encoding="utf-8",
    )


def _make_docs_source(root: Path) -> Path:
    source = root / "docs"
    source.mkdir()
    (source / "guide.md").write_text("# Guide\n", encoding="utf-8")
    return source


class SyncDocYakeFlagTests(unittest.TestCase):
    def test_sync_doc_default_enables_yake(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _make_docs_source(root)
            _write_config(root, source)

            with patch("cortex_harness.dev._venv_python", return_value="/fake/python"):
                result = runner.invoke(
                    cli,
                    ["sync", "doc", "--project-dir", str(root), "--dry-run"],
                    input="\n",
                )

        self.assertEqual(result.exit_code, 0, result.output)
        # space-delimited: a bare assertIn("--yake-rules") would also match
        # the always-present "--yake-rules-dir" and prove nothing
        self.assertIn(" --yake-rules ", result.output)
        self.assertIn("--yake-language en", result.output)
        self.assertIn("--yake-top 150", result.output)
        self.assertIn(f"rules/from-yake/shop", result.output)

    def test_sync_doc_env_disables_yake(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _make_docs_source(root)
            _write_config(root, source, doc_env={"YAKE_ENABLED": "0"})

            with patch("cortex_harness.dev._venv_python", return_value="/fake/python"):
                result = runner.invoke(
                    cli,
                    ["sync", "doc", "--project-dir", str(root), "--dry-run"],
                    input="\n",
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--no-yake-rules", result.output)
        self.assertNotIn(" --yake-rules ", result.output)
        self.assertIn("yake   : off", result.output)

    def test_sync_doc_cli_override(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _make_docs_source(root)
            _write_config(root, source, doc_env={"YAKE_ENABLED": "0"})

            with patch("cortex_harness.dev._venv_python", return_value="/fake/python"):
                # group options must precede the subcommand (M5)
                enabled = runner.invoke(
                    cli,
                    ["sync", "doc", "--project-dir", str(root), "--yake", "--dry-run", "all"],
                )

        self.assertEqual(enabled.exit_code, 0, enabled.output)
        self.assertIn(" --yake-rules ", enabled.output)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _make_docs_source(root)
            _write_config(root, source)

            with patch("cortex_harness.dev._venv_python", return_value="/fake/python"):
                disabled = runner.invoke(
                    cli,
                    ["sync", "doc", "--project-dir", str(root), "--no-yake", "--dry-run", "all"],
                )

        self.assertEqual(disabled.exit_code, 0, disabled.output)
        self.assertIn("--no-yake-rules", disabled.output)

    def test_sync_doc_env_params_forwarded(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _make_docs_source(root)
            _write_config(
                root, source,
                doc_env={"YAKE_LANGUAGE": "vi", "YAKE_TOP": "80", "YAKE_MAX_NGRAM": "2"},
            )

            with patch("cortex_harness.dev._venv_python", return_value="/fake/python"):
                result = runner.invoke(
                    cli,
                    ["sync", "doc", "--project-dir", str(root), "--dry-run"],
                    input="\n",
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--yake-language vi", result.output)
        self.assertIn("--yake-top 80", result.output)
        self.assertIn("--yake-max-ngram 2", result.output)

    def test_sync_doc_all_same_flags(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _make_docs_source(root)
            _write_config(root, source)

            with patch("cortex_harness.dev._venv_python", return_value="/fake/python"):
                result = runner.invoke(
                    cli,
                    ["sync", "doc", "--project-dir", str(root), "--dry-run", "all"],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(" --yake-rules ", result.output)
        self.assertIn("--yake-rules-dir", result.output)


class DeletedDocsPruneTests(unittest.TestCase):
    def _run_folder_sync(self, root: Path, dry_run: bool) -> list:
        env = {
            "GRAPH_PROVIDER": "falkordb",
            "DOC_GRAPH_PROVIDER": "falkordb",
            "FALKORDB_GRAPH": "shop_doc",
            "CORTEX_DATA_HOME": str(root / "data"),
            "QDRANT_DOC_PATH": str(root / "data" / "qdrant" / "doc"),
        }
        prune_cmds: list = []
        with patch("cortex_harness.dev._run_with_retry", return_value=0), patch(
            "cortex_harness.dev.subprocess.run", side_effect=lambda cmd, **kw: (
                prune_cmds.append(cmd),
                type("P", (), {"returncode": 0, "stderr": "", "stdout": ""})(),
            )[1]
        ):
            _sync_doc_folder(
                project_path=root,
                folder="docs",
                env=env,
                python="/fake/python",
                project={"code": "shop", "name": "Shop"},
                force_mode="incremental",
                entity_provider="gliner",
                dry_run=dry_run,
                preview=False,
                yake=True,
            )
        return prune_cmds

    def test_deleted_docs_prune_rule_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = root / "docs"
            docs.mkdir()
            keep = docs / "keep.md"
            keep.write_text("# keep\n", encoding="utf-8")
            gone = docs / "gone.md"
            gone.write_text("# gone\n", encoding="utf-8")

            _save_state(root, "doc:docs", {
                "folder": "docs",
                "git_commit": "",
                "file_hashes": {
                    "keep.md": _doc_file_hash(keep),
                    "gone.md": _doc_file_hash(gone),
                },
                "file_count": 2,
            })
            gone.unlink()

            prune_cmds = self._run_folder_sync(root, dry_run=False)

            # _git_head also shells out during state save; keep only prune calls
            yake_cmds = [c for c in prune_cmds if "yake_rules.py" in str(c[1])]
            self.assertEqual(len(yake_cmds), 1)
            cmd = yake_cmds[0]
            self.assertTrue(str(cmd[1]).endswith("yake_rules.py"), cmd)
            self.assertIn("--rules-dir", cmd)
            rules_dir_index = cmd.index("--rules-dir") + 1
            self.assertIn("rules/from-yake/shop", cmd[rules_dir_index])
            self.assertIn("--prune-sources", cmd)
            # both id spellings: folder-mode relpath id + single-file stem id
            self.assertIn("gone.md", cmd)
            self.assertIn("gone", cmd)

    def test_prune_keeps_stem_of_surviving_file(self):
        """Deleting sub/gone.md must not prune the stem-keyed rules of a kept
        top-level gone.md (reviewer finding: stem-spelling collision)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = root / "docs"
            (docs / "sub").mkdir(parents=True)
            keep_top = docs / "gone.md"
            keep_top.write_text("# gone top\n", encoding="utf-8")
            nested = docs / "sub" / "gone.md"
            nested.write_text("# gone nested\n", encoding="utf-8")

            _save_state(root, "doc:docs", {
                "folder": "docs",
                "git_commit": "",
                "file_hashes": {
                    "gone.md": _doc_file_hash(keep_top),
                    "sub/gone.md": _doc_file_hash(nested),
                },
                "file_count": 2,
            })
            nested.unlink()

            prune_cmds = self._run_folder_sync(root, dry_run=False)

            yake_cmds = [c for c in prune_cmds if "yake_rules.py" in str(c[1])]
            self.assertEqual(len(yake_cmds), 1)
            cmd = yake_cmds[0]
            prune_args = cmd[cmd.index("--prune-sources") + 1:]
            # the relpath spelling of the deleted file is pruned...
            self.assertIn("sub__gone.md", prune_args)
            # ...but the stem spelling of the SURVIVING top-level file is kept
            self.assertNotIn("gone", prune_args)

    def test_dry_run_does_not_prune(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = root / "docs"
            docs.mkdir()
            keep = docs / "keep.md"
            keep.write_text("# keep\n", encoding="utf-8")
            gone = docs / "gone.md"
            gone.write_text("# gone\n", encoding="utf-8")

            _save_state(root, "doc:docs", {
                "folder": "docs",
                "git_commit": "",
                "file_hashes": {
                    "keep.md": _doc_file_hash(keep),
                    "gone.md": _doc_file_hash(gone),
                },
                "file_count": 2,
            })
            gone.unlink()

            prune_cmds = self._run_folder_sync(root, dry_run=True)

            yake_cmds = [c for c in prune_cmds if "yake_rules.py" in str(c[1])]
            self.assertEqual(yake_cmds, [])


if __name__ == "__main__":
    unittest.main()
