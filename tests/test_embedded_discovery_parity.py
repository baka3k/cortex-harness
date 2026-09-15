"""Embedded-FalkorDB discovery parity on a SYNTHETIC process table.

The live-mock approach in scripts/rust_parity/dev_cli_parity.py GATE 7 is
sandbox-hostile (non-python children are SIGKILLed), so the strong check runs
here: both engines receive the same fabricated process record and must agree
on the pid that owns the fake RDB. No live processes required.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_BIN = REPO_ROOT / "rust" / "target" / "debug" / "cortex-dev"


class EmbeddedDiscoveryParityTests(unittest.TestCase):
    def test_synthetic_redis_config_discovery_matches(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emb-parity-") as directory:
            side = Path(directory)
            config = side / "redis.config"
            config.write_text(f"dir {side}\ndbfilename code.rdb\n", encoding="utf-8")
            db_path = side / "code.rdb"
            argv = [
                str(side / "redis-server-mock"),
                "-f",
                str(config),
                "extra-token",
            ]

            # Python engine: sync_processes.embedded_falkordb_pids with a
            # synthetic process table (psutil is bypassed via `processes=`).
            from cortex_harness.sync_processes import (
                ProcessRecord,
                embedded_falkordb_pids,
            )

            table = {9999: ProcessRecord(pid=9999, ppid=1, argv=tuple(argv))}
            py_pids = embedded_falkordb_pids(db_path, processes=table)

            # Rust engine: the parity hook builds the same synthetic record.
            env = dict(os.environ)
            env["CORTEX_DEV_PARITY_ENV"] = "procinfo_synth"
            env["CORTEX_DEV_PARITY_DB"] = str(db_path)
            env["CORTEX_DEV_PARITY_ARGV"] = "|".join(argv)
            env["CORTEX_HARNESS_REPO_ROOT"] = str(REPO_ROOT)
            completed = subprocess.run(
                [str(RUST_BIN)], capture_output=True, text=True, env=env, check=False
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            import json

            rs_pids = json.loads(completed.stdout)["pids"]

            self.assertEqual(py_pids, [9999], "python discovery must match the mock")
            self.assertEqual(rs_pids, [9999], "rust discovery must match the mock")


if __name__ == "__main__":
    unittest.main()
