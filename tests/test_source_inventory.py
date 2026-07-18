import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.source_inventory import (  # noqa: E402
    SourceChangedError,
    capture_source_inventory,
    diff_source_inventories,
    load_inventory_generation,
    validate_inventory_unchanged,
    write_inventory_generation,
)


class SourceInventoryTests(unittest.TestCase):
    def test_inventory_detects_content_change_delete_and_add(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root, "first.py")
            deleted = Path(root, "deleted.py")
            first.write_text("one\n", encoding="utf-8")
            deleted.write_text("gone\n", encoding="utf-8")
            before = capture_source_inventory(root, {"first.py", "deleted.py"})

            first.write_text("two\n", encoding="utf-8")
            deleted.unlink()
            Path(root, "added.py").write_text("new\n", encoding="utf-8")
            after = capture_source_inventory(
                root,
                {"first.py", "added.py"},
                previous=before,
                force_hash_paths={"first.py", "added.py"},
            )

            changed, removed = diff_source_inventories(before, after)
            self.assertEqual(changed, {"first.py", "added.py"})
            self.assertEqual(removed, {"deleted.py"})

    def test_generation_round_trip_is_immutable_and_deterministic(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "main.py").write_text("print('ok')\n", encoding="utf-8")
            inventory = capture_source_inventory(root, {"main.py"})
            target = write_inventory_generation(Path(root, ".cache"), inventory)
            second_target = write_inventory_generation(Path(root, ".cache"), inventory)

            self.assertEqual(target, second_target)
            self.assertEqual(load_inventory_generation(target), inventory)

    def test_validation_rejects_mid_run_change(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "main.py")
            path.write_text("before\n", encoding="utf-8")
            inventory = capture_source_inventory(root, {"main.py"})
            path.write_text("after\n", encoding="utf-8")

            with self.assertRaises(SourceChangedError):
                validate_inventory_unchanged(root, inventory, {"main.py"})

    def test_force_hash_catches_same_size_preserved_mtime_change(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "main.py")
            path.write_text("aaaa", encoding="utf-8")
            before = capture_source_inventory(root, {"main.py"})
            stat = path.stat()
            path.write_text("bbbb", encoding="utf-8")
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            after = capture_source_inventory(
                root,
                {"main.py"},
                previous=before,
                force_hash_paths={"main.py"},
            )
            changed, _ = diff_source_inventories(before, after)
            self.assertEqual(changed, {"main.py"})


if __name__ == "__main__":
    unittest.main()
