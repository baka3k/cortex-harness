import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.incremental_sync_state import (  # noqa: E402
    STATE_SCHEMA_VERSION,
    backup_legacy_state,
    load_sync_state,
    mark_clean,
    state_file_path,
)


class IncrementalSyncStateMigrationTests(unittest.TestCase):
    def test_shared_cache_namespaces_state_by_project_and_root(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_path = state_file_path(cache, "same-project", first)
            second_path = state_file_path(cache, "same-project", second)
            self.assertNotEqual(first_path, second_path)

    def test_v1_state_requires_conservative_bootstrap_and_retains_sha(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "state.json")
            path.write_text(
                json.dumps(
                    {
                        "project_id": "project",
                        "root": root,
                        "last_good_sha": "abc123",
                        "dirty": False,
                    }
                ),
                encoding="utf-8",
            )

            state = load_sync_state(str(path), "project", root)
            self.assertEqual(state.schema_version, STATE_SCHEMA_VERSION)
            self.assertEqual(state.last_good_sha, "abc123")
            self.assertTrue(state.migration_required)
            self.assertEqual(state.migrated_from, 1)

            backup = backup_legacy_state(str(path), state)
            self.assertTrue(Path(backup).exists())
            mark_clean(
                str(path),
                state,
                last_good_sha="def456",
                before_sha="abc123",
                after_sha="def456",
                snapshot_id="snapshot",
                inventory_path="inventory.json",
                repositories={".": {"last_good_sha": "def456"}},
                working_tree_paths=[],
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], STATE_SCHEMA_VERSION)
            self.assertFalse(saved["migration_required"])
            self.assertEqual(saved["snapshot_id"], "snapshot")


if __name__ == "__main__":
    unittest.main()
