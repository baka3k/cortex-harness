"""Tests for the local/remote backend selection wizard in ``dev init``.

These cover the flow added by plan ``260820-dev-init-backend-selection``:
a fresh project prompts for ``storage_backend`` (``local`` | ``remote``),
and only prompts for the remote endpoint fields when ``remote`` is chosen.
Re-init must default the prompt to the previously stored value so an
operator never silently downgrades a remote project back to local.

Phase-04 tests verify the local-Docker defaults UX: pressing Enter on a
fresh remote wizard fills ``http://localhost:6333`` / ``localhost:6379``
and skips the credential prompts (localhost Docker has no auth).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from cortex_harness.dev import cli
from cortex_harness.storage.config import validate_backend_config


class DevInitStorageBackendTests(unittest.TestCase):
    # Prompt flow (Phase-04):
    #   Local:  code, name, backend, CORTEX_STORAGE_INSTANCE, CORTEX_DATA_HOME,
    #           code GRAPH_PROVIDER, code FALKORDB_GRAPH, ...
    #   Remote (localhost): code, name, backend, qdrant_url, falkordb_uri,
    #           code GRAPH_PROVIDER, ...  (NO credential prompts)
    #   Remote (non-localhost): code, name, backend, qdrant_url, falkordb_uri,
    #           qdrant_api_key, falkordb_password, falkordb_ssl,
    #           code GRAPH_PROVIDER, ...
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

    def test_blank_input_defaults_batch8_and_auto_device(self):
        """Phase 06/03: fresh init defaults BATCH_SIZE=8 and device=auto."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            result = runner.invoke(cli, ["init", str(project_path)], input="\n" * 60)

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["code"]["env"]["BATCH_SIZE"], "8")
            self.assertEqual(cfg["doc"]["env"]["BATCH_SIZE"], "8")
            # device resolves inside dev.py before any analyzer sees it
            self.assertEqual(cfg["code"]["env"]["device"], "auto")

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
                "http://qdrant.local:6333",  # qdrant_url (non-localhost)
                "redis://falkor.local:6379", # falkordb_uri (non-localhost)
                "qkey-secret",     # qdrant_api_key (prompted for non-local)
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
        """Explicit Qdrant URL + blank FalkorDB → falkordb gets localhost default."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "QDRANT_ONLY",      # project code
                "",                 # project name
                "remote",           # storage backend
                "http://qdrant.remote:6333",  # qdrant_url (non-localhost)
                "",                 # falkordb_uri → default localhost:6379
                "",                 # qdrant_api_key (prompted: qdrant is non-local)
                "",                 # falkordb_password
                "n",                # falkordb_ssl
            ] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["storage_backend"], "remote")
            remote = cfg["remote"]
            self.assertEqual(remote["qdrant_url"], "http://qdrant.remote:6333")
            # FalkorDB falls through to localhost default (Phase 04)
            self.assertEqual(remote["falkordb_uri"], "localhost:6379")
            self.assertFalse(remote["falkordb_ssl"])
            mode, parsed = validate_backend_config("remote", remote)
            self.assertEqual(mode.value, "remote")

    def test_remote_only_falkordb_is_accepted(self):
        """Explicit FalkorDB URI + blank Qdrant → qdrant gets localhost default."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "FALKOR_ONLY",      # project code
                "",                 # project name
                "remote",           # storage backend
                "",                 # qdrant_url → default http://localhost:6333
                "redis://falkor.remote:6379",  # falkordb_uri (non-localhost)
                "",                 # qdrant_api_key (prompted: falkordb is non-local)
                "secret",           # falkordb_password
                "y",                # falkordb_ssl
            ] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["storage_backend"], "remote")
            remote = cfg["remote"]
            # Qdrant falls through to localhost default (Phase 04)
            self.assertEqual(remote["qdrant_url"], "http://localhost:6333")
            self.assertEqual(remote["falkordb_uri"], "redis://falkor.remote:6379")
            self.assertEqual(remote["falkordb_password"], "secret")
            self.assertTrue(remote["falkordb_ssl"])

    # ---------------------------------------------------------------------
    # Remote with both URLs empty must be rejected
    # ---------------------------------------------------------------------
    def test_remote_without_any_url_is_rejected_at_validation_layer(self):
        """Phase-04 wizard always fills defaults, so both-empty can't happen via
        the prompt. But validate_backend_config must still reject hand-edited
        configs with neither URL — this guards the runtime contract."""
        with self.assertRaises(ValueError) as ctx:
            validate_backend_config("remote", {"qdrant_url": "", "falkordb_uri": ""})
        self.assertIn("at least", str(ctx.exception))

        # And via the wizard: blank Enter fills localhost defaults (no error).
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = ["BAD", "", "remote", "", ""] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))
            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["remote"]["qdrant_url"], "http://localhost:6333")
            self.assertEqual(cfg["remote"]["falkordb_uri"], "localhost:6379")

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
                "http://qdrant.local:6333",   # qdrant_url (non-localhost)
                "redis://falkor.local:6379",   # falkordb_uri (non-localhost)
                qkey,                          # qdrant_api_key
                fpass,                         # falkordb_password
                "n",                           # falkordb_ssl
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
                "http://qdrant.local:6333",   # qdrant_url (non-localhost)
                "redis://falkor.local:6379",   # falkordb_uri (non-localhost)
                "",                            # qdrant_api_key
                "",                            # falkordb_password
                "n",                           # falkordb_ssl
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


    # =================================================================
    # Phase-04: Remote defaults UX — local Docker endpoints
    # =================================================================

    def test_blank_input_gets_local_docker_defaults(self):
        """Fresh remote with all-Enter → localhost Docker endpoints, no creds."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "DOCKER_PROJ",  # project code
                "",              # project name
                "remote",        # storage backend
                "",              # qdrant_url → default http://localhost:6333
                "",              # falkordb_uri → default localhost:6379
                # credential prompts should be SKIPPED for localhost
            ] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            # Must not prompt for credentials when endpoints are localhost
            self.assertNotIn("API key", result.output)
            self.assertNotIn("password", result.output)
            self.assertNotIn("TLS", result.output)
            # Must show local Docker hint
            self.assertIn("local Docker", result.output)
            self.assertIn("infra-up", result.output)

            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["storage_backend"], "remote")
            remote = cfg["remote"]
            self.assertEqual(remote["qdrant_url"], "http://localhost:6333")
            self.assertEqual(remote["falkordb_uri"], "localhost:6379")
            self.assertNotIn("qdrant_api_key", remote)
            self.assertNotIn("falkordb_password", remote)
            self.assertFalse(remote["falkordb_ssl"])

    def test_blank_input_with_env_port_override(self):
        """Env QDRANT_HTTP_PORT / FALKORDB_PORT override the default ports."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "ENV_PORT",  # project code
                "",          # project name
                "remote",    # storage backend
                "",          # qdrant_url → default http://localhost:16333
                "",          # falkordb_uri → default localhost:16379
            ] + self._LOCAL_TAIL
            with patch.dict("os.environ", {"QDRANT_HTTP_PORT": "16333", "FALKORDB_PORT": "16379"}):
                result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            remote = cfg["remote"]
            self.assertEqual(remote["qdrant_url"], "http://localhost:16333")
            self.assertEqual(remote["falkordb_uri"], "localhost:16379")

    def test_reinit_remote_preserves_existing_urls_not_defaults(self):
        """Re-init of existing remote project keeps prior URLs, not 6333/6379."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            existing = {
                "active": True,
                "project": {"code": "SHOP", "name": "Shop"},
                "storage_backend": "remote",
                "remote": {
                    "qdrant_url": "http://qdrant.prev:6333",
                    "falkordb_uri": "redis://falkor.prev:6379",
                    "falkordb_ssl": True,
                },
                "code": {"env": {}, "source": {"projects": []}},
                "doc":  {"env": {}, "source": {"projects": []}},
            }
            self._write_existing_config(project_path, "dev", existing)

            # All-Enter: every prompt accepts its prior default
            result = runner.invoke(cli, ["init", str(project_path)], input="\n" * 60)

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            # Must keep the EXISTING URLs, not override to localhost defaults
            self.assertEqual(cfg["remote"]["qdrant_url"], "http://qdrant.prev:6333")
            self.assertEqual(cfg["remote"]["falkordb_uri"], "redis://falkor.prev:6379")
            self.assertTrue(cfg["remote"]["falkordb_ssl"])

    def test_non_localhost_url_still_prompts_credentials(self):
        """Entering a non-localhost URL triggers credential prompts as before."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "REMOTE_REAL",  # project code
                "",             # project name
                "remote",       # storage backend
                "https://q.example.io:6333",  # non-localhost qdrant
                "localhost:6379",              # localhost falkordb
                "my-api-key",   # qdrant_api_key (prompted because qdrant is non-local)
                "my-fpass",     # falkordb_password (prompted)
                "y",            # falkordb_ssl
            ] + self._LOCAL_TAIL
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            # Credentials must be prompted
            self.assertIn("API key", result.output)
            self.assertIn("password", result.output)
            self.assertIn("TLS", result.output)
            # Post-save hint must NOT say "local Docker defaults" (mixed endpoints)
            self.assertNotIn("local Docker defaults", result.output)
            # Instead shows the generic remote connectivity hint
            self.assertIn("dev doctor", result.output)

            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["remote"]["qdrant_url"], "https://q.example.io:6333")
            self.assertEqual(cfg["remote"]["qdrant_api_key"], "my-api-key")
            self.assertEqual(cfg["remote"]["falkordb_uri"], "localhost:6379")
            self.assertEqual(cfg["remote"]["falkordb_password"], "my-fpass")
            self.assertTrue(cfg["remote"]["falkordb_ssl"])

    def test_validate_backend_config_unchanged(self):
        """Phase-04 does not alter validate_backend_config behaviour."""
        # Local mode: no remote section needed
        mode, parsed = validate_backend_config("local", None)
        self.assertEqual(mode.value, "local")
        self.assertIsNone(parsed)

        # Remote with both URLs empty: must reject
        with self.assertRaises(ValueError):
            validate_backend_config("remote", {"qdrant_url": "", "falkordb_uri": ""})

        # Remote with one URL: accepted
        mode, parsed = validate_backend_config("remote", {"qdrant_url": "http://localhost:6333"})
        self.assertEqual(mode.value, "remote")
        self.assertEqual(parsed.qdrant_url, "http://localhost:6333")


if __name__ == "__main__":
    unittest.main()
