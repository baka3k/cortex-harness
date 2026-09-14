"""Phase 14 flip-defaults regression test cho `_rust_analyzer_binary`.

Contract (phase 14 scope B của plans/260913-2130-rust-full-migration):
- `CORTEX_RUST_ANALYZER` UNSET + binary Rust đã build → binary Rust được chọn
  (auto-flip mặc định).
- `CORTEX_RUST_ANALYZER=python` → cờ rollback: luôn Python script path.
- `=rust` giữ nghĩa cũ; binary vắng → fallback Python.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "code-tiny" / "tools" / "sync" / "incremental_sync.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("incremental_sync", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("incremental_sync", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sync_module():
    return _load_module()


class _Analyzer:
    """Đủ mặt `AnalyzerConfig` mà `_rust_analyzer_binary`/_build_analyzer_cmd dùng."""

    def __init__(self, parser, script_path="/fake/analyze.py"):
        self.parser = parser
        self.script_path = script_path
        self.extra_args = []


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CORTEX_RUST_ANALYZER", raising=False)
    monkeypatch.delenv("CORTEX_RUST_ANALYZER_BIN_DIR", raising=False)


def _release_binary(sync_module, parser):
    repo_root = Path(sync_module._ROOT_DIR).parent
    return repo_root / "rust" / "target" / "release" / sync_module._RUST_ANALYZER_BINARIES[parser]


def test_unset_with_built_binary_selects_rust(sync_module):
    binary = _release_binary(sync_module, "python")
    if not binary.is_file():
        pytest.skip("analyzer-python chưa build (cargo build --release)")
    resolved = sync_module._rust_analyzer_binary(_Analyzer("python"))
    assert resolved == str(binary)


def test_unset_without_binary_falls_back_to_python(sync_module, monkeypatch):
    monkeypatch.setenv("CORTEX_RUST_ANALYZER_BIN_DIR", "/nonexistent/bin")
    assert sync_module._rust_analyzer_binary(_Analyzer("python")) is None


def test_python_env_forces_python_backend(sync_module):
    binary = _release_binary(sync_module, "python")
    if not binary.is_file():
        pytest.skip("analyzer-python chưa build (cargo build --release)")
    # Binary tồn tại nhưng =python vẫn ép Python backend (rollback flag).
    os.environ["CORTEX_RUST_ANALYZER"] = "python"
    assert sync_module._rust_analyzer_binary(_Analyzer("python")) is None
    os.environ.pop("CORTEX_RUST_ANALYZER")


def test_rust_env_keeps_old_semantics(sync_module):
    binary = _release_binary(sync_module, "python")
    if not binary.is_file():
        pytest.skip("analyzer-python chưa build (cargo build --release)")
    os.environ["CORTEX_RUST_ANALYZER"] = "rust"
    resolved = sync_module._rust_analyzer_binary(_Analyzer("python"))
    assert resolved == str(binary)
    os.environ.pop("CORTEX_RUST_ANALYZER")


def test_unported_parser_never_swaps(sync_module):
    assert sync_module._rust_analyzer_binary(_Analyzer("no_such_parser")) is None


def test_build_analyzer_cmd_heads(sync_module):
    binary = _release_binary(sync_module, "python")
    if not binary.is_file():
        pytest.skip("analyzer-python chưa build (cargo build --release)")

    def build():
        return sync_module._build_analyzer_cmd(
            python_bin="/venv/bin/python",
            analyzer=_Analyzer("python"),
            root="/repo",
            project_id="p",
            project_name="P",
            before_sha="aa",
            after_sha="bb",
            changed_manifest=None,
            deleted_manifest=None,
            qdrant_collection=None,
            message_scan_enabled=False,
            message_output_dir=None,
            message_qdrant_collection=None,
            incremental=False,
            verbose=False,
        )

    # Auto-flip (unset): head là binary Rust, flags giữ nguyên.
    cmd = build()
    assert cmd[0] == str(binary)
    assert cmd[1:3] == ["--root", "/repo"]

    # Rollback (=python): head là python + script path.
    os.environ["CORTEX_RUST_ANALYZER"] = "python"
    cmd_python = build()
    assert cmd_python[0] == "/venv/bin/python"
    assert cmd_python[1].endswith(".py")
    assert cmd_python[2:4] == ["--root", "/repo"]
    os.environ.pop("CORTEX_RUST_ANALYZER")
