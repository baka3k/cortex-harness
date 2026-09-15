"""Phase-07 CI bridge-ban: the Rust dev CLI must not regain a Python bridge.

Fails the build when any of the retired bridge constructs reappear under
`rust/crates/cortex-dev/src/`:
  - the bridge module name (pyexec),
  - its interpreter helper name (renamed to `util::harness_python`),
  - the embedded Python helper source constant.
Whitelisted strings: none — comments included, so the history stays in the
git log / plan reports instead of the source tree.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEV_SRC = REPO_ROOT / "rust" / "crates" / "cortex-dev" / "src"

BANNED_TOKENS = ("pyexec", "venv_python", "HELPER_SRC")


class RustBridgeBanTests(unittest.TestCase):
    def test_no_bridge_constructs_in_cortex_dev_sources(self):
        offenders: list[str] = []
        for path in sorted(DEV_SRC.rglob("*.rs")):
            text = path.read_text(encoding="utf-8")
            for token in BANNED_TOKENS:
                if token in text:
                    line_no = next(
                        i
                        for i, line in enumerate(text.splitlines(), 1)
                        if token in line
                    )
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {token}")
        self.assertEqual(
            offenders,
            [],
            "Python-bridge constructs reintroduced into cortex-dev:\n"
            + "\n".join(offenders),
        )

    def test_no_entrypoint_references_dev_py(self):
        entrypoints = (
            "dev.sh",
            "dev.bat",
            "dev.ps1",
            "dev-global.cmd",
            "installers/windows/scripts/wrapper.bat",
            "Makefile",
        )
        for name in entrypoints:
            with self.subTest(entrypoint=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                self.assertNotIn("dev.py", text)

    def test_lifecycle_python_scripts_are_archived_or_shim_only(self):
        """`mcp-lifecycle.ps1` is deleted (phase-07); `ensure_ort.py` is
        archived (native `dev ensure-ort` replaced it). The POSIX lifecycle
        script stays ONLY while the shim actions (doctor/start/stop/infra-*)
        have no native port."""
        self.assertFalse(
            (REPO_ROOT / "scripts" / "mcp-lifecycle.ps1").exists(),
            "mcp-lifecycle.ps1 must stay deleted",
        )
        self.assertFalse(
            (REPO_ROOT / "scripts" / "ensure_ort.py").exists(),
            "ensure_ort.py must live under scripts/archived/ (native ensure-ort)",
        )
        self.assertTrue(
            (REPO_ROOT / "scripts" / "archived" / "ensure_ort.py").is_file()
        )


if __name__ == "__main__":
    unittest.main()
