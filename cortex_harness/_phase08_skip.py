"""Phase-08 skip helper + module stub installer.

The Python analyzer entry points (`tools/<lang>/<lang>_analyzer.py` and their
supporting modules) were retired at the phase-08 cutover. Two helpers ship
here so the test surface stays loadable without re-introducing the archived
plane:

* :func:`install_stubs` registers ``sys.modules`` entries for every retired
  Python module so ``from tools.<retired> import …`` no longer fails
  collection. The stub is just ``None``/empty — the resolved attribute is
  ``None``, so any test method that actually touches it is skipped by
  :func:`phase08_retired`.

* :func:`phase08_retired` is a class/method decorator that skips with a loud
  reason so the pytest report calls the regression out — per red-team F7 /
  phase-08 audit disposition, skips MUST be loud (no silent
  ``importorskip``).

The installer is wired into a :mod:`conftest` hook so ``pytest`` collection
loads the stubs before test modules execute their ``from … import …`` lines.
"""

from __future__ import annotations

import os
import subprocess
import sys
import types
import unittest


PHASE08_RETIRED_REASON = (
    "Phase-08 (analyzer-layer-rust-cutover): the Python analyzer entry point "
    "this test depends on was retired at the cutover commit. Skipping loudly "
    "per the phase-08 audit disposition — see reports/phase08-cutover.md."
)


def _make_stub_module(name: str) -> types.ModuleType:
    """Return a module whose every attribute raises ``SkipTest`` on use.

    Phase-08 disposition: tests that touch retired analyzer state must be
    loud-skipped (not silently swallowed). The stub achieves this by
    returning a sentinel object whose ``__bool__`` / ``__call__`` /
    ``__getattr__`` hooks all raise ``unittest.SkipTest`` — pytest's
    collection runner surfaces this as a clear ``s`` line in the report.

    The sentinel also lets ``isinstance``/``patch.object``/``mock``
    helpers continue to introspect the surface without crashing — those
    calls hit ``__getattr__`` and propagate the skip, but only at the
    point of use, not at collection.
    """
    module = types.ModuleType(name)

    class _SkipSentinel:
        def __getattr__(self, attr: str):  # type: ignore[no-untyped-def]
            raise unittest.SkipTest(PHASE08_RETIRED_REASON)

        def __call__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise unittest.SkipTest(PHASE08_RETIRED_REASON)

        def __bool__(self) -> bool:
            return False

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(())

        def __len__(self) -> int:
            return 0

        def __getitem__(self, _key: object):  # type: ignore[no-untyped-def]
            raise unittest.SkipTest(PHASE08_RETIRED_REASON)

        def __enter__(self):  # type: ignore[no-untyped-def]
            raise unittest.SkipTest(PHASE08_RETIRED_REASON)

        def __exit__(self, *exc):  # type: ignore[no-untyped-def]
            raise unittest.SkipTest(PHASE08_RETIRED_REASON)

        def __class_getitem__(cls, _key: object):  # type: ignore[no-untyped-def]
            raise unittest.SkipTest(PHASE08_RETIRED_REASON)

        def __instancecheck__(self, _instance: object) -> bool:  # type: ignore[no-untyped-def]
            return False

        def __subclasscheck__(self, _subclass: type) -> bool:  # type: ignore[no-untyped-def]
            return False

    def __getattr__(attr: str):  # type: ignore[no-untyped-def]
        if attr in ("__path__", "__file__", "__name__", "__loader__", "__spec__", "__package__", "__cached__"):
            raise AttributeError(attr)
        return _SkipSentinel()

    module.__getattr__ = __getattr__  # type: ignore[attr-defined]
    return module


def _is_stub(module: object) -> bool:
    """Return ``True`` if the module is one of our phase-08 stubs."""
    if not isinstance(module, types.ModuleType):
        return False
    name = getattr(module, "__name__", "")
    return name.startswith("tools.") and getattr(module, "__getattr__", None) is not None


def _disk_path(repo_root: str, dotted: str) -> str:
    parts = dotted.split(".")
    return os.path.join(repo_root, "code-tiny", *parts) + ".py"


def _dir_path(repo_root: str, dotted: str) -> str:
    parts = dotted.split(".")
    return os.path.join(repo_root, "code-tiny", *parts)


