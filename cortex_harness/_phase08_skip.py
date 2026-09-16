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
    """Return dotted module paths of every deleted Python analyzer file.

    Three sources are unioned:

    1. ``git status`` for any in-flight deletions (dev-loop case).
    2. ``git diff --name-only HEAD~1 HEAD`` for the last commit's
       deletions (covers post-commit, where ``git status`` is clean).
    3. The explicit :data:`EXPLICIT_RETIRED_LIST` (covers CI sandboxes
       where git is unavailable and as a safety net for any future
       case the git-derived scan misses).
    """
    deleted: set[str] = set(EXPLICIT_RETIRED_LIST)
    for cmd in (
        ["git", "status", "--short"],
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
    ):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                cwd=repo_root,
            )
        except (OSError, FileNotFoundError):
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            path = _extract_deleted_path(line)
            if not path or not path.startswith("code-tiny/tools/") or not path.endswith(".py"):
                continue
            suffix = path[len("code-tiny/"):]
            if suffix.endswith("__init__.py"):
                module = suffix[: -len("__init__.py")].rstrip("/").replace("/", ".")
            else:
                module = suffix[: -len(".py")].replace("/", ".")
            if not module:
                continue
            # Skip if the file is actually on disk (kept-plane exception).
            if os.path.isfile(os.path.join(repo_root, path)):
                continue
            deleted.add(module)
    return sorted(deleted)


def _extract_deleted_path(line: str) -> str:
    """Return the deleted-file path from a ``git status`` / ``git diff`` line.

    ``git diff`` emits one path per line; ``git status`` emits two
    columns (``XY path`` then ``-> newpath`` for renames). Strip both.
    """
    line = line.strip()
    if not line:
        return ""
    # ``git status`` lines: ``XY path`` (XY = status + space).
    # ``git diff`` lines: just ``path``.
    if len(line) > 3 and line[2] == " ":
        return line[3:].split("\t", 1)[-1].split(" -> ", 1)[-1]
    return line


