"""Shared directory/file ignore patterns for source scanners.

Centralises the set of environment / build / cache directories that every
analyzer should skip while walking a project tree.  Analyzers that have their
own narrower ignore list (e.g. ``flutter.dart_parser.SKIPPED_DIRECTORIES``)
should *extend* :data:`COMMON_SCAN_EXCLUDE` rather than redeclare the list so
that adding a new environment directory here propagates everywhere.

Why a single source of truth
----------------------------
Without this module, every analyzer reimplemented its own ad-hoc filter
(``.dart_tool``, ``.git``, ``build``, ``.venv``) and missed cases.  ``.venv``
in particular is a Python virtual environment that can sit at the project
root next to source code; scanning it produces noise (and sometimes fake
matches for unrelated languages).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


# Directories that must never be scanned by any source analyzer.
# Mirrors ``cortex_harness/dev.py::_SCAN_EXCLUDE`` so behaviour is identical
# between the orchestrator CLI and the underlying analyzer subprocesses.
COMMON_SCAN_EXCLUDE: frozenset[str] = frozenset(
    {
   # Version control
        ".git",
        ".hg",
        ".svn",

        # Python
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".pytype",
        ".pyre",
        ".hypothesis",
        ".ipynb_checkpoints",
        ".eggs",
        "htmlcov",

        # JavaScript / TypeScript / frontend frameworks
        "node_modules",
        "bower_components",
        ".next",
        ".nuxt",
        ".output",
        ".svelte-kit",
        ".astro",
        ".parcel-cache",
        ".turbo",
        ".nx",
        ".vite",
        ".pnpm-store",
        ".npm",

        # Java / Kotlin / Scala / Android
        ".gradle",
        ".kotlin",
        ".bloop",
        ".metals",
        ".bsp",
        ".scala-build",

        # .NET
        ".vs",
        "TestResults",
        "BenchmarkDotNet.Artifacts",

        # Rust
        # "target" already covered below

        # C / C++ / CMake / Meson / Bazel / Conan
        "CMakeFiles",
        "_deps",
        ".ccache",
        ".conan",
        "meson-private",
        "meson-logs",
        "meson-info",

        # Apple / Swift / Objective-C
        "Pods",
        "DerivedData",
        "xcuserdata",

        # Dart / Flutter
        ".dart_tool",
        ".pub-cache",
        ".flutter-plugins",
        ".flutter-plugins-dependencies",

        # Ruby
        ".bundle",
        ".yardoc",
        "_yardoc",

        # Elixir / Erlang / OCaml
        "_build",
        ".elixir_ls",
        ".lexical",
        ".opam-switch",

        # Haskell
        ".stack-work",
        "dist-newstyle",

        # Infrastructure / cloud tooling
        ".terraform",
        ".terragrunt-cache",
        ".serverless",
        ".aws-sam",
        "cdk.out",

        # Generic build output
        "build",
        "out",
        "target",
        "dist",
        "bin",
        "obj",
        "artifacts",

        # Dependencies
        "vendor",

        # Test / coverage output
        "coverage",
        ".nyc_output",
        "lcov-report",
        "playwright-report",
        "test-results",
        "allure-results",
        "allure-report",

        # IDE and local tooling
        ".idea",
        ".vscode",
        ".fleet",
        ".cache",
        ".scannerwork",
        ".cortext-harness",
    }
)


def is_excluded_dir(name: str) -> bool:
    """Return ``True`` when *name* should be skipped during a directory walk.

    Accepts both exact matches (``".venv"``) and simple glob patterns
    (``"*.egg-info"``) so analyzers can extend the set without rewriting the
    matching logic.
    """
    if name in COMMON_SCAN_EXCLUDE:
        return True
    # Light glob support for entries like ``*.egg-info`` and ``*.lock``.
    if any(_matches_pattern(name, pattern) for pattern in COMMON_SCAN_EXCLUDE):
        return True
    return False


def _matches_pattern(name: str, pattern: str) -> bool:
    if "*" not in pattern and "?" not in pattern and "[" not in pattern:
        return False
    import fnmatch

    return fnmatch.fnmatch(name, pattern)


def has_excluded_parent(
    candidate: Path | str,
    *,
    root: Path | str,
) -> bool:
    """True if any directory part of *candidate* (relative to *root*) is excluded.

    Skips the final component of the path — calling code decides whether the
    leaf itself is excluded (e.g. via a sensitive-file matcher).
    """
    candidate_path = Path(candidate)
    try:
        relative = candidate_path.relative_to(root)
    except ValueError:
        return False
    parents = relative.parts[:-1] if relative.is_file() or relative.suffix else relative.parts
    return any(is_excluded_dir(part) for part in parents)


def filter_paths(
    paths: Iterable[Path],
    *,
    root: Path,
) -> list[Path]:
    """Return the subset of *paths* that do not live inside an excluded dir."""
    root_path = Path(root)
    return [
        path for path in paths
        if not has_excluded_parent(path, root=root_path)
    ]