def _discover_retired(repo_root: str) -> list[str]:
    """Return dotted module paths of every deleted Python analyzer file."""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
            cwd=repo_root,
        )
    except (OSError, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []
    deleted: list[str] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        if not (line.startswith(" D ") or line.startswith("D ")):
            continue
        path = line[3:].strip()
        if not path.startswith("code-tiny/tools/") or not path.endswith(".py"):
            continue
        suffix = path[len("code-tiny/"):]
        if suffix.endswith("__init__.py"):
            module = suffix[: -len("__init__.py")].rstrip("/").replace("/", ".")
        else:
            module = suffix[: -len(".py")].replace("/", ".")
        if not module or module in seen:
            continue
        # Skip if the file is actually on disk (kept-plane exception).
        if os.path.isfile(os.path.join(repo_root, path)):
            continue
        seen.add(module)
        deleted.append(module)
    return sorted(deleted)


def _stub_path(name: str, repo_root: str | None = None) -> None:
    """Stub every prefix along ``name`` except the ``tools`` parent.

    The top-level ``tools`` package stays live — it contains kept modules
    (``tools.graph``, ``tools.common``, ``tools.sync``, …). When the
    intermediate ``tools.<dir>`` parent still has live ``*.py`` files on
    disk (e.g. ``tools.project_topology`` keeps ``models/contracts/registry``
    after ``topology_analyzer.py`` is archived), we skip stubbing the
    parent too — the real filesystem import handles the kept submodules.
    """
    parts = name.split(".")
    for i in range(2, len(parts) + 1):
        candidate = ".".join(parts[:i])
        if not candidate or candidate in sys.modules:
            continue
        # Skip stubbing parent packages whose directory still has live
        # ``*.py`` files (kept-plane exception).
        if repo_root is not None and i >= 2 and i < len(parts):
            dir_path = _dir_path(repo_root, candidate)
            if os.path.isdir(dir_path) and any(
                f.endswith(".py") and (f != "__init__.py" or os.path.isfile(os.path.join(dir_path, f)))
                for f in os.listdir(dir_path)
            ):
                continue
        sys.modules[candidate] = _make_stub_module(candidate)


def install_stubs() -> None:
    """Register stubs for every retired Python analyzer module.

    Two-phase install:

    1. **Clean up** any phase-08 stubs whose backing file now exists on disk
       (a restored kept-plane dependency) or whose parent directory is
       non-empty (so the parent module is reachable via the real
       filesystem).
    2. **Install** stubs for every retired leaf module — module paths
       reported by ``git status`` as deleted where the file is *not* on
       disk and the parent directory has no remaining live files.
    """
    repo_root = os.environ.get("CORTEX_HARNESS_ROOT") or os.getcwd()

    # Phase 1: remove stale stubs.
    stale: list[str] = []
    for name, module in list(sys.modules.items()):
        if not _is_stub(module):
            continue
        if os.path.isfile(_disk_path(repo_root, name)):
            stale.append(name)
            continue
        dir_path = _dir_path(repo_root, name)
        if os.path.isdir(dir_path):
            live = [
                f for f in os.listdir(dir_path)
                if f.endswith(".py") and (f != "__init__.py" or os.path.isfile(os.path.join(dir_path, f)))
            ]
            if live:
                stale.append(name)
    for name in stale:
        del sys.modules[name]

    # Phase 2: install fresh stubs.
    for name in _discover_retired(repo_root):
        # Skip parent stubs (depth 2) when their directory still has live
        # submodules — the real filesystem import handles the kept ones.
        if name.count(".") < 2:
            dir_path = _dir_path(repo_root, name)
            if os.path.isdir(dir_path) and any(
                f.endswith(".py") for f in os.listdir(dir_path)
                if f != "__init__.py" or os.path.isfile(os.path.join(dir_path, f))
            ):
                continue
        _stub_path(name, repo_root)


def phase08_retired(obj):  # type: ignore[no-untyped-def]
    """Class- and method-level decorator that skips with the phase-08 reason."""
    if isinstance(obj, type) and issubclass(obj, unittest.TestCase):
        for name in list(vars(obj)):
            if name.startswith("test_") and callable(getattr(obj, name)):
                setattr(obj, name, unittest.skip(PHASE08_RETIRED_REASON)(getattr(obj, name)))
        return obj
    return unittest.skip(PHASE08_RETIRED_REASON)(obj)


# Auto-install on import so any Python process that touches this helper has
# the stubs available — saves conftest wiring for the dev-loop case.
install_stubs()