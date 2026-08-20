"""Tests for the local/remote backend selection wizard in ``dev init``.

These cover the flow added by plan ``260820-dev-init-backend-selection``:
a fresh project prompts for ``storage_backend`` (``local`` | ``remote``),
and only prompts for the remote endpoint fields when ``remote`` is chosen.
Re-init must default the prompt to the previously stored value so an
operator never silently downgrades a remote project back to local.
"""

import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from cortex_harness.dev import cli
from cortex_harness.storage.config import validate_backend_config


class DevInitStorageBackendTests(unittest.TestCase):
    # Common prompt offsets (see also ``test_dev_init_graph_provider.py``):
    #   0  project code          1  project name
    #   2  storage backend       3  CORTEX_STORAGE_INSTANCE
    #   4  CORTEX_DATA_HOME      5  code GRAPH_PROVIDER
    #   6  code FALKORDB_GRAPH   7  code QDRANT_COLLECTION
    #   8  code EMBEDDING_MODEL  9  code BATCH_SIZE
    #  10  code MAX_EMBED_CHARS 11  code device
    #  12  doc  GRAPH_PROVIDER  13  doc  FALKORDB_GRAPH
    #  14  doc  EMBEDDING_MODEL 15  doc  BATCH_SIZE
    #  16  doc  MAX_EMBED_CHARS 17  doc  device
    #  18  code git              19  code folders
    #  20  doc  git              21  doc  folders
    # Remote inserts a sub-flow after the backend prompt:
    #   3  qdrant_url            4  qdrant_api_key
    #   5  falkordb_uri          6  falkordb_password
    #   7  falkordb_ssl
    _LOCAL_TAIL = [""] * 60

    def _write_existing_config(self, project_path: Path, env: str, cfg: dict) -> Path:
        cfg_dir = project_path / ".cortext-harness" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        path = cfg_dir / f"{env}.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def _config_path(self, project_path: Path, env: str = "dev") -> Path:
        return project_path / ".cortext-harness" / "config" / f"{env}.json"

    # ---------------------------------------------------------------------
    # Default local
    # ---------------------------------------------------------------------
    def test_default_local_writes_no_remote_section(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            result = runner.invoke(cli, ["init", str(project_path)], input="\n" * 60)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn("Remote backend", result.output)
            self.assertNotIn("Qdrant URL", result.output)
            self.assertNotIn("FalkorDB URI", result.output)

            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["storage_backend"], "local")
            self.assertNotIn("remote", cfg)
            self.assertNotIn(
                "Remote backend — Enter at least one endpoint",
                result.output,
            )

    # ---------------------------------------------------------------------
    # Remote full configuration
    # ---------------------------------------------------------------------
    def test_remote_full_configuration_passes_validator(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "SHOP",            # project code
                "",                # project name -> defaults to SHOP
                "remote",          # storage backend
                "http://qdrant.local:6333",  # qdrant_url
                "qkey-secret",     # qdrant_api_key
                "redis://falkor.local:6379", # falkordb_uri
                "fpass-secret",    # falkordb_password
                "y",               # falkordb_ssl
            ] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("storage_backend=remote", result.output)
            self.assertIn("infra-up", result.output)

            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["storage_backend"], "remote")
            remote = cfg["remote"]
            self.assertEqual(remote["qdrant_url"], "http://qdrant.local:6333")
            self.assertEqual(remote["qdrant_api_key"], "qkey-secret")
            self.assertEqual(remote["falkordb_uri"], "redis://falkor.local:6379")
            self.assertEqual(remote["falkordb_password"], "fpass-secret")
            self.assertTrue(remote["falkordb_ssl"])

            # validate_backend_config must accept the section unchanged.
            mode, parsed = validate_backend_config("remote", remote)
            self.assertEqual(mode.value, "remote")
            self.assertEqual(parsed.qdrant_url, "http://qdrant.local:6333")
            self.assertEqual(parsed.falkordb_uri, "redis://falkor.local:6379")

    # ---------------------------------------------------------------------
    # Remote only Qdrant / only FalkorDB
    # ---------------------------------------------------------------------
    def test_remote_only_qdrant_is_accepted(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "QDRANT_ONLY",      # project code
                "",                 # project name
                "remote",           # storage backend
                "http://qdrant.local:6333",  # qdrant_url
                "",                 # qdrant_api_key
                "",                 # falkordb_uri  (skip)
                "",                 # falkordb_password
                "n",                # falkordb_ssl
            ] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["storage_backend"], "remote")
            remote = cfg["remote"]
            self.assertEqual(remote["qdrant_url"], "http://qdrant.local:6333")
            self.assertNotIn("qdrant_api_key", remote)
            self.assertNotIn("falkordb_uri", remote)
            self.assertNotIn("falkordb_password", remote)
            self.assertFalse(remote["falkordb_ssl"])
            mode, parsed = validate_backend_config("remote", remote)
            self.assertEqual(mode.value, "remote")

    def test_remote_only_falkordb_is_accepted(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "FALKOR_ONLY",      # project code
                "",                 # project name
                "remote",           # storage backend
                "",                 # qdrant_url  (skip)
                "",                 # qdrant_api_key
                "redis://falkor.local:6379",  # falkordb_uri
                "secret",           # falkordb_password
                "y",                # falkordb_ssl
            ] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["storage_backend"], "remote")
            remote = cfg["remote"]
            self.assertNotIn("qdrant_url", remote)
            self.assertEqual(remote["falkordb_uri"], "redis://falkor.local:6379")
            self.assertEqual(remote["falkordb_password"], "secret")
            self.assertTrue(remote["falkordb_ssl"])

    # ---------------------------------------------------------------------
    # Remote with both URLs empty must be rejected
    # ---------------------------------------------------------------------
    def test_remote_without_any_url_is_rejected(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "BAD",      # project code
                "",         # project name
                "remote",   # storage backend
                "",         # qdrant_url
                "",         # qdrant_api_key
                "",         # falkordb_uri
                "",         # falkordb_password
                "n",        # falkordb_ssl
                "n",        # "Retry remote fields?" -> no
            ]
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertNotEqual(result.exit_code, 0, result.output)
            self.assertIn("remote config must specify", result.output)
            # Config file must NOT exist when validation rejects the section.
            self.assertFalse(self._config_path(project_path).exists())

    # ---------------------------------------------------------------------
    # Re-init must reuse the prior storage_backend + remote section
    # ---------------------------------------------------------------------
    def test_reinit_remote_reuses_remote_defaults(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            existing = {
                "active": True,
                "project": {"code": "SHOP", "name": "Shop"},
                "storage_backend": "remote",
                "remote": {
                    "qdrant_url": "http://qdrant.prev:6333",
                    "qdrant_api_key": "old-qkey",
                    "falkordb_uri": "redis://falkor.prev:6379",
                    "falkordb_password": "old-fpass",
                    "falkordb_ssl": True,
                },
                "code": {"env": {}, "source": {"projects": []}},
                "doc":  {"env": {}, "source": {"projects": []}},
            }
            self._write_existing_config(project_path, "dev", existing)

            # All-empty inputs: every prompt should accept its prior default.
            result = runner.invoke(cli, ["init", str(project_path)], input="\n" * 60)

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["storage_backend"], "remote")
            self.assertEqual(cfg["remote"]["qdrant_url"], "http://qdrant.prev:6333")
            self.assertEqual(cfg["remote"]["qdrant_api_key"], "old-qkey")
            self.assertEqual(cfg["remote"]["falkordb_uri"], "redis://falkor.prev:6379")
            self.assertEqual(cfg["remote"]["falkordb_password"], "old-fpass")
            self.assertTrue(cfg["remote"]["falkordb_ssl"])

    def test_reinit_local_after_remote_keeps_local_choice(self):
        """Re-init of an already-remote project that switches back to local.

        Ensures ``existing_backend`` is honored on the very first prompt so an
        operator who explicitly types ``local`` does not get prompted twice.
        """
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            existing = {
                "active": True,
                "project": {"code": "SHOP", "name": "Shop"},
                "storage_backend": "remote",
                "remote": {"qdrant_url": "http://qdrant.prev:6333"},
                "code": {"env": {}, "source": {"projects": []}},
                "doc":  {"env": {}, "source": {"projects": []}},
            }
            self._write_existing_config(project_path, "dev", existing)

            # The first non-empty answer is "local" — must switch and finish
            # without ever prompting the remote sub-flow.
            inputs = ["", "", "local"] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn("Remote backend", result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["storage_backend"], "local")
            self.assertNotIn("remote", cfg)

    # ---------------------------------------------------------------------
    # Secrets must not leak into the click output / config "tip" lines
    # ---------------------------------------------------------------------
    def test_secrets_not_echoed_to_output(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            qkey = "qkey-MUST-NOT-LEAK-123"
            fpass = "fpass-MUST-NOT-LEAK-456"
            inputs = [
                "SHOP",
                "",
                "remote",
                "http://qdrant.local:6333",
                qkey,
                "redis://falkor.local:6379",
                fpass,
                "n",
            ] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn(qkey, result.output)
            self.assertNotIn(fpass, result.output)
            # And the printed RemoteStorageConfig repr (if any) must also redact.
            self.assertNotIn("qkey-MUST-NOT-LEAK", result.output)

    # ---------------------------------------------------------------------
    # When remote is chosen the local-storage prompts must be skipped
    # ---------------------------------------------------------------------
    def test_remote_path_skips_local_storage_prompts(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "REMOTE_PROJECT",
                "",
                "remote",
                "http://qdrant.local:6333",
                "",
                "redis://falkor.local:6379",
                "",
                "n",
            ] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn("CORTEX_STORAGE_INSTANCE", result.output)
            self.assertNotIn("CORTEX_DATA_HOME", result.output)
            self.assertNotIn("Local storage", result.output)

            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            for section in ("code", "doc"):
                env = cfg[section]["env"]
                self.assertNotIn("CORTEX_STORAGE_INSTANCE", env)
                self.assertNotIn("CORTEX_DATA_HOME", env)


if __name__ == "__main__":
    unittest.main()