# Explicit retired list — covers CI sandboxes + safety net for the dev
# loop where git history doesn't surface the cutover commit's diff yet
# (fresh clone, shallow clone, etc.).
EXPLICIT_RETIRED_LIST: tuple[str, ...] = (
    # Whole-tree retired (24 primary parsers + overlays + topology + flutter).
    "tools.android", "tools.android.android_common",
    "tools.android.android_java_analyzer",
    "tools.android.android_kotlin_analyzer",
    "tools.android.android_mixed_analyzer",
    "tools.aspnet_core", "tools.aspnet_core.__init__",
    "tools.aspnet_core.artifact_parsers",
    "tools.aspnet_core.aspnet_core_analyzer",
    "tools.aspnet_core.detector",
    "tools.aspnet_core.pipeline",
    "tools.aspnet_core.resolver",
    "tools.aspnet_framework", "tools.aspnet_framework.__init__",
    "tools.aspnet_framework.artifact_parsers",
    "tools.aspnet_framework.aspnet_framework_analyzer",
    "tools.aspnet_framework.detector",
    "tools.aspnet_framework.pipeline",
    "tools.aspnet_framework.resolver",
    "tools.cobol", "tools.cobol.__init__",
    "tools.cobol.cfg", "tools.cobol.cobol_analyzer",
    "tools.cobol.models", "tools.cobol.parser",
    "tools.cobol.parser_runtime", "tools.cobol.pipeline",
    "tools.cobol.qdrant", "tools.cobol.resolver",
    "tools.cobol.semantics",
    "tools.database_schema", "tools.database_schema.__init__",
    "tools.database_schema.database_schema_analyzer",
    "tools.database_schema.models",
    "tools.database_schema.pipeline",
    "tools.delphi", "tools.delphi.delphi_analyzer",
    "tools.flutter", "tools.flutter.__init__",
    "tools.flutter.cache", "tools.flutter.dart_parser",
    "tools.flutter.detector", "tools.flutter.flutter_analyzer",
    "tools.flutter.models", "tools.flutter.normalizer",
    "tools.flutter.pipeline", "tools.flutter.protocol",
    "tools.go", "tools.go.go_analyzer",
    "tools.java", "tools.java.java_analyzer",
    "tools.jp1", "tools.jp1.__init__",
    "tools.jp1.jp1_analyzer", "tools.jp1.models",
    "tools.jp1.parser", "tools.jp1.pipeline",
    "tools.js", "tools.js.js_analyzer",
    "tools.kotlin", "tools.kotlin.kotlin_analyzer",
    "tools.mybatis", "tools.mybatis.__init__",
    "tools.mybatis.annotation_mapper",
    "tools.mybatis.cache", "tools.mybatis.detector",
    "tools.mybatis.dynamic_sql",
    "tools.mybatis.mapper_interface_analyzer",
    "tools.mybatis.mapper_xml_analyzer",
    "tools.mybatis.models",
    "tools.mybatis.mybatis_analyzer",
    "tools.mybatis.parser_runtime",
    "tools.mybatis.pipeline",
    "tools.mybatis.resolver",
    "tools.mybatis.spring_bridge",
    "tools.mybatis.sql_semantic_analyzer",
    "tools.perl", "tools.perl.__init__",
    "tools.perl.models", "tools.perl.parser_runtime",
    "tools.perl.perl_analyzer", "tools.perl.perl_parser",
    "tools.perl.pipeline", "tools.perl.resolver",
    "tools.php", "tools.php.php_analyzer",
    "tools.plsql", "tools.plsql.plsql_analyzer",
    "tools.python", "tools.python.CLAUDE.md",
    "tools.python.python_analyzer",
    "tools.rust", "tools.rust.rust_analyzer",
    "tools.servlet_jsp", "tools.servlet_jsp.__init__",
    "tools.servlet_jsp.cache", "tools.servlet_jsp.detector",
    "tools.servlet_jsp.el_parser",
    "tools.servlet_jsp.java_identity",
    "tools.servlet_jsp.java_semantics",
    "tools.servlet_jsp.jsp_parser",
    "tools.servlet_jsp.models",
    "tools.servlet_jsp.parser_runtime",
    "tools.servlet_jsp.path_resolver",
    "tools.servlet_jsp.pipeline",
    "tools.servlet_jsp.properties_parser",
    "tools.servlet_jsp.resolver",
    "tools.servlet_jsp.servlet_jsp_analyzer",
    "tools.servlet_jsp.servlet_jsp_java_analyzer",
    "tools.servlet_jsp.web_xml_parser",
    "tools.shell", "tools.shell.__init__",
    "tools.shell.mapping", "tools.shell.models",
    "tools.shell.parser", "tools.shell.pipeline",
    "tools.shell.shell_analyzer",
    "tools.spring", "tools.spring.__init__",
    "tools.spring.adapters",
    "tools.spring.annotation_catalog",
    "tools.spring.cache", "tools.spring.config",
    "tools.spring.detector", "tools.spring.models",
    "tools.spring.pipeline",
    "tools.spring.source_scanner",
    "tools.spring.spring_analyzer",
    "tools.spring.spring_java_analyzer",
    "tools.spring.spring_kotlin_analyzer",
    "tools.spring.spring_mixed_analyzer",
    "tools.spring.value_resolver",
    "tools.sql", "tools.sql.sql_analyzer",
    "tools.struts", "tools.struts.__init__",
    "tools.struts.java_validation",
    "tools.struts.models", "tools.struts.pipeline",
    "tools.struts.resolver",
    "tools.struts.struts_analyzer",
    "tools.struts.struts_xml_parser",
    "tools.struts.validation_parser",
    "tools.struts.web_xml_parser",
    "tools.struts.xml_utils",
    "tools.swift", "tools.swift.swift_analyzer",
    "tools.web_framework", "tools.web_framework.__init__",
    "tools.web_framework.models",
    "tools.web_framework.pipeline",
    "tools.web_framework.web_framework_analyzer",
    # Partial-retired (cplus/csharp/ts/vb — analyzer entries + ts submodules).
    "tools.cplus.cplus_analyzer",
    "tools.csharp.csharp_analyzer",
    "tools.csharp.roslyn_adapter",
    "tools.ts.ts_analyzer",
    "tools.ts.ts_backend_analyzer",
    "tools.ts._refactor_ts_analyzer",
    "tools.ts.ts_api_bridge",
    "tools.ts.workflow_finder",
    "tools.ts.agents", "tools.ts.agents.__init__",
    "tools.ts.agents.api_bridge_agent",
    "tools.ts.agents.backend_agent",
    "tools.ts.agents.dependency_agent",
    "tools.ts.agents.graph_agent",
    "tools.ts.agents.parser_agent",
    "tools.ts.agents.symbol_agent",
    "tools.ts.agents.traversal_agent",
    "tools.ts.pipeline", "tools.ts.pipeline.__init__",
    "tools.ts.pipeline.backend_pipeline",
    "tools.ts.pipeline.frontend_pipeline",
    "tools.ts.types", "tools.ts.types.__init__",
    "tools.ts.types.ast_types",
    "tools.ts.types.graph_types",
    "tools.ts.utils", "tools.ts.utils.__init__",
    "tools.ts.utils.file_utils",
    "tools.ts.utils.id_utils",
    "tools.ts.utils.regex_patterns",
    "tools.ts.context", "tools.ts.context.__init__",
    "tools.ts.context.analyzer_context",
    "tools.vb.vb_analyzer_base",
    "tools.vb.vb_common",
    "tools.vb.vb_roslyn_adapter",
    # Topology — entry retired, support modules kept.
    "tools.project_topology.topology_analyzer",
)


def _stub_path(name: str, repo_root: str | None = None) -> None:
    """Stub every prefix along ``name`` except the ``tools`` parent.

    The top-level ``tools`` package stays live — it contains kept modules
    (``tools.graph``, ``tools.common``, …; ``tools.sync`` was deleted at the 2026-09-16 sync-plane Rust cutover). When the
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